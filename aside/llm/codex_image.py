"""이미지 엔진 — 생성 / 편집 / 병합.

주 엔진 = **codex (키리스)**: `~/.codex/auth.json` 의 ChatGPT OAuth 토큰으로
`https://chatgpt.com/backend-api/codex/responses` 에 직접 POST 한다. 요청에 싣는 모델은
**추론 모델(gpt-5.5)**이고, 그림은 그 안의 `image_generation` 툴이 만든다. 툴이 쓰는
이미지 모델은 **gpt-image-2** — 응답에는 이름이 안 실리지만 OpenAI 문서에 이미지 생성
모델이 그것뿐이고 동작(16의 배수·1:3~3:1·최대 4K)도 일치한다.
API 키가 필요 없고 사용량은 사용자의 ChatGPT 구독 한도에서 차감된다.

> 주의: codex/responses 는 Codex CLI 가 쓰는 내부(비공식) 엔드포인트다. 구글/오픈AI 의
> 공개 SDK 가 아니므로 변경될 수 있다. 구현은 오픈소스 chatgpt-imagegen 의 검증된
> 메커니즘(토큰 로드/갱신·헤더·페이로드·SSE 파싱)을 차용했다.

★ 토큰·헤더·SSE 는 이제 **`llm/codex_transport.py`** 에 산다. 파이프라인(대본·목차·
  비전)도 같은 전송을 쓰는데 `llm/` 이 `imgstudio/` 를 import 하면 의존이 거꾸로라
  한 칸 아래로 내렸다. 이 파일은 **그 이름들을 그대로 re-export** 하므로
  `codex_text.py` · `batch_jobs.py` · 라우트는 예전처럼 여기서 가져다 쓰면 된다.

호출부(routes)는 blocking 함수를 asyncio.to_thread 로 감싸 이벤트루프를 막지 않는다.
"""
from __future__ import annotations

import base64
import os
import re
import time
import urllib.error
from typing import List, Optional, Tuple

# ── 전송 계층 re-export ───────────────────────────────────────────────────────
# 이 목록이 곧 예전에 이 파일이 들고 있던 것들이다. 이름을 바꾸지 않는다 —
# `codex_text.py:17-28` 이 밑줄 함수까지 여기서 가져간다.
from .codex_transport import (  # noqa: F401
    AUTH_PATH,
    CODEX_BACKEND,
    CODEX_MODEL,
    FALLBACK_UA,
    FALLBACK_VERSION,
    ImageEngineError,
    JsonDict,
    NotAuthenticated,
    OAUTH_CLIENT_ID,
    OAUTH_TOKEN_URL,
    VERSION_PATH,
    _build_headers,
    _detect_codex_version,
    _encode_ref,
    _extract_tokens,
    _load_auth,
    _loosen_read_timeout,
    _persist_refreshed_auth,
    _refresh_access_token,
    refresh_once,
    _sniff_mime,
    _stream,
)

# ── 이미지에만 있는 상수 ──────────────────────────────────────────────────────
# image_generation 툴이 받는 크기 규칙(공식 문서 기준).
#   · 가로·세로 모두 **16의 배수**
#   · 비율은 **1:3 ~ 3:1** 사이
#   · 최대 3840x2160 (2560x1440 초과는 실험적)
#
# 예전에 이 파일은 "1024x1024 · 1536x1024 · 1024x1536 셋뿐"으로 알고 막아 뒀는데,
# 그건 구형 gpt-image-1 기준이라 지금 모델에는 맞지 않는다. 그 탓에 16:9 를 고르면
# 조용히 3:2 로 바뀌어 나갔다.
#
# 2026-08-29 실측 — 16:9·9:16·4:3·21:9 모두 그대로 생성됐다:
#     1536x864  요청 → 1672x941   (16:9)
#     864x1536  요청 → 941x1672   (9:16)
#     1408x1056 요청 → 1448x1086  (4:3)
#     1536x656  요청 → 1918x820   (21:9)
# 보다시피 **비율은 지키지만 해상도는 백엔드가 자기 기준으로 다시 잡는다.**
# size 는 정확한 픽셀 지정이 아니라 사실상 비율 지정에 가깝다.
CODEX_MAX_W, CODEX_MAX_H = 3840, 2160

# 배경을 불투명으로 못박는다. image_generation 툴의 background 기본값이 "auto" 라서
# 모델이 그때그때 정하고, 간혹 알파(투명)로 비워 버린다. 투명 PNG 는 보는 쪽 배경색이
# 그대로 비치므로 — 다크모드 탐색기, 어두운 PPT 마스터에서 글자가 배경에 묻힌다.
# 프롬프트에 "바탕을 칠하라"를 적어도 알파로 비우면 그대로 통과하니 여기서 막아야 한다.
# (2026-08-17 실측: 제21장 32장 중 003·014 두 장만 RGBA 로 나왔다.)
# 잘라내기용 투명 PNG 가 필요하면 CODEX_IMAGE_BACKGROUND=transparent 로 돌린다.
CODEX_BACKGROUND = os.environ.get("CODEX_IMAGE_BACKGROUND", "opaque")
DEFAULT_TOTAL_TIMEOUT = int(os.environ.get("IMAGE_TIMEOUT", "300"))
DEFAULT_STALL_TIMEOUT = 120.0
REF_B64_SOFT_CAP = 8 * 1024 * 1024  # 참조 이미지 base64 상한(초과 시 경고만)


# ── 페이로드 ──────────────────────────────────────────────────────────────────
def _build_user_text(prompt: str, size: str, output_format: str, is_edit: bool) -> str:
    if is_edit:
        text = (
            "Use the image_generation tool to edit the attached reference image(s). "
            "Treat the reference as the canonical subject — reproduce its exact pattern, "
            "colours, and texture faithfully; do not redesign the subject itself. "
            f"Request: {prompt}. Output format: {output_format}."
        )
    else:
        text = (
            "Use the image_generation tool to render the following. "
            f"Request: {prompt}. Output format: {output_format}."
        )
    if size and size != "auto":
        text += f" Size: {size}."
    text += " Do not include explanatory text — produce only the image."
    return text


def _build_payload(prompt: str, size: str, output_format: str, model: str,
                   refs: Optional[List[Tuple[str, str]]]) -> JsonDict:
    is_edit = bool(refs)
    user_text = _build_user_text(prompt, size, output_format, is_edit)

    image_tool: JsonDict = {"type": "image_generation", "output_format": output_format}
    if CODEX_BACKGROUND:
        image_tool["background"] = CODEX_BACKGROUND
    if size and size != "auto":
        image_tool["size"] = size

    if refs:
        content = [
            {"type": "input_image", "image_url": f"data:{mime};base64,{b64}"}
            for (b64, mime) in refs
        ]
        content.append({"type": "input_text", "text": user_text})
        tool_choice = "required"
    else:
        content = [{"type": "input_text", "text": user_text}]
        tool_choice = "auto"

    return {
        "model": model,
        "stream": True,
        "instructions": "You are an image generation assistant.",
        "input": [{"type": "message", "role": "user", "content": content}],
        "tools": [image_tool],
        "tool_choice": tool_choice,
        "parallel_tool_calls": False,
        "store": False,
        "reasoning": {"effort": "low", "summary": "auto"},
        "include": ["reasoning.encrypted_content"],
        "text": {"verbosity": "low"},
    }


# ── 이미지 응답 SSE 파싱 ──────────────────────────────────────────────────────
def _post_for_image(headers: dict, payload: JsonDict,
                    deadline: float, stall_timeout: float) -> Tuple[bytes, JsonDict]:
    image_b64: Optional[str] = None
    item_meta: JsonDict = {}
    seen: dict = {}
    failure_detail: Optional[str] = None
    try:
        for evt in _stream(headers, payload, deadline, stall_timeout):
            t = evt.get("type", "?")
            seen[t] = seen.get(t, 0) + 1
            if t in ("error", "response.failed"):
                detail = None
                if isinstance(evt.get("response"), dict):
                    err = evt["response"].get("error")
                    if isinstance(err, dict):
                        detail = err.get("message") or err.get("code")
                if isinstance(evt.get("error"), dict):
                    detail = evt["error"].get("message") or detail
                failure_detail = str(detail or evt.get("message") or evt.get("code") or "")
            if t == "response.output_item.done":
                item = evt.get("item")
                if isinstance(item, dict) and item.get("type") == "image_generation_call":
                    if isinstance(item.get("result"), str):
                        image_b64 = item["result"]
                        item_meta = {k: v for k, v in item.items() if k != "result"}
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="ignore")
        raise ImageEngineError(f"HTTP {e.code}: {body_text[:400]}", status=e.code)
    except (TimeoutError, ConnectionError):
        raise ImageEngineError("이미지 백엔드 응답이 지연/중단되었습니다. 다시 시도하세요.")
    except urllib.error.URLError as e:
        raise ImageEngineError(f"네트워크 오류: {e.reason}")

    if not image_b64:
        types_seen = ", ".join(sorted(seen)) or "(none)"
        if failure_detail:
            raise ImageEngineError(f"생성 실패: {failure_detail} (events: {types_seen})")
        raise ImageEngineError(f"이미지가 반환되지 않았습니다. (events: {types_seen})")
    try:
        return base64.b64decode(image_b64, validate=True), item_meta
    except Exception:
        raise ImageEngineError("백엔드가 잘못된 base64 를 반환했습니다.")


def _check_size(size: str) -> None:
    """보내기 전에 크기 규칙을 확인한다 — 백엔드 오류 문구가 불친절해서."""
    if not size or size == "auto":
        return
    m = re.fullmatch(r"(\d+)\s*[xX]\s*(\d+)", size.strip())
    if not m:
        raise ImageEngineError(f"크기 형식이 잘못됐습니다: {size} (예: 1536x1024)")
    w, h = int(m.group(1)), int(m.group(2))
    if w % 16 or h % 16:
        raise ImageEngineError(f"{size} — 가로·세로 모두 16의 배수여야 합니다.")
    if not (1 / 3) <= (w / h) <= 3:
        raise ImageEngineError(f"{size} — 비율은 1:3 에서 3:1 사이여야 합니다.")
    if w > CODEX_MAX_W or h > CODEX_MAX_H:
        raise ImageEngineError(
            f"{size} — 최대 {CODEX_MAX_W}x{CODEX_MAX_H} 까지입니다.")


# ── codex 엔진 진입점 (401 시 갱신 후 1회 재시도) ─────────────────────────────
def _run_codex(prompt: str, size: str, output_format: str,
               refs: Optional[List[bytes]]) -> Tuple[bytes, JsonDict]:
    _check_size(size)
    auth = _load_auth()
    access, account_id, refresh = _extract_tokens(auth)
    if not access:
        raise NotAuthenticated(
            "~/.codex/auth.json 에 ChatGPT OAuth access_token 이 없습니다. "
            "`codex login` 으로 구독 계정에 로그인하세요. (OPENAI_API_KEY 로는 대체 불가)"
        )

    loaded_refs = None
    if refs:
        loaded_refs = [_encode_ref(r) for r in refs]

    version = _detect_codex_version()
    payload = _build_payload(prompt, size, output_format, CODEX_MODEL, loaded_refs)
    deadline = time.monotonic() + DEFAULT_TOTAL_TIMEOUT

    def _attempt(token: str) -> Tuple[bytes, JsonDict]:
        headers = _build_headers(token, account_id, version)
        return _post_for_image(headers, payload, deadline, DEFAULT_STALL_TIMEOUT)

    try:
        return _attempt(access)
    except ImageEngineError as e:
        # ★ 갱신은 `refresh_once` 가 **직렬로** 한다. 워커 셋이 동시에 만료를
        #   만나 각자 갱신하면 refresh_token 이 회전해 나머지가 401 을 받는다.
        if e.status == 401:
            _, new_access, account_id, _ = refresh_once(access)
            return _attempt(new_access)
        raise


# ── 공개 API ──────────────────────────────────────────────────────────────────
def generate_image(prompt: str, *, size: str = "auto",
                   refs: Optional[List[bytes]] = None,
                   settings: Optional[dict] = None) -> Tuple[bytes, dict]:
    """프롬프트(+ 선택 참조 이미지)로 이미지 1장을 생성/편집/병합한다.

    refs 가 있으면 편집/병합 경로(image-to-image). 반환: (png_bytes, meta).
    """
    settings = settings or {}
    output_format = settings.get("default_format", "png")
    img, meta = _run_codex(prompt, size, output_format, refs)
    meta["engine"] = "codex"
    meta["model"] = CODEX_MODEL
    return img, meta
