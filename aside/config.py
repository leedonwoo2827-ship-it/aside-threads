"""설정 — `aside.config.json` 위에 `aside.config.local.json`(PC별, git 제외)을 덮는다.

job 설정은 `jobs/<job>/job.json` 위에 `job.local.json` 을 덮는다(lecture-composer 와 같은 규칙).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parent.parent
JOBS = ROOT / "jobs"
PROFILES = ROOT / "profiles"
LOGS = ROOT / "logs"
TEMPLATES = ROOT / "templates"
LOCAL = ROOT / "local.json"

DEFAULTS: Dict[str, Any] = {
    "llm": {"total_timeout_sec": 900, "stall_timeout_sec": 180, "retries": 2},
    "models": {"copy": "auto", "image": "auto"},       # auto = 계정에서 되는 모델을 찾아 local.json 에 기억
    "effort": {"copy": "medium"},
    "image": {
        "bg": "#F6F1E8", "accent_a": "#1F4E79", "accent_b": "#9DC3E6",
        "cover_size": "1536x1024", "spot_size": "1024x1024",
        "per_post": 2, "workers": 3, "retries": 2,
        "negative": "",
    },
    "card": {"w": 1080, "h": 1350},
    "threads": {"chrome": "", "native_schedule": False, "slots": ["08:30", "19:30"],
                "max_per_run": 5, "base_port": 9341},
    "ui": {"port": 5291, "width": 460},
}


def deep_merge(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(a)
    for k, v in (b or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as e:
        raise SystemExit(f"JSON 이 깨졌습니다: {path} — {e}")


def write_json(path: Path, data: Any) -> None:
    """원자적 쓰기 — 서버와 하위 프로세스가 같은 파일을 만진다."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def load() -> Dict[str, Any]:
    cfg = deep_merge(DEFAULTS, read_json(ROOT / "aside.config.json", {}) or {})
    return deep_merge(cfg, read_json(ROOT / "aside.config.local.json", {}) or {})


def local() -> Dict[str, Any]:
    return read_json(LOCAL, {}) or {}


def save_local(data: Dict[str, Any]) -> None:
    write_json(LOCAL, data)
