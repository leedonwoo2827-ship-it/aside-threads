# -*- coding: utf-8 -*-
"""Codex 프로바이더 — ChatGPT 구독 OAuth. **API 키를 쓰지 않는다.**

`llm/codex_transport.py` 의 전송(토큰·헤더·SSE)을 쓴다. 그림 스튜디오가 수백 장을
구운 바로 그 길이다 — 새 길을 내지 않는다.

예전 `ClaudeProvider` 의 duck-typed 계약을 **필드 이름까지 그대로** 맞춘다. 그래야
호출부 13곳이 클래스 이름 한 단어만 바뀐다:

    model                                     # 반드시 **대입 가능한 필드**
    generate(system, messages) -> str
    stream(system, messages)   -> Iterator[str]      # ★ 증분 델타
    structured(system, messages, schema) -> dict
    vision(system, parts, schema)        -> dict     # 이미지 입력
    ping() -> (bool, str)

받되 **쓰지 않는** 인자가 셋 있다. 호출부를 안 고치려고 남겨 둔 것이다:

    cwd · allowed_tools     Claude Code 는 레포 안을 Read/Grep 으로 훑었다. codex/responses
                            직접 호출에는 툴이 없다. 원고(HTML) 프로젝트는 s2-repo 를
                            건너뛰어 `clone` 이 늘 None 이므로 단행본 흐름은 영향 없다.
                            레포→쇼케이스 모드를 되살리려면 그 스테이지에
                            `codex exec -C <clone> -s read-only` 를 붙이면 된다.
    budget_usd              구독 정액이라 콜당 비용이 없다. `last_cost_usd` 는 늘 0.0 —
                            대신 `last_usage` 에 토큰 수가 담긴다.

★ 구조화 출력은 **두 길**이다. `text.format` 의 json_schema 가 정석이지만
  codex/responses 는 비공식 엔드포인트라 받아 줄지가 확실하지 않다. 거부당하면
  스키마를 지시문에 적고 JSON 을 뽑아내는 길로 **자동으로** 떨어진다. 어느 쪽으로
  갔는지는 `last_schema_mode` 에 남는다(스모크가 이 값을 찍는다).
"""
from __future__ import annotations

import base64
import json
import time
import urllib.error
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Tuple

from .codex_transport import (
    CODEX_MODEL,
    CodexAuthError,
    CodexHTTPError,
    _build_headers,
    _detect_codex_version,
    _extract_tokens,
    _load_auth,
    refresh_once,
    _sniff_mime,
    _stream,
)
from .errors import NotAuthenticated, ProviderError, QuotaExceeded

# 이미지 확장자 → media_type. 파이프라인이 넘기는 비전 프레임은 webp 다.
MEDIA = {".webp": "image/webp", ".png": "image/png",
         ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif"}

# 추론 강도. gpt-5.5 가 받는 값은 **none · low · medium · high · xhigh** 다
# (2026-09-20 실측: 'minimal' 을 보내면 400 unsupported_value 로 되돌아온다 —
#  그건 gpt-5 계열 이름이다). Claude 의 minimal·max 를 양끝으로 접어 준다.
EFFORT = {"none": "none", "minimal": "none", "low": "low", "medium": "medium",
          "high": "high", "xhigh": "xhigh", "max": "xhigh"}

# 기본 시간 예산. 챗 한 마디가 아니라 **한 장짜리 목차·대본**이 기준이다.
# `showcase.config.json` 의 `llm` 블록이 있으면 그것이 이긴다.
DEFAULT_TOTAL_TIMEOUT = 900.0
DEFAULT_STALL_TIMEOUT = 180.0
DEFAULT_RETRIES = 2
BACKOFF_BASE = 6.0          # 초 — 일반 실패 (지수)
BACKOFF_RATE_LIMIT = 45.0   # 초 — 429/한도 초과는 길게 쉰다 (batch_jobs 와 같은 값)


# ── 설정 ───────────────────────────────────────────────────────────────────
def _cfg_llm() -> Dict[str, Any]:
    """aside 는 `aside.config.json` 의 `llm` 블록을 쓴다. 없으면 기본값."""
    try:
        from .. import config

        blk = config.load().get("llm")
        return dict(blk) if isinstance(blk, dict) else {}
    except Exception:
        return {}


# ── 스키마 (draft-07 → OpenAI strict) ──────────────────────────────────────
def _nullable(node: Dict[str, Any]) -> Dict[str, Any]:
    """필수가 아니던 칸을 null 허용으로 넓힌다.

    strict 는 `required` 에 **모든 키**를 요구한다. 원래 선택이던 칸을 그냥 필수로
    만들면 모델이 없는 값을 지어내므로, 대신 null 을 허용하고 받은 뒤에 걷어낸다.
    """
    out = dict(node)
    t = out.get("type")
    if isinstance(t, str) and t != "null":
        out["type"] = [t, "null"]
    elif isinstance(t, list) and "null" not in t:
        out["type"] = list(t) + ["null"]
    if isinstance(out.get("enum"), list) and None not in out["enum"]:
        out["enum"] = list(out["enum"]) + [None]
    return out


def _strictify(node: Any) -> Any:
    """레포 스키마는 Claude 기준 draft-07 이라 strict 규칙을 어긴다.

    - `s13c_zones.SCHEMA` 는 `additionalProperties` 가 아예 없다
    - `s5_decisions.SCHEMA` 는 `tradeoff` 가 properties 에만 있고 required 에 없다

    재귀적으로 ① 모든 object 에 `additionalProperties: false` ② `required` = 전체 키
    ③ 원래 선택이던 칸은 null 허용.
    """
    if isinstance(node, list):
        return [_strictify(x) for x in node]
    if not isinstance(node, dict):
        return node

    out = dict(node)
    props = out.get("properties")
    if isinstance(props, dict):
        req = set(out.get("required") or [])
        new_props: Dict[str, Any] = {}
        for k, v in props.items():
            sub = _strictify(v)
            new_props[k] = sub if k in req else _nullable(sub)
        out["properties"] = new_props
        out["required"] = list(new_props.keys())
        out["additionalProperties"] = False
    if "items" in out:
        out["items"] = _strictify(out["items"])
    for key in ("anyOf", "oneOf", "allOf"):
        if isinstance(out.get(key), list):
            out[key] = [_strictify(x) for x in out[key]]
    return out


def _drop_nulls(node: Any) -> Any:
    """null 을 통째로 걷어낸다 — 호출부가 `.get(k)` 로 **없는 칸**을 전제한다."""
    if isinstance(node, dict):
        return {k: _drop_nulls(v) for k, v in node.items() if v is not None}
    if isinstance(node, list):
        return [_drop_nulls(x) for x in node if x is not None]
    return node


def _extract_json(text: str) -> Optional[dict]:
    """응답에서 JSON 객체 하나를 뽑는다. 코드펜스와 앞뒤 군말을 견딘다."""
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1] if "\n" in s else s
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
        s = s.strip()
        if s.lower().startswith("json"):
            s = s[4:].lstrip()
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass
    # 첫 균형 잡힌 {...} — 문자열 안의 중괄호와 이스케이프를 센다
    start = s.find("{")
    if start < 0:
        return None
    depth, in_str, esc = 0, False, False
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(s[start:i + 1])
                    return obj if isinstance(obj, dict) else None
                except Exception:
                    return None
    return None


def _schema_note(schema: dict) -> str:
    return ("\n\n# 출력 형식 — 어기면 버려진다\n"
            "아래 JSON 스키마에 **정확히** 맞는 JSON 객체 **하나만** 출력한다.\n"
            "설명·머리말·코드펜스를 붙이지 않는다.\n\n"
            + json.dumps(schema, ensure_ascii=False, indent=2))


def _is_schema_rejection(e: Exception) -> bool:
    """`text.format` 을 못 알아들어서 난 400 인가."""
    if getattr(e, "status", None) != 400:
        return False
    low = str(e).lower()
    return any(k in low for k in ("format", "schema", "json_schema", "text.format"))


# 재시도해 봐야 같은 답이 오는 것들. 429 는 여기 없다 — 그건 기다리면 풀린다.
NO_RETRY_STATUS = (400, 403, 404)

# ★ 백엔드가 `text.format` 을 한 번 거부하면 **이 프로세스에서는 다시 시도하지
#   않는다.** 17장을 도는 동안 콜마다 400 왕복을 더 하면 그것만으로 몇 분이
#   날아간다. 프로바이더는 스테이지마다 새로 만들어지므로 모듈 전역이어야 한다.
#   (2026-09-20 실측으로는 받아 준다 — 이 깃발이 내려갈 일이 아직 없었다.)
_SCHEMA_OK = True


# ── 프로바이더 ─────────────────────────────────────────────────────────────
@dataclass
class CodexProvider:
    model: str = ""                      # 빈 값 = CODEX_MODEL
    effort: Optional[str] = None         # none | low | medium | high | xhigh
    cwd: Optional[str] = None            # 받되 쓰지 않는다 (모듈 주석 참고)
    allowed_tools: List[str] = field(default_factory=list)   # 받되 쓰지 않는다
    max_turns: int = 1                                       # 받되 쓰지 않는다
    budget_usd: float = 1.5                                  # 받되 쓰지 않는다
    # ★ 콜 하나가 2~3분씩 간다. 그동안 화면이 한 글자도 안 바뀌면 **멈춘 것으로
    #   보인다.** Claude Code 는 Read/Grep 을 흘려 줬는데 여기엔 툴이 없으므로 대신
    #   추론 요약을 흘린다.
    on_activity: Optional[Callable[[str], None]] = field(default=None, repr=False)

    total_timeout: float = 0.0           # 0 = 설정값
    stall_timeout: float = 0.0
    retries: int = -1                    # -1 = 설정값

    last_cost_usd: float = 0.0           # 구독 정액 — 늘 0.0 (호출부 호환)
    last_turns: int = 0
    last_usage: Dict[str, int] = field(default_factory=dict)
    last_schema_mode: str = ""           # json_schema | prompt | prompt-retry

    def __post_init__(self) -> None:
        cfg = _cfg_llm()
        if not self.total_timeout:
            self.total_timeout = float(cfg.get("total_timeout_sec") or DEFAULT_TOTAL_TIMEOUT)
        if not self.stall_timeout:
            self.stall_timeout = float(cfg.get("stall_timeout_sec") or DEFAULT_STALL_TIMEOUT)
        if self.retries < 0:
            self.retries = int(cfg.get("retries", DEFAULT_RETRIES))

    # ── 페이로드 ──
    def _effort(self) -> str:
        return EFFORT.get((self.effort or "").strip().lower(), "medium")

    def _payload(self, instructions: str, content: List[dict], *,
                 schema: Optional[dict] = None, verbosity: str = "medium") -> dict:
        text: Dict[str, Any] = {"verbosity": verbosity}
        if schema is not None:
            text["format"] = {"type": "json_schema", "name": "result",
                              "strict": True, "schema": schema}
        model = (self.model or "").strip()
        if model in ("", "cli-default", "default") or model.startswith("claude"):
            # 설정이 아직 claude-* 를 들고 있어도 조용히 기본 모델로 간다.
            model = CODEX_MODEL
        return {
            "model": model,
            "stream": True,
            "instructions": instructions or "You are a helpful assistant.",
            "input": [{"type": "message", "role": "user", "content": content}],
            "store": False,
            "reasoning": {"effort": self._effort(), "summary": "auto"},
            "include": ["reasoning.encrypted_content"],
            "text": text,
        }

    # ── 지금 무엇을 하고 있는가 ──
    def _activity(self, buf: List[str], chunk: str) -> None:
        """추론 요약을 문장 단위로 한 줄씩 흘린다."""
        if not self.on_activity:
            return
        buf.append(chunk)
        joined = "".join(buf)
        if not ("\n" in joined or len(joined) >= 40):
            return
        line = joined.replace("\n", " ").strip()
        buf.clear()
        line = line.lstrip("#* ").strip()
        if not line:
            return
        try:
            self.on_activity(line[:60])
        except Exception:      # noqa: BLE001 — 표시용이다. 절대 본작업을 죽이지 않는다
            pass

    # ── SSE → 태그된 사건 ──
    def _sse(self, headers: dict, payload: dict,
             deadline: float) -> Iterator[Tuple[str, Any]]:
        seen: Dict[str, int] = {}
        reason_buf: List[str] = []
        try:
            for evt in _stream(headers, payload, deadline, self.stall_timeout):
                t = evt.get("type", "?")
                seen[t] = seen.get(t, 0) + 1
                if t == "response.output_text.delta":
                    if isinstance(d := evt.get("delta"), str) and d:
                        yield ("delta", d)
                elif t == "response.output_text.done":
                    if isinstance(x := evt.get("text"), str) and x:
                        yield ("final", x)
                elif t == "response.reasoning_summary_text.delta":
                    if isinstance(d := evt.get("delta"), str) and d:
                        self._activity(reason_buf, d)
                elif t == "response.output_item.done":
                    item = evt.get("item")
                    if isinstance(item, dict) and item.get("type") == "message":
                        if buf := _text_from_content(item.get("content")):
                            yield ("final", buf)
                elif t in ("response.completed", "response.done"):
                    resp = evt.get("response")
                    if isinstance(resp, dict):
                        for it in resp.get("output", []) or []:
                            if isinstance(it, dict) and it.get("type") == "message":
                                if buf := _text_from_content(it.get("content")):
                                    yield ("final", buf)
                        if isinstance(u := resp.get("usage"), dict):
                            yield ("usage", u)
                elif t in ("error", "response.failed"):
                    detail = None
                    if isinstance(evt.get("response"), dict):
                        err = evt["response"].get("error")
                        if isinstance(err, dict):
                            detail = err.get("message") or err.get("code")
                    if isinstance(evt.get("error"), dict):
                        detail = evt["error"].get("message") or detail
                    yield ("fail", str(detail or evt.get("message")
                                        or evt.get("code") or ""))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")
            raise CodexHTTPError(f"HTTP {e.code}: {body[:400]}", status=e.code)
        except (TimeoutError, ConnectionError):
            raise CodexHTTPError("백엔드 응답이 지연/중단되었습니다. 다시 시도하세요.")
        except urllib.error.URLError as e:
            raise CodexHTTPError(f"네트워크 오류: {e.reason}")
        yield ("events", ", ".join(sorted(seen)) or "(none)")

    # ── 한 번 보내기 (401 이면 토큰 갱신 후 1회 재시도) ──
    def _once(self, payload: dict) -> Iterator[Tuple[str, Any]]:
        auth = _load_auth()
        access, account_id, refresh = _extract_tokens(auth)
        if not access:
            raise CodexAuthError(
                "~/.codex/auth.json 에 ChatGPT OAuth access_token 이 없습니다. "
                "`codex login` 으로 구독 계정에 로그인하세요.")
        version = _detect_codex_version()
        deadline = time.monotonic() + self.total_timeout

        started = False
        try:
            for ev in self._sse(_build_headers(access, account_id, version),
                                payload, deadline):
                started = True
                yield ev
            return
        except CodexHTTPError as e:
            # 401 은 접속 순간에 난다 — 아직 아무것도 안 흘렸을 때만 갱신하고 다시.
            if started or e.status != 401:
                raise
        _, new_access, account_id, _ = refresh_once(access)
        yield from self._sse(_build_headers(new_access, account_id, version),
                             payload, deadline)

    # ── 재시도까지 감싼 한 콜 ──
    def _call(self, payload: dict) -> str:
        last: Exception = ProviderError("호출이 시작되지 않았습니다.")
        for attempt in range(max(0, self.retries) + 1):
            try:
                return self._collect(payload)
            except NotAuthenticated:
                raise                       # 로그인 문제는 재시도가 무의미하다
            except (QuotaExceeded, ProviderError) as e:
                last = e
                if attempt >= self.retries or getattr(e, "status", None) in NO_RETRY_STATUS:
                    break
                time.sleep(BACKOFF_RATE_LIMIT if isinstance(e, QuotaExceeded)
                           else BACKOFF_BASE * (2 ** attempt))
        raise last

    def _collect(self, payload: dict) -> str:
        """SSE 를 끝까지 읽고 최종 텍스트를 돌려준다.

        ★ 델타와 **완성 전문**이 둘 다 온다. 전문이 있으면 그것이 이긴다 —
          델타가 한 조각도 안 오는 응답이 실제로 있다(codex_text 가 같은 규칙).
        """
        chunks: List[str] = []
        final = ""
        fail = ""
        events = "(none)"
        try:
            for kind, val in self._once(payload):
                if kind == "delta":
                    chunks.append(val)
                elif kind == "final":
                    final = val
                elif kind == "usage":
                    self._record(val)
                elif kind == "fail":
                    fail = val
                elif kind == "events":
                    events = val
        except CodexHTTPError as e:
            raise self._map(e) from e

        text = (final or "".join(chunks)).strip()
        if text:
            return text
        if fail:
            raise self._map(CodexHTTPError(f"생성 실패: {fail} (events: {events})"))
        raise ProviderError(f"빈 응답을 받았습니다. (events: {events})")

    def _record(self, usage: dict) -> None:
        self.last_usage = {
            "input": int(usage.get("input_tokens") or 0),
            "output": int(usage.get("output_tokens") or 0),
            "total": int(usage.get("total_tokens") or 0),
        }
        self.last_turns = 1

    # ── 에러 타입화 ──
    @staticmethod
    def _map(e: Exception) -> Exception:
        """호출부의 `except` 와 화면 문구가 그대로 살도록 `llm.errors` 로 옮긴다.

        ★ HTTP 코드를 `.status` 로 **넘겨 준다.** 안 그러면 위에서 400(스키마 거부)과
          503(잠깐 끊김)을 구별할 길이 없어, 폴백해야 할 때 재시도만 하다 끝난다.
        """
        msg = str(e)
        low = msg.lower()
        status = getattr(e, "status", None)
        # ★ token_revoked 는 401 로 온다. 이것을 일반 실패로 두면 배치가 재시도로
        #   할당량을 태운다 — 로그인 만료는 **즉시 세워야** 한다.
        if isinstance(e, CodexAuthError) or status == 401 or "token_revoked" in low:
            out: Exception = NotAuthenticated(msg)
        elif status == 429 or any(k in low for k in
                                  ("429", "insufficient_quota", "quota", "rate limit",
                                   "too many", "usage limit", "한도")):
            out = QuotaExceeded(msg)
        else:
            out = ProviderError(msg)
        out.status = status  # type: ignore[attr-defined]
        return out

    # ── 텍스트 ──
    def generate(self, system: str, messages: List[Dict], *,
                 max_tokens: int = 0, temperature: float = 0.0) -> str:
        """max_tokens·temperature 는 백엔드가 받지 않는다(시그니처 호환용)."""
        return self._call(self._payload(
            system, [{"type": "input_text", "text": self._flatten(messages)}]))

    def stream(self, system: str, messages: List[Dict], **_) -> Iterator[str]:
        """증분 텍스트를 흘린다. 델타가 하나도 없었을 때만 전문을 폴백으로 쓴다."""
        payload = self._payload(
            system, [{"type": "input_text", "text": self._flatten(messages)}])
        n, final = 0, ""
        try:
            for kind, val in self._once(payload):
                if kind == "delta":
                    n += 1
                    yield val
                elif kind == "final":
                    final = val
                elif kind == "usage":
                    self._record(val)
        except CodexHTTPError as e:
            raise self._map(e) from e
        if n == 0 and final:
            yield final

    # ── 구조화 ──
    def structured(self, system: str, messages: List[Dict], *, schema: dict) -> dict:
        return self._structured(
            system, [{"type": "input_text", "text": self._flatten(messages)}], schema)

    def _structured(self, system: str, content: List[dict], schema: dict) -> dict:
        global _SCHEMA_OK
        note = _schema_note(schema)
        plans: List[Tuple[str, str, Optional[dict]]] = []
        if _SCHEMA_OK:
            plans.append(("json_schema", system, _strictify(schema)))
        plans.append(("prompt", system + note, None))
        # ★ 폴백으로 떨어지면 JSON 이 깨질 확률이 올라간다. 한 번 더 준다.
        plans.append(("prompt-retry",
                      system + note + "\n\n앞선 시도가 JSON 파싱에 실패했다. "
                      "**JSON 객체 하나만** 출력하라.", None))

        last: Exception = ProviderError("구조화 출력을 받지 못했습니다.")
        for mode, sys_text, fmt in plans:
            try:
                text = self._call(self._payload(sys_text, content,
                                                schema=fmt, verbosity="low"))
            except ProviderError as e:
                # `text.format` 을 못 알아들은 400 이면 폴백으로 넘어간다.
                if fmt is not None and _is_schema_rejection(e):
                    _SCHEMA_OK = False
                    last = e
                    continue
                raise
            if isinstance(obj := _extract_json(text), dict):
                self.last_schema_mode = mode
                return _drop_nulls(obj)
            last = ProviderError(f"JSON 을 뽑지 못했습니다 ({mode}): {text[:160]}")
        raise last

    # ── 비전 ──
    def vision(self, system: str, parts: Iterable[Dict[str, Any]], *,
               schema: dict, intro: str = "") -> dict:
        """`parts` = [{"text": "...", "image": Path|None}, …] 를 **한 번에** 보낸다.

        한 항목의 프레임 전부를 한 콜에 묶는다 — 장마다 부르면 왕복만 늘고 결과가
        장별로 어긋난다.
        """
        content: List[dict] = []
        if intro:
            content.append({"type": "input_text", "text": intro})
        content.extend(self._image_blocks(parts))
        return self._structured(system, content, schema)

    @staticmethod
    def _image_blocks(parts: Iterable[Dict[str, Any]]) -> List[dict]:
        out: List[dict] = []
        for p in parts:
            if txt := (p.get("text") or ""):
                out.append({"type": "input_text", "text": txt})
            img = p.get("image")
            if not img:
                continue
            path = Path(img)
            raw = path.read_bytes()
            mt = _sniff_mime(raw) or MEDIA.get(path.suffix.lower())
            if mt is None:
                raise ProviderError(f"지원하지 않는 이미지 형식: {path.name}")
            b64 = base64.b64encode(raw).decode("ascii")
            out.append({"type": "input_image", "image_url": f"data:{mt};base64,{b64}"})
        return out

    # ── 상태 ──
    def ping(self) -> Tuple[bool, str]:
        try:
            txt = self.generate("You are a connection tester.",
                                [{"role": "user", "content": "Respond with exactly: OK"}])
            tok = self.last_usage.get("total", 0)
            return True, f"OK ({tok} tok) → {txt.strip()[:40]}"
        except Exception as e:  # noqa: BLE001
            return False, f"{type(e).__name__}: {e}"

    # ── 내부 ──
    @staticmethod
    def _flatten(messages: List[Dict]) -> str:
        """백엔드는 대화 배열이 아니라 한 프롬프트를 받는다 — 역할을 표시해 접는다."""
        if len(messages) == 1 and messages[0].get("role") == "user":
            return str(messages[0].get("content") or "")
        role_ko = {"user": "사용자", "assistant": "어시스턴트", "system": "지시"}
        return "\n\n".join(
            f"[{role_ko.get(m.get('role', 'user'), m.get('role', 'user'))}]\n"
            f"{m.get('content') or ''}" for m in messages)


def _text_from_content(content: Any) -> str:
    if not isinstance(content, list):
        return ""
    buf: List[str] = []
    for p in content:
        if isinstance(p, dict) and p.get("type") in ("output_text", "text", "input_text"):
            if isinstance(p.get("text"), str):
                buf.append(p["text"])
    return "".join(buf)

# 로그인 상태(연결 칩·doctor)는 `imgstudio/services/codex_auth.py:status()` 가 답한다.
# 여기서 감싸지 않는다 — `llm/` 이 `imgstudio/` 를 import 하면 의존이 거꾸로다.
