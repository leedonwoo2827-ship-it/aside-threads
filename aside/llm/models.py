"""어떤 모델을 쓸지 — **계정마다 다르다.**

2026-10-03 실측(이 PC): models_cache 에 gpt-5.5 가 보이는데 404, gpt-6-* 는 「ChatGPT 계정으론
안 됨」 400, gpt-5.6-terra·luna 는 된다. 팀원마다 요금제가 달라 고정할 수 없으므로:
  1) aside.config(.local).json 의 models.copy 가 "auto" 가 아니면 그것
  2) local.json 에 캐시된 codex_model
  3) ~/.codex/models_cache.json 의 visibility=list 순서대로 ping → 처음 되는 것을 캐시
텍스트와 그림(image_generation 툴을 부르는 추론 모델) 둘 다 이 모델을 쓴다.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List

from .. import config
from ..log import detail, log

PREFER = ["gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5"]


def _candidates() -> List[str]:
    out: List[str] = []
    try:
        d = json.loads((Path.home() / ".codex" / "models_cache.json").read_text(encoding="utf-8"))
        out = [m.get("slug") for m in d.get("models", []) if m.get("visibility") == "list" and m.get("slug")]
    except Exception:
        pass
    ranked = [m for m in PREFER if m in out] + [m for m in out if m not in PREFER]
    return ranked or PREFER


def resolve(probe: bool = True) -> str:
    want = (config.load()["models"].get("copy") or "auto").strip()
    if want and want != "auto":
        return want
    cached = config.local().get("codex_model")
    if cached:
        return cached
    if not probe:
        return PREFER[0]
    from .codex_provider import CodexProvider
    for m in _candidates():
        ok, msg = CodexProvider(model=m, effort="low", retries=0).ping()
        if ok:
            data = config.local()
            data["codex_model"] = m
            config.save_local(data)
            detail(f"  Codex 모델 자동 선택: {m} (local.json 에 기억 — 바꾸려면 그 칸을 지우세요)")
            return m
        detail(f"  · {m} 안 됨: {msg[:300]}")
    raise SystemExit("이 Codex 계정으로 쓸 수 있는 모델을 찾지 못했습니다 — `codex login` 상태를 확인하세요")


def apply(model: str) -> None:
    """그림 쪽(codex_image)이 import 때 잡아 둔 모델 이름까지 바꿔 끼운다."""
    from . import codex_image, codex_transport
    codex_transport.CODEX_MODEL = model
    codex_image.CODEX_MODEL = model


# ── 그림 ────────────────────────────────────────────────────────────────────
# 2026-10-03 실측: gpt-5.6-* 는 tool_mode=code_mode_only 라 백엔드가 image_generation 툴을
# **조용히 떼어 버린다**(빈 메시지로 끝남, tool_choice=required 를 주면 「tools 가 없다」 400).
# gpt-5.5 는 이 계정에서 404. 즉 계정·요금제에 따라 그림이 아예 안 될 수 있다 — 그래서 탐지한다.
# 안 되는 계정은 빈 응답이 몇 초 만에 오므로 탐지 비용이 거의 없다.
NO_IMAGE = "none"


def _image_candidates() -> List[str]:
    try:
        d = json.loads((Path.home() / ".codex" / "models_cache.json").read_text(encoding="utf-8"))
        ms = [m for m in d.get("models", []) if m.get("slug")]
        # code_mode_only 가 아닌 모델을 앞에 — 그쪽이 툴을 직접 받는다
        ms.sort(key=lambda m: (m.get("tool_mode") == "code_mode_only", m.get("visibility") != "list"))
        return [m["slug"] for m in ms]
    except Exception:
        return ["gpt-5.5", *PREFER]


def resolve_image(force_probe: bool = False) -> str:
    """그림을 굽는 모델, 없으면 NO_IMAGE. 결과는 local.json 의 codex_image_model 에 기억."""
    want = (config.load()["models"].get("image") or "auto").strip()
    if want not in ("", "auto"):
        return want
    cached = config.local().get("codex_image_model")
    if cached and not force_probe:
        return cached
    from .codex_image import generate_image
    from .codex_transport import ImageEngineError, NotAuthenticated
    found = NO_IMAGE
    log("처음 한 번, 그림을 그릴 수 있는지 확인하는 중이에요 …")
    for m in _image_candidates():
        apply(m)
        try:
            generate_image("tiny flat blue dot on plain ivory background, no text", size="1024x1024")
            found = m
            break
        except NotAuthenticated:
            raise
        except ImageEngineError as e:
            detail(f"  · 그림 {m}: {str(e)[:300]}")
    data = config.local()
    data["codex_image_model"] = found
    config.save_local(data)
    detail(f"  그림 모델: {found if found != NO_IMAGE else '없음 — 이 계정 Codex 는 지금 그림 도구를 열어 주지 않음'}")
    return found
