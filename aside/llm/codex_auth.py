"""codex(OpenAI Codex CLI) 인증 상태 헬퍼.

Codex 는 "Sign in with ChatGPT"(OAuth) 로 로그인하면 API 키 없이 ChatGPT 구독 할당량으로
모델을 쓴다. 로그인 `codex login`, 로그아웃 `codex logout`, 자격증명은 ~/.codex/auth.json.

(backplate imgstudio/services/codex_auth.py 그대로 — aside 는 status()·codex_path() 만 쓰고,
 로그인은 cli `codex-login` 이 패널 로그 창으로 흘려 보낸다.)
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

CODEX_BIN = os.environ.get("CODEX_BIN", "codex")
AUTH_PATH = os.environ.get("CODEX_AUTH_PATH", str(Path.home() / ".codex" / "auth.json"))

_FALLBACK_CODEX_PATHS = [
    os.path.expanduser("~/.local/bin/codex"),
    os.path.expandvars(r"%APPDATA%\npm\codex.cmd"),
    os.path.expandvars(r"%APPDATA%\npm\codex"),
]


def codex_path() -> Optional[str]:
    if os.path.sep in CODEX_BIN or (os.path.altsep and os.path.altsep in CODEX_BIN):
        return CODEX_BIN if os.path.isfile(CODEX_BIN) else None
    found = shutil.which(CODEX_BIN)
    if found:
        return found
    for p in _FALLBACK_CODEX_PATHS:
        if p and os.path.isfile(p):
            return p
    return None


def is_installed() -> bool:
    return codex_path() is not None


def _read_auth() -> Optional[dict]:
    try:
        if os.path.isfile(AUTH_PATH):
            with open(AUTH_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return None


def has_token() -> bool:
    """~/.codex/auth.json 에 사용 가능한 ChatGPT OAuth access_token 이 있으면 True."""
    auth = _read_auth()
    if not isinstance(auth, dict):
        return False
    tokens = auth.get("tokens")
    return isinstance(tokens, dict) and isinstance(tokens.get("access_token"), str)


def _decode_jwt_email(token: str) -> Optional[str]:
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64).decode("utf-8"))
        for k in ("email", "preferred_username"):
            v = payload.get(k)
            if isinstance(v, str) and "@" in v:
                return v.strip().lower()
        prof = payload.get("https://api.openai.com/auth") or payload.get(
            "https://api.openai.com/profile"
        )
        if isinstance(prof, dict) and isinstance(prof.get("email"), str):
            return prof["email"].strip().lower()
    except Exception:
        pass
    return None


def _deep_find(obj, keys) -> Optional[str]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in keys and isinstance(v, str) and v.strip():
                return v.strip()
        for v in obj.values():
            r = _deep_find(v, keys)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _deep_find(v, keys)
            if r:
                return r
    return None


def get_account_email() -> Optional[str]:
    auth = _read_auth()
    if not isinstance(auth, dict):
        return None
    email = _deep_find(auth, {"email", "account_email", "preferred_username"})
    if email and "@" in email:
        return email.strip().lower()
    idt = _deep_find(auth, {"id_token", "access_token"})
    if idt and idt.count(".") >= 2:
        return _decode_jwt_email(idt)
    return None


def login_terminal_cmd() -> List[str]:
    return [codex_path() or CODEX_BIN, "login"]


def logout_terminal_cmd() -> List[str]:
    return [codex_path() or CODEX_BIN, "logout"]


def launch_console(args: List[str]) -> dict:
    """새 콘솔 창에서 codex 명령을 실행한다(로그인=계정변경 / 로그아웃).

    Windows 는 CREATE_NEW_CONSOLE 로 별도 cmd 창을 띄우고 `/k` 로 창을 유지한다
    (로그인은 브라우저를 열고 사용자 입력을 기다리므로 창이 보여야 한다).
    """
    import subprocess
    import sys
    try:
        if sys.platform == "win32":
            CREATE_NEW_CONSOLE = 0x00000010
            subprocess.Popen(["cmd", "/k"] + args, creationflags=CREATE_NEW_CONSOLE)
        else:
            subprocess.Popen(args)
        return {"ok": True}
    except FileNotFoundError:
        return {"ok": False, "error": "codex CLI 를 찾을 수 없습니다. `npm i -g @openai/codex` 후 다시 시도하세요."}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


# ── 로그인 도중 취소에 대비한 백업 ─────────────────────────────────────────────
# ★ `codex login` 은 **시작하자마자 기존 auth.json 을 지운다**(2026-10-03 실측: 로그인 창을 띄웠다가
#   취소했더니 멀쩡하던 로그인이 사라졌다). 그래서 시작 전에 떠 두고, 끝나지 않으면 되돌린다.
#   aside 프로세스가 「중지」로 죽어 되돌리지 못한 경우는 다음 상태 확인 때 restore_if_needed() 가 한다.
BACKUP_PATH = AUTH_PATH + ".aside-bak"


def backup() -> bool:
    import shutil as _sh
    if os.path.isfile(AUTH_PATH):
        _sh.copy2(AUTH_PATH, BACKUP_PATH)
        return True
    return False


def drop_backup() -> None:
    try:
        os.remove(BACKUP_PATH)
    except OSError:
        pass


def login_running() -> bool:
    """codex login 이 로그인 대기 중이면 1455 포트를 열고 있다."""
    import socket
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", 1455)) == 0


def restore_if_needed() -> bool:
    """로그인 파일이 없어졌는데 백업이 있고, 로그인이 진행 중이 아니면 되돌린다."""
    import shutil as _sh
    if os.path.isfile(BACKUP_PATH) and not has_token() and not login_running():
        _sh.copy2(BACKUP_PATH, AUTH_PATH)
        drop_backup()
        return True
    if os.path.isfile(BACKUP_PATH) and has_token() and not login_running():
        drop_backup()          # 새 로그인이 끝났다 — 백업은 필요 없다
    return False


def status() -> dict:
    installed = is_installed()
    authed = has_token()
    return {
        "provider": "codex",
        "label": "OpenAI (ChatGPT)",
        "installed": installed,
        "authenticated": authed,
        "email": (get_account_email() if authed else None),
        "login_hint": "터미널에서 `codex login` 을 실행해 ChatGPT 계정으로 로그인하세요.",
    }
