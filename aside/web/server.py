"""aside 패널 서버 — 127.0.0.1 전용. 화면 오른쪽 좁은 앱창이 이 서버를 본다.

무거운 일(Codex·Playwright)은 전부 `python -m aside …` 하위 프로세스로 돌리고 로그만 읽는다
(lecture-composer 방식). 한 번에 하나만 돈다 — 할당량과 브라우저를 두 일이 다투지 않게.
예약 대기열은 30초마다 확인해서 시각이 된 것을 하나씩 올린다.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from .. import accounts, config, schedule
from ..job import Job, all_jobs
from ..log import log
from ..s4_render import plain_title

STATIC = Path(__file__).parent / "static"
ALLOWED = {"make", "s1-copy", "s2-imgjson", "s3-images", "s4-render", "post", "plan", "queue",
           "login", "probe", "doctor", "fonts", "codex-login"}

app = FastAPI(title="aside")


# ── 하위 프로세스 하나 ────────────────────────────────────────────────────────
def label(args: List[str]) -> str:
    """로그 창에 보일 일 이름 — 명령어 대신 쉬운 말."""
    cmd = args[0] if args else ""
    if cmd == "post":
        return "미리 채워 보기" if "--dry-run" in args else ("예약 넣기" if "--at" in args else "올리기")
    names = {"make": "딸깍 만들기", "s1-copy": "글 쓰기", "s2-imgjson": "그림 설명 준비",
             "s3-images": "그림 그리기", "s4-render": "카드 다시 만들기", "queue": "예약 시간 확인",
             "plan": "예약 자동 배치", "login": "로그인 창 열기", "probe": "글쓰기 창 구조 확인",
             "doctor": "점검", "fonts": "글꼴 받기", "codex-login": "Codex 로그인"}
    return names.get(cmd, cmd)


class Runner:
    def __init__(self) -> None:
        self.proc: Optional[subprocess.Popen] = None
        self.lines: List[str] = []
        self.cmd: List[str] = []
        self.code: Optional[int] = None
        self.lock = threading.Lock()

    @property
    def busy(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, args: List[str]) -> None:
        with self.lock:
            if self.busy:
                raise HTTPException(409, "지금 다른 일을 하는 중이에요. 끝나면 다시 눌러 주세요.")
            env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
            self.cmd = args
            self.code = None
            self.lines.append(f"▶ {label(args)}")
            from ..log import detail
            detail(f"$ aside {' '.join(args)}")
            self.proc = subprocess.Popen(
                [child_python(), "-m", "aside", *args], cwd=str(config.ROOT), env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            threading.Thread(target=self._pump, args=(self.proc,), daemon=True).start()

    def _pump(self, proc: subprocess.Popen) -> None:
        for line in proc.stdout:
            self.lines.append(line.rstrip("\n"))
            if len(self.lines) > 3000:
                del self.lines[:1000]
        self.code = proc.wait()
        if getattr(self, "stopped", False):
            self.code = -1
            self.stopped = False
        else:
            self.lines.append("✓ 다 됐어요" if self.code == 0 else "✗ 중간에 멈췄어요 — 바로 위 줄을 확인해 주세요")

    def stop(self) -> None:
        if self.busy:
            self.stopped = True
            self.lines.append("■ 멈췄어요. 이미 된 건 남아 있고, 다시 누르면 남은 것만 이어서 해요.")
            if sys.platform.startswith("win"):
                subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                               capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            else:
                self.proc.terminate()


RUN = Runner()


def _scheduler() -> None:
    while True:
        time.sleep(30)
        try:
            if not RUN.busy and schedule.due():
                RUN.start(["queue"])
        except Exception as e:      # noqa: BLE001 — 대기열 확인이 서버를 죽이면 안 된다
            from ..log import detail
            detail(f"대기열 확인 실패: {e}")


# ── 보기 ─────────────────────────────────────────────────────────────────────
def _job(name: str) -> Job:
    job = Job(name)
    if not job.exists():
        raise HTTPException(404, f"원고 묶음을 찾을 수 없어요: {name}")
    return job


def _status(job: Job, data_id: str, st: Dict[str, Any], has_post: bool, n_cards: int) -> str:
    if st.get("posted"):
        return "posted"
    if (st.get("scheduled") or {}).get("when"):
        return "scheduled"
    if st.get("error"):
        return "error"
    if n_cards:
        return "ready"
    return "copy" if has_post else "new"


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC / "aside.html").read_text(encoding="utf-8")


@app.get("/api/state")
def state() -> Dict[str, Any]:
    loc = config.local()
    return {
        "jobs": [{"name": j.name, "title": j.get("title", j.name)} for j in all_jobs()],
        "accounts": accounts.all_(),
        "current": loc.get("current"),
        "model": loc.get("codex_model"),
        "slots": config.load()["threads"]["slots"],
        "native": bool(config.load()["threads"].get("native_schedule")),
        "busy": RUN.busy, "cmd": RUN.cmd, "app": "aside",
    }


@app.get("/api/codex")
def codex_status() -> Dict[str, Any]:
    """Codex(ChatGPT) 로그인 상태 — 패널 맨 위에 보인다. 파일만 보므로 빠르다(세션이 죽었는지는 모른다)."""
    from ..llm import codex_auth
    restored = codex_auth.restore_if_needed()     # 로그인 도중 「중지」로 끊겼으면 원래 로그인으로
    st = codex_auth.status()
    return {"installed": st["installed"], "logged_in": st["authenticated"], "email": st["email"],
            "restored": restored}


@app.get("/api/log")
def get_log(since: int = 0) -> Dict[str, Any]:
    total = len(RUN.lines)
    since = min(max(0, since), total)
    return {"lines": RUN.lines[since:], "next": total, "busy": RUN.busy, "code": RUN.code, "cmd": RUN.cmd,
            "label": label(RUN.cmd)}


class RunBody(BaseModel):
    args: List[str]


@app.post("/api/run")
def run(body: RunBody) -> Dict[str, Any]:
    if not body.args or body.args[0] not in ALLOWED:
        raise HTTPException(400, "허용되지 않은 명령")
    RUN.start(body.args)
    return {"ok": True}


@app.post("/api/stop")
def stop() -> Dict[str, Any]:
    RUN.stop()
    return {"ok": True}


@app.get("/api/jobs/{name}/posts")
def posts(name: str) -> List[Dict[str, Any]]:
    job = _job(name)
    st_all = job.state()
    out = []
    for s in job.slides:
        post = job.post(s.data_id)
        cards = job.card_files(s.data_id)
        st = st_all.get(s.data_id) or {}
        out.append({
            "data_id": s.data_id, "n": s.n, "title": plain_title(s.title), "section": s.section,
            "q_count": s.q_count, "hook": (post or {}).get("hook", ""),
            "cards": len(cards), "status": _status(job, s.data_id, st, bool(post), len(cards)),
            "scheduled": st.get("scheduled"), "posted": st.get("posted"), "error": st.get("error"),
            "cover": (job.images / f"{s.data_id}-cover.png").exists(),
        })
    return out


@app.get("/api/jobs/{name}/posts/{data_id}")
def post_detail(name: str, data_id: str) -> Dict[str, Any]:
    job = _job(name)
    s = job.slide(data_id)
    if not s:
        raise HTTPException(404)
    cards = job.card_files(data_id)
    stamp = int(max((c.stat().st_mtime for c in cards), default=0))
    return {
        "data_id": data_id, "title": plain_title(s.title), "section": s.section,
        "say": s.say, "q_detail": s.q_detail, "topic": job.get("topic", ""),
        "post": job.post(data_id), "overridden": (job.overrides / f"{data_id}.json").exists(),
        "cards": [f"/files/{name}/cards/{data_id}/{c.name}?v={stamp}" for c in cards],
        "images": {r: (job.images / f"{data_id}-{r}.png").exists() for r in ("cover", "spot")},
        "state": job.state().get(data_id) or {},
        "next_slot": schedule.next_slots(1, config.local().get("current"))[0].strftime("%Y-%m-%dT%H:%M"),
    }


class Patch(BaseModel):
    patch: Dict[str, Any]
    rerender: bool = True


@app.put("/api/jobs/{name}/posts/{data_id}")
def save_post(name: str, data_id: str, body: Patch) -> Dict[str, Any]:
    job = _job(name)
    if not job.raw_post(data_id):
        raise HTTPException(400, "글이 아직 없어요. 먼저 「딸깍 만들기」를 눌러 주세요.")
    job.save_override(data_id, body.patch)
    if body.rerender and not RUN.busy:
        RUN.start(["s4-render", "--job", name, "--only", data_id])
    return {"ok": True}


@app.delete("/api/jobs/{name}/posts/{data_id}/override")
def reset_post(name: str, data_id: str) -> Dict[str, Any]:
    job = _job(name)
    job.reset_override(data_id)
    if not RUN.busy:
        RUN.start(["s4-render", "--job", name, "--only", data_id])
    return {"ok": True}


@app.get("/files/{name}/{kind}/{rest:path}")
def files(name: str, kind: str, rest: str):
    if kind not in ("cards", "images"):
        raise HTTPException(404)
    base = (_job(name).dir / kind).resolve()
    p = (base / rest).resolve()
    if base not in p.parents or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p, headers={"Cache-Control": "no-store"})


@app.get("/api/jobs/{name}/prompts/{data_id}")
def image_prompts(name: str, data_id: str) -> List[Dict[str, Any]]:
    """그 게시물의 그림 지시문 — 「프롬프트 복사」로 ChatGPT 앱에 붙여 넣는 용도."""
    from ..s3_images import full_prompt
    env = config.read_json(_job(name).images / "이미지프롬프트.json") or {}
    return [{"role": it["role"], "file": it["file"], "size": it.get("size"),
             "prompt": full_prompt(it, env.get("style_hint", ""))}
            for it in env.get("prompts", []) if it["data_id"] == data_id]


class Upload(BaseModel):
    data_url: str


@app.post("/api/jobs/{name}/images/{data_id}/{role}")
def put_image(name: str, data_id: str, role: str, body: Upload) -> Dict[str, Any]:
    """사람이 만든 그림 넣기(ChatGPT 앱 등) — 넣으면 카드를 다시 찍는다."""
    import base64
    if role not in ("cover", "spot"):
        raise HTTPException(400)
    job = _job(name)
    if not job.slide(data_id):
        raise HTTPException(404)
    head, _, b64 = body.data_url.partition(",")
    if "image/" not in head:
        raise HTTPException(400, "그림 파일이 아니에요.")
    raw = base64.b64decode(b64)
    job.images.mkdir(parents=True, exist_ok=True)
    out = job.images / f"{data_id}-{role}.png"
    if "image/png" in head:
        out.write_bytes(raw)
    else:       # jpg·webp 는 브라우저(Playwright)로 png 로 바꾸지 않고 확장자만 png 로 두면 깨진다 → 변환
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            b = pw.chromium.launch()
            pg = b.new_page()
            pg.set_content(f'<img id=i src="{body.data_url}">')
            pg.locator("#i").screenshot(path=str(out))
            b.close()
    if not RUN.busy:
        RUN.start(["s4-render", "--job", name, "--only", data_id])
    return {"ok": True}


@app.delete("/api/jobs/{name}/images/{data_id}/{role}")
def del_image(name: str, data_id: str, role: str) -> Dict[str, Any]:
    if role not in ("cover", "spot"):
        raise HTTPException(400)
    (_job(name).images / f"{data_id}-{role}.png").unlink(missing_ok=True)
    if not RUN.busy:
        RUN.start(["s4-render", "--job", name, "--only", data_id])
    return {"ok": True}


class Pick(BaseModel):
    kind: str = "folder"      # folder | files
    path: str = ""            # 선택 창 대신 직접 입력한 폴더 경로(선택 창을 못 띄우는 PC)


@app.post("/api/pick")
def pick(body: Pick) -> Dict[str, Any]:
    """Windows 기본 선택 창을 띄워 경로를 받는다(브라우저는 실제 경로를 못 준다)."""
    import json as _json
    if body.kind not in ("folder", "files"):
        raise HTTPException(400)
    if body.path:
        d = Path(body.path.strip().strip('"')).expanduser()
        if not d.is_dir():
            raise HTTPException(400, f"폴더를 찾을 수 없어요: {d}")
        return {"folder": str(d), "files": sorted(str(p) for p in d.glob("*.html"))}
    r = subprocess.run([child_python(), "-m", "aside.pick", body.kind], cwd=str(config.ROOT),
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                       capture_output=True, text=True, encoding="utf-8", timeout=600,
                       env=dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8"))
    if r.returncode != 0:
        raise HTTPException(501, "선택 창을 띄우지 못했습니다(리눅스는 python3-tk 필요). 폴더 경로를 직접 입력하세요.")
    return _json.loads(r.stdout.strip().splitlines()[-1])


class NewJob(BaseModel):
    name: str
    src: str = ""
    files: List[str] = []
    title: str = ""
    topic: str = ""
    handle: str = ""


@app.post("/api/jobs")
def new_job(body: NewJob) -> Dict[str, Any]:
    from ..cli import main
    if Job(body.name).exists():
        raise HTTPException(400, f"「{body.name}」 이름이 이미 있어요. 다른 이름을 써 주세요.")
    args = ["new", body.name] + (["--files", *body.files] if body.files else ["--from", body.src])
    for k in ("title", "topic", "handle"):
        if getattr(body, k):
            args += [f"--{k}", getattr(body, k)]
    try:
        main(args)
    except SystemExit as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.delete("/api/jobs/{name}")
def delete_job(name: str) -> Dict[str, Any]:
    """작업 폴더를 통째로 지운다(복사본·문구·그림·카드·게시 기록). 원본 원고 폴더는 건드리지 않는다."""
    import shutil
    job = _job(name)
    if name == "example":
        raise HTTPException(400, "example 은 지우지 않습니다")
    if RUN.busy:
        raise HTTPException(409, "지금 다른 일을 하는 중이에요. 끝나면 다시 눌러 주세요.")
    pending = [k for k, v in job.state().items()
               if (v.get("scheduled") or {}).get("mode") == "queue" and not v.get("posted")]
    if pending:
        raise HTTPException(400, f"예약이 {len(pending)}건 걸려 있어요. 예약 탭에서 먼저 취소해 주세요.")
    shutil.rmtree(job.dir)
    return {"ok": True}


# ── 계정 ─────────────────────────────────────────────────────────────────────
class NewAcc(BaseModel):
    name: str
    label: str = ""


@app.post("/api/accounts")
def add_account(body: NewAcc) -> Dict[str, Any]:
    try:
        return accounts.add(body.name, body.label)
    except SystemExit as e:
        raise HTTPException(400, str(e))


@app.post("/api/accounts/{name}/use")
def use_account(name: str) -> Dict[str, Any]:
    accounts.set_current(name)
    return {"ok": True}


@app.post("/api/accounts/{name}/login")
def login(name: str) -> Dict[str, Any]:
    from .. import threads
    acc = accounts.get(name)
    if not threads.ensure_chrome(acc, threads.HOME):     # 이미 떠 있으면 앞으로 가져와 다시 붙인다
        try:
            threads.place(int(acc["port"]), "left")
        except Exception:
            pass
    return {"ok": True}


@app.get("/api/accounts/{name}/status")
def account_status(name: str) -> Dict[str, Any]:
    from .. import threads
    if RUN.busy and "post" in RUN.cmd[:1]:
        return {"chrome": True, "logged_in": None, "note": "게시 중"}
    try:
        return threads.status(accounts.get(name))
    except Exception as e:      # noqa: BLE001
        return {"chrome": False, "logged_in": None, "note": str(e)[:120]}


# ── 예약 ─────────────────────────────────────────────────────────────────────
class Sched(BaseModel):
    job: str
    data_id: str
    when: str
    account: str
    native: bool = False


@app.post("/api/schedule")
def add_schedule(body: Sched) -> Dict[str, Any]:
    job = _job(body.job)
    when = schedule.parse_when(body.when)
    if when < datetime.now():
        raise HTTPException(400, "이미 지난 시간이에요. 앞으로의 시간을 골라 주세요.")
    if body.native:
        RUN.start(["post", "--job", body.job, "--only", body.data_id, "--account", body.account,
                   "--at", when.strftime(schedule.FMT), "--native"])
        return {"ok": True, "mode": "native"}
    schedule.enqueue(job, body.data_id, when, body.account)
    return {"ok": True, "mode": "queue"}


@app.delete("/api/schedule/{name}/{data_id}")
def cancel_schedule(name: str, data_id: str) -> Dict[str, Any]:
    job = _job(name)
    st = job.state().get(data_id) or {}
    if (st.get("scheduled") or {}).get("mode") == "native":
        raise HTTPException(400, "Threads 쪽에 예약된 것입니다 — Threads 의 「예약됨」에서 취소하세요")
    job.update_state(data_id, {"scheduled": None, "error": None})
    return {"ok": True}


@app.get("/api/schedule")
def list_schedule() -> List[Dict[str, Any]]:
    out = []
    for job in all_jobs():
        for data_id, st in job.state().items():
            sc = st.get("scheduled") or {}
            s = job.slide(data_id)
            if sc.get("when") or st.get("posted"):
                out.append({"job": job.name, "data_id": data_id, "title": plain_title(s.title) if s else data_id,
                            "when": sc.get("when") or (st.get("posted") or {}).get("at", "")[:16].replace("T", " "),
                            "mode": sc.get("mode"), "account": sc.get("account") or (st.get("posted") or {}).get("account"),
                            "posted": bool(st.get("posted")), "url": (st.get("posted") or {}).get("url"),
                            "error": st.get("error")})
    return sorted(out, key=lambda r: r["when"])


class Plan(BaseModel):
    job: str
    account: str
    apply: bool = False


@app.post("/api/plan")
def plan(body: Plan) -> List[Dict[str, Any]]:
    job = _job(body.job)
    st = job.state()
    left = [s for s in job.slides if not (st.get(s.data_id) or {}).get("posted")
            and not ((st.get(s.data_id) or {}).get("scheduled") or {}).get("when")
            and job.card_files(s.data_id)]
    slots = schedule.next_slots(len(left), body.account)
    out = []
    for s, when in zip(left, slots):
        if body.apply:
            schedule.enqueue(job, s.data_id, when, body.account)
        out.append({"data_id": s.data_id, "title": plain_title(s.title), "when": when.strftime(schedule.FMT)})
    return out


# ── 띄우기 ───────────────────────────────────────────────────────────────────
def _open_panel(port: int) -> None:
    """오른쪽 좁은 앱창. 디버그 포트를 달아 두고, 뜬 뒤 화면 오른쪽 끝에 정확히 붙인다(threads.place)."""
    from ..threads import chrome_path, pick_panel_port, place, port_open, screen, wait_port
    width = int(config.load()["ui"]["width"])
    url = f"http://127.0.0.1:{port}/"
    pport = pick_panel_port(port)
    if port_open(pport):          # 이미 떠 있는 패널 — 새로 띄우지 않고 앞으로 가져와 붙인다
        try:
            place(pport, "right")
        except Exception:
            pass
        return
    sw, sh = screen()
    try:
        subprocess.Popen([chrome_path(), f"--app={url}", f"--user-data-dir={config.PROFILES / '_panel'}",
                          f"--remote-debugging-port={pport}", "--no-first-run", "--no-default-browser-check",
                          f"--window-position={max(0, sw - width)},0", f"--window-size={width},{sh - 40}"])
    except SystemExit:
        webbrowser.open(url)
        return
    if wait_port(pport, 40):
        time.sleep(0.8)
        try:
            place(pport, "right")
        except Exception as e:      # noqa: BLE001
            log(f"패널 배치 실패(무시): {e}")


class Arrange(BaseModel):
    account: str = ""


@app.post("/api/arrange")
def arrange_windows(body: Arrange) -> Dict[str, Any]:
    from .. import threads
    acc = None
    if body.account:
        try:
            acc = accounts.get(body.account)
        except SystemExit:
            acc = None
    if RUN.busy and RUN.cmd[:1] == ["post"]:
        raise HTTPException(409, "게시 중에는 창을 옮기지 않습니다")
    return {"done": threads.arrange(acc)}


@app.post("/api/quit")
def quit_app() -> Dict[str, Any]:
    """「프로그램 끄기」 — 패널 창과 서버를 함께 끈다. 걸어 둔 aside 예약은 다음에 켤 때 이어서 올라간다."""
    RUN.stop()

    def _bye():
        time.sleep(0.6)
        try:
            from ..threads import panel_port, port_open
            if port_open(panel_port()):
                urllib.request.urlopen(urllib.request.Request(
                    f"http://127.0.0.1:{panel_port()}/json/close/" + _panel_target(), method="PUT"), timeout=2)
        except Exception:
            pass
        os._exit(0)

    threading.Thread(target=_bye, daemon=True).start()
    return {"ok": True}


def _panel_target() -> str:
    import json as _json
    from ..threads import panel_port
    tabs = _json.load(urllib.request.urlopen(f"http://127.0.0.1:{panel_port()}/json/list", timeout=2))
    return next(t["id"] for t in tabs if t.get("type") == "page")


def _quiet_stdio() -> None:
    """검은 창 없이(pythonw) 뜨면 표준출력이 없다 — 로그를 파일로 돌린다."""
    if sys.stdout is None or sys.stderr is None:
        config.LOGS.mkdir(parents=True, exist_ok=True)
        f = open(config.LOGS / "server.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or f
        sys.stderr = sys.stderr or f


def _is_aside(port: int) -> bool:
    import json as _json
    try:
        return _json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=1)).get("app") == "aside"
    except Exception:
        return False


def child_python() -> str:
    """하위 작업은 python.exe 로 — pythonw 는 출력이 없어 로그 창이 빈다(창은 CREATE_NO_WINDOW 로 숨김)."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and (exe.parent / "python.exe").exists():
        return str(exe.parent / "python.exe")
    return sys.executable


def serve(window: bool = True) -> None:
    import uvicorn
    from ..threads import port_open
    _quiet_stdio()
    want = int(os.environ.get("ASIDE_PORT") or config.load()["ui"]["port"])
    # ★ 이런 도구를 여러 개 쓰는 PC 는 포트가 자주 겹친다. 5291 부터 위로 보면서
    #   · aside 가 이미 켜져 있으면 → 패널만 다시 열고 끝(서버 둘이 예약을 두 번 올리면 안 된다)
    #   · 다른 프로그램이 쓰고 있으면 → 다음 번호로
    port = want
    for cand in range(want, want + 20):
        if not port_open(cand):
            port = cand
            break
        if _is_aside(cand):
            log(f"이미 켜져 있어요. 패널을 다시 열어요. (주소 http://127.0.0.1:{cand}/)")
            if window:
                _open_panel(cand)
            return
    else:
        raise SystemExit(f"{want}~{want + 19} 포트가 모두 쓰이고 있어요. 다른 프로그램을 몇 개 닫고 다시 켜 주세요.")
    if port != want:
        log(f"{want} 번은 다른 프로그램이 쓰고 있어서 {port} 번으로 켰어요.")
    data = config.local()
    data["ui_port"] = port
    config.save_local(data)
    threading.Thread(target=_scheduler, daemon=True).start()
    if window:
        threading.Timer(1.2, _open_panel, args=(port,)).start()
    log(f"aside 가 켜졌어요. 이 검은 창을 닫으면 예약이 멈춰요 (최소화는 괜찮아요). 패널 주소: http://127.0.0.1:{port}/")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", log_config=None)
