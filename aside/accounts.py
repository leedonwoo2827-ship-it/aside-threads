"""Threads 계정 = Chrome 전용 프로필 하나 + CDP 포트 하나.

local.json (PC별, git 제외):
  {"me": "홍길동", "current": "study", "accounts": [{"name": "study", "port": 9341, "label": "@aside.study"}]}
프로필 폴더 profiles/<name>/ 에 로그인 쿠키가 산다 — **절대 커밋하지 않는다.**
같은 프로필 Chrome 은 동시에 하나만 뜰 수 있어서 계정마다 포트를 따로 준다.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config


def all_() -> List[Dict[str, Any]]:
    return list(config.local().get("accounts") or [])


def get(name: Optional[str]) -> Dict[str, Any]:
    accs = all_()
    if not accs:
        raise SystemExit("계정이 없습니다 — `python -m aside account add <이름>` 또는 패널의 「계정 추가」")
    name = name or config.local().get("current") or accs[0]["name"]
    for a in accs:
        if a["name"] == name:
            return a
    raise SystemExit(f"계정이 없습니다: {name} (있는 것: {', '.join(a['name'] for a in accs)})")


def profile(acc: Dict[str, Any]) -> Path:
    p = config.PROFILES / acc["name"]
    p.mkdir(parents=True, exist_ok=True)
    return p


def add(name: str, label: str = "") -> Dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", name):
        raise SystemExit("계정 이름은 영문·숫자·_- 만 (폴더 이름이 된다). 화면 이름은 label 에")
    data = config.local()
    accs = list(data.get("accounts") or [])
    if any(a["name"] == name for a in accs):
        raise SystemExit(f"이미 있습니다: {name}")
    base = int(config.load()["threads"]["base_port"])
    used = {int(a["port"]) for a in accs}
    port = next(p for p in range(base, base + 200) if p not in used)
    acc = {"name": name, "port": port, "label": label or name}
    accs.append(acc)
    data["accounts"] = accs
    data.setdefault("current", name)
    config.save_local(data)
    profile(acc)
    return acc


def remove(name: str) -> None:
    data = config.local()
    data["accounts"] = [a for a in data.get("accounts") or [] if a["name"] != name]
    if data.get("current") == name:
        data["current"] = data["accounts"][0]["name"] if data["accounts"] else None
    config.save_local(data)


def set_current(name: str) -> None:
    get(name)
    data = config.local()
    data["current"] = name
    config.save_local(data)
