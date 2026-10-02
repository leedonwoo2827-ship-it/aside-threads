"""Threads 게시 — 실제 Chrome(전용 프로필) + CDP 로 붙는다. lecture-composer `s6_upload` 와 같은 방식.

왜 Playwright 가 띄운 브라우저가 아닌가: Meta·Google 은 자동화 브라우저 로그인을 자주 막는다.
그래서 **사람이 그 Chrome 에서 한 번 로그인**하고, 이후로는 같은 프로필에 CDP 로 붙어서 조종한다.
그 Chrome 창이 화면 왼쪽에 떠 있고, 오른쪽에 aside 패널이 붙는다 — 게시가 눈앞에서 진행된다.

Threads 화면이 바뀌면 **SEL 표만 고친다.** `python -m aside probe` 가 작성창의 구조(aria)와
스크린샷을 logs/probe/ 에 떨궈 주므로 그것을 보고 고친다.
"""
from __future__ import annotations

import ctypes
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import accounts, config
from .log import detail, log

HOME = "https://www.threads.com/"
INTENT = "https://www.threads.com/intent/post?text="

# ── 셀렉터 — 화면이 바뀌면 여기만 고친다 ───────────────────────────────────
SEL: Dict[str, Any] = {
    "dialog": '[role="dialog"]',
    "editor": '[role="dialog"] [contenteditable="true"]',
    "file": '[role="dialog"] input[type="file"]',
    "add_thread": re.compile(r"^(스레드에 추가|Add to thread)$"),
    "post": re.compile(r"^(게시|Post)$"),
    "schedule_btn": re.compile(r"^(예약|Schedule)$"),
    "more": re.compile(r"^(더 보기|More)$"),
    "schedule_item": re.compile(r"(예약|Schedule)"),
    "done": re.compile(r"^(완료|Done)$"),
    "view": re.compile(r"^(보기|View)$"),
    "login_hint": re.compile(r"(로그인|Log in|Instagram으로 계속|Continue with Instagram)"),
}
SESSION_COOKIES = ("sessionid", "ds_user_id")


class PostError(RuntimeError):
    pass


class ScheduleUnsupported(PostError):
    """Threads 예약 UI 를 못 찾음 — 호출부가 내장 대기열로 넘긴다."""


# ── Chrome ──────────────────────────────────────────────────────────────────
def chrome_path() -> str:
    """Windows · macOS · Linux 의 Google Chrome. 설정 threads.chrome 이 있으면 그것이 이긴다.

    Chromium 도 받지만 **Google Chrome 을 권한다** — 로그인 차단이 덜하다(lecture-composer 와 같은 이유)."""
    cfg = config.load()["threads"]
    cands = [cfg.get("chrome") or ""]
    if sys.platform.startswith("win"):
        cands += [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                  r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                  str(Path.home() / r"AppData\Local\Google\Chrome\Application\chrome.exe")]
    elif sys.platform == "darwin":
        cands += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                  str(Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                  "/Applications/Chromium.app/Contents/MacOS/Chromium"]
    else:
        cands += [shutil.which(n) or "" for n in
                  ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")]
    for c in cands:
        if c and Path(c).exists():
            return c
    raise SystemExit("Chrome 을 찾지 못했습니다 — Google Chrome 을 설치하거나 "
                     "aside.config.local.json 의 threads.chrome 에 실행 파일 경로를 적으세요")


_SCREEN: Optional[tuple] = None


def screen() -> tuple:
    """화면 크기(가로, 세로) — 왼쪽 Threads 창과 오른쪽 패널을 나란히 놓는 데 쓴다."""
    global _SCREEN
    if _SCREEN:
        return _SCREEN
    try:
        if sys.platform.startswith("win"):
            u = ctypes.windll.user32
            u.SetProcessDPIAware()
            _SCREEN = (u.GetSystemMetrics(0), u.GetSystemMetrics(1))
        else:
            # macOS·Linux: tkinter 는 메인 스레드를 가리므로 별도 프로세스로 묻는다
            r = subprocess.run([sys.executable, "-c",
                                "import tkinter as t;r=t.Tk();r.withdraw();"
                                "print(r.winfo_screenwidth(), r.winfo_screenheight())"],
                               capture_output=True, text=True, timeout=10)
            w, h = map(int, r.stdout.split())
            _SCREEN = (w, h)
    except Exception:
        _SCREEN = (1440, 900) if sys.platform == "darwin" else (1920, 1080)
    return _SCREEN


def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def ensure_chrome(acc: Dict[str, Any], url: str = HOME) -> bool:
    """그 계정의 Chrome 을 왼쪽에 띄운다. 이미 떠 있으면 그대로 쓴다. 새로 띄웠으면 True."""
    port = int(acc["port"])
    if port_open(port):
        return False
    sw, sh = screen()
    panel = int(config.load()["ui"]["width"])
    subprocess.Popen([
        chrome_path(), f"--user-data-dir={accounts.profile(acc)}",
        f"--remote-debugging-port={port}", "--no-first-run", "--no-default-browser-check",
        "--window-position=0,0", f"--window-size={max(900, sw - panel)},{sh - 40}", url])
    if not wait_port(port):
        raise SystemExit("Chrome 디버그 포트가 열리지 않습니다 — 같은 프로필 Chrome 이 이미 떠 있으면 닫고 다시")
    try:
        place(port, "left")
    except Exception as e:      # noqa: BLE001 — 배치는 보기 좋으라고 하는 일이다
        detail(f"  (창 배치 실패, 무시: {e})")
    return True


def wait_port(port: int, tries: int = 60) -> bool:
    for _ in range(tries):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1)
            return True
        except Exception:
            time.sleep(0.5)
    return False


PANEL_PORT_OFFSET = -1      # 패널 Chrome 의 디버그 포트 = base_port - 1


def panel_port() -> int:
    return int(config.load()["threads"]["base_port"]) + PANEL_PORT_OFFSET


def place(port: int, side: str) -> None:
    """창을 화면 왼쪽(Threads) 또는 오른쪽(패널)에 **정확히** 붙인다.

    ★ 처음에는 실행 인자(--window-position/size)로만 놓았는데, 그 좌표는 Windows 배율(125%·150%)과
      모니터 구성에 따라 Chrome 이 다르게 읽는다 — 패널이 화면 밖으로 밀리고 가운데가 비었다(2026-10-03).
      그래서 창이 뜬 뒤 **Chrome 에게 직접** 쓸 수 있는 화면 영역(screen.avail*)을 묻고,
      같은 단위로 CDP `Browser.setWindowBounds` 를 건다. 배율·OS 와 무관하게 맞는다."""
    from playwright.sync_api import sync_playwright

    width = int(config.load()["ui"]["width"])
    with sync_playwright() as pw:
        br = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        if True:      # with 블록을 나가면 CDP 연결만 끊긴다 — 사람이 보는 창은 그대로 남는다
            ctx = br.contexts[0]
            page = next((p for p in ctx.pages if not p.url.startswith("devtools")), None) or ctx.new_page()
            ax, ay, aw, ah = page.evaluate(
                "[screen.availLeft || 0, screen.availTop || 0, screen.availWidth, screen.availHeight]")
            cdp = ctx.new_cdp_session(page)
            wid = cdp.send("Browser.getWindowForTarget")["windowId"]
            cdp.send("Browser.setWindowBounds", {"windowId": wid, "bounds": {"windowState": "normal"}})
            if side == "right":
                b = {"left": ax + aw - width, "top": ay, "width": width, "height": ah}
            else:
                b = {"left": ax, "top": ay, "width": max(700, aw - width), "height": ah}
            cdp.send("Browser.setWindowBounds", {"windowId": wid, "bounds": b})
            cdp.detach()


def arrange(acc: Optional[Dict[str, Any]] = None) -> List[str]:
    """떠 있는 창들을 다시 붙인다 — 패널의 「창 정렬」."""
    done = []
    if port_open(panel_port()):
        place(panel_port(), "right")
        done.append("패널")
    if acc and port_open(int(acc["port"])):
        place(int(acc["port"]), "left")
        done.append(acc["name"])
    return done


class Session:
    """with Session(acc) as s: s.page …  — 끝나도 Chrome 은 닫지 않는다(사람이 계속 본다)."""

    def __init__(self, acc: Dict[str, Any]):
        self.acc = acc

    def __enter__(self):
        from playwright.sync_api import sync_playwright

        ensure_chrome(self.acc)
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.connect_over_cdp(f"http://127.0.0.1:{self.acc['port']}")
        self.ctx = self.browser.contexts[0] if self.browser.contexts else self.browser.new_context()
        pages = [p for p in self.ctx.pages if "threads." in p.url]
        self.page = pages[0] if pages else (self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page())
        self.page.bring_to_front()
        return self

    def __exit__(self, *exc):
        try:
            self._pw.stop()     # CDP 연결만 끊는다 — 창은 남는다
        except Exception:
            pass

    def logged_in(self) -> bool:
        names = {c["name"] for c in self.ctx.cookies(["https://www.threads.com", "https://www.threads.net"])}
        return any(n in names for n in SESSION_COOKIES)

    def shot(self, name: str) -> Path:
        d = config.LOGS / "post"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{time.strftime('%m%d-%H%M%S')}-{name}.png"
        try:
            self.page.screenshot(path=str(p))
        except Exception:
            pass
        return p


# ── 동작 ────────────────────────────────────────────────────────────────────
def open_login(acc: Dict[str, Any]) -> None:
    fresh = ensure_chrome(acc, HOME + "login")
    if not fresh:
        with Session(acc) as s:
            s.page.goto(HOME + "login")
    log("왼쪽 Chrome 창에서 Threads 에 로그인해 주세요 (「Instagram으로 계속하기」). 창은 닫지 않아도 돼요.")


def status(acc: Dict[str, Any]) -> Dict[str, Any]:
    if not port_open(int(acc["port"])):
        return {"chrome": False, "logged_in": None}
    with Session(acc) as s:
        return {"chrome": True, "logged_in": s.logged_in()}


def _dialog(page):
    return page.locator(SEL["dialog"]).filter(has=page.locator('[contenteditable="true"]')).last


def _open_composer(s: Session, text: str):
    """intent URL 로 작성창을 연다 — 본문이 미리 채워져 와서 입력 흉내보다 튼튼하다."""
    page = s.page
    page.goto(INTENT + urllib.parse.quote(text), wait_until="domcontentloaded")
    if "/login" in page.url or not s.logged_in():
        s.shot("not-logged-in")
        raise PostError("로그인이 안 되어 있습니다 — 패널의 「로그인」으로 이 계정 Chrome 에서 로그인하세요")
    dlg = _dialog(page)
    dlg.wait_for(state="visible", timeout=30_000)
    ed = dlg.locator('[contenteditable="true"]').first
    ed.wait_for(state="visible", timeout=15_000)
    # 미리 채워지지 않았으면 직접 넣는다
    if len(ed.inner_text().strip()) < min(10, len(text.strip())):
        ed.click()
        page.keyboard.insert_text(text)
    return dlg


def _attach(s: Session, dlg, files: List[Path]) -> None:
    if not files:
        return
    inp = dlg.locator('input[type="file"]').first
    try:
        inp.wait_for(state="attached", timeout=15_000)
    except Exception:
        inp = s.page.locator('input[type="file"]').first
    inp.set_input_files([str(f) for f in files])
    # 미리보기 썸네일이 다 붙을 때까지
    deadline = time.time() + 60 + 5 * len(files)
    while time.time() < deadline:
        if dlg.locator("img[src^='blob:'], video").count() >= len(files):
            break
        time.sleep(1)
    time.sleep(1.5)


def _add_reply(s: Session, dlg, reply: str) -> None:
    if not reply.strip():
        return
    before = dlg.locator('[contenteditable="true"]').count()
    btn = dlg.get_by_text(SEL["add_thread"]).last
    btn.click(timeout=10_000)
    for _ in range(20):
        if dlg.locator('[contenteditable="true"]').count() > before:
            break
        time.sleep(0.3)
    ed = dlg.locator('[contenteditable="true"]').last
    ed.click()
    s.page.keyboard.insert_text(reply)
    time.sleep(0.6)


def _set_schedule(s: Session, dlg, when: datetime) -> None:
    """Threads 웹 작성창의 「…」→「예약」. 화면이 확정되지 않아 단계마다 찍고, 못 찾으면 ScheduleUnsupported."""
    page = s.page
    try:
        more = dlg.get_by_role("button", name=SEL["more"]).last
        more.click(timeout=8_000)
        page.get_by_role("menuitem", name=SEL["schedule_item"]).or_(
            page.get_by_text(SEL["schedule_item"])).first.click(timeout=8_000)
        s.shot("schedule-picker")
        # 날짜·시각 칸: input[type=date|time] 이거나 spinbutton 들이다
        picker = page.locator(SEL["dialog"]).last
        date_in = picker.locator('input[type="date"]')
        time_in = picker.locator('input[type="time"]')
        if date_in.count() and time_in.count():
            date_in.first.fill(when.strftime("%Y-%m-%d"))
            time_in.first.fill(when.strftime("%H:%M"))
        else:
            spins = picker.get_by_role("spinbutton")
            if spins.count() < 2:
                raise ScheduleUnsupported("예약 날짜·시각 칸을 찾지 못했습니다")
            # 흔한 배열: [월, 일, 년, 시, 분] 또는 [시, 분]
            vals = ([when.strftime("%m"), when.strftime("%d"), when.strftime("%Y")] if spins.count() >= 5 else []) \
                + [when.strftime("%I" if spins.count() in (3, 6) else "%H"), when.strftime("%M")]
            for i, v in enumerate(vals[:spins.count()]):
                spins.nth(i).click()
                page.keyboard.type(v)
            if spins.count() in (3, 6):
                page.keyboard.type("AM" if when.hour < 12 else "PM")
        picker.get_by_role("button", name=SEL["done"]).last.click(timeout=8_000)
        s.shot("schedule-set")
    except ScheduleUnsupported:
        raise
    except Exception as e:
        s.shot("schedule-fail")
        page.keyboard.press("Escape")
        raise ScheduleUnsupported(f"Threads 예약 화면을 다루지 못했습니다: {str(e)[:160]}")


def _submit(s: Session, dlg, scheduled: bool) -> Optional[str]:
    name = SEL["schedule_btn"] if scheduled else SEL["post"]
    btn = dlg.get_by_role("button", name=name).last
    btn.wait_for(state="visible", timeout=20_000)
    for _ in range(60):     # 미디어 업로드 중에는 비활성
        if btn.get_attribute("aria-disabled") not in ("true",) and btn.is_enabled():
            break
        time.sleep(1)
    btn.click()
    try:
        dlg.wait_for(state="detached", timeout=180_000)
    except Exception:
        s.shot("submit-stuck")
        raise PostError("게시 버튼을 눌렀는데 작성창이 닫히지 않습니다 — 스크린샷 확인")
    url = None
    try:
        view = s.page.get_by_role("link", name=SEL["view"]).first
        view.wait_for(timeout=15_000)
        href = view.get_attribute("href") or ""
        url = urllib.parse.urljoin(HOME, href) if href else None
    except Exception:
        pass
    s.shot("posted")
    return url


def compose_text(post: Dict[str, Any], topic: str) -> str:
    text = (post.get("post") or "").strip()
    if topic and f"#{topic}" not in text:
        text += f"\n\n#{topic}"
    return text[:500]


def publish(acc: Dict[str, Any], post: Dict[str, Any], cards: List[Path], *, topic: str = "",
            when: Optional[datetime] = None, dry_run: bool = False) -> Dict[str, Any]:
    """게시물 하나(본문+카드+이어쓰기 답글). when 이 있으면 Threads 예약."""
    text = compose_text(post, topic)
    with Session(acc) as s:
        for attempt in (1, 2):
            try:
                log("  ① 글쓰기 창을 여는 중")
                dlg = _open_composer(s, text)
                log(f"  ② 카드 {len(cards)}장을 붙이는 중 …")
                _attach(s, dlg, cards)
                if (post.get("reply") or "").strip():
                    log("  ③ 답글을 넣는 중")
                    _add_reply(s, dlg, post.get("reply", ""))
                if when:
                    log(f"  ④ 예약 시간을 넣는 중 ({when:%m월 %d일 %H:%M})")
                    _set_schedule(s, dlg, when)
                s.shot("ready")
                log("  ⑤ 준비 끝")
                if dry_run:
                    log("✓ 미리 채워 두었어요. 왼쪽 창에서 확인해 보세요. 아직 올리지 않았어요 (확인 후 창의 X 로 닫으면 돼요).")
                    return {"dry_run": True}
                log("  ⑥ " + ("예약하는" if when else "올리는") + " 중 … (카드가 올라가는 데 조금 걸려요)")
                url = _submit(s, dlg, scheduled=bool(when))
                log("  ⑦ 끝!")
                detail(f"  posted url={url}")
                return {"url": url, "at": datetime.now().isoformat(timespec="seconds"),
                        "scheduled_for": when.isoformat(timespec="minutes") if when else None}
            except ScheduleUnsupported:
                raise
            except Exception as e:
                s.shot(f"error{attempt}")
                if attempt == 2:
                    raise PostError(str(e)[:300])
                log("  … 잘 안 돼서 한 번 더 해 볼게요")
                detail(f"  publish attempt {attempt} 실패: {e}")
                s.page.keyboard.press("Escape")
                time.sleep(3)
    return {}


def probe(acc: Dict[str, Any]) -> Path:
    """작성창 구조를 떠 둔다 — 셀렉터를 맞출 때 쓴다. 게시하지 않는다."""
    out = config.LOGS / "probe" / time.strftime("%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    with Session(acc) as s:
        log(f"  로그인: {s.logged_in()}")
        dlg = _open_composer(s, "aside probe — 게시하지 않습니다")
        (out / "dialog.html").write_text(dlg.evaluate("e => e.outerHTML"), encoding="utf-8")
        try:
            (out / "dialog.aria.yml").write_text(dlg.aria_snapshot(), encoding="utf-8")
        except Exception as e:
            log(f"  aria 스냅샷 실패: {e}")
        s.page.screenshot(path=str(out / "1-composer.png"))
        try:
            dlg.get_by_role("button", name=SEL["more"]).last.click(timeout=5_000)
            time.sleep(1)
            s.page.screenshot(path=str(out / "2-more-menu.png"))
            (out / "page.aria.yml").write_text(s.page.locator("body").aria_snapshot(), encoding="utf-8")
            s.page.keyboard.press("Escape")
        except Exception as e:
            log(f"  「더 보기」 메뉴를 못 찾음: {str(e)[:120]}")
    log(f"  → {out}")
    return out
