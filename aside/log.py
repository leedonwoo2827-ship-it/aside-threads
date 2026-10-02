"""로그 두 갈래.

  log(msg)     패널 로그 창에 보이는 줄 — **쉬운 말만.** 팀원이 읽는다(모델 이름·오류 코드·명령어 금지).
  detail(msg)  logs/detail.log 에만 남는 줄 — 모델·HTTP 코드·재시도 같은 기술 내용. 문제를 고칠 때 본다.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

_DETAIL = Path(__file__).resolve().parent.parent / "logs" / "detail.log"


def _clean(msg: object) -> str:
    # 추론 요약 조각이 글자 중간에서 잘려 서로게이트가 섞여 올 때가 있다 — 깨진 글자는 ? 로
    return str(msg).encode("utf-8", "replace").decode("utf-8")


def detail(msg: object) -> None:
    try:
        _DETAIL.parent.mkdir(parents=True, exist_ok=True)
        with _DETAIL.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {_clean(msg)}\n")
    except OSError:
        pass


def log(msg: object) -> None:
    msg = _clean(msg)
    detail(msg)
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        sys.stdout.buffer.write((line + "\n").encode("utf-8", "replace"))
        sys.stdout.flush()


def usage() -> str:
    """남은 사용량 한 줄(쉬운 말). 아직 Codex 를 한 번도 안 불렀으면 빈 문자열."""
    try:
        from .llm import codex_transport
        lim = codex_transport.limits()
        used = lim.get("used_5h")
        if used is None:
            return ""
        return f"  남은 사용량 {max(0, 100 - int(round(float(used))))}%"
    except Exception:
        return ""
