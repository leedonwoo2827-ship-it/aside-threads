"""python -m aside <명령> …

만들기
  new <job> (--from <원고폴더> | --files a.html b.html …) [--title 시리즈] [--topic ADsP] [--handle @id]
  make      --job J [--only id,…] [--force] [--limit N]    s1→s2→s3→s4 한 번에
  s1-copy | s2-imgjson | s3-images | s4-render   (단계별, 옵션 같음)
올리기
  account add <이름> [--label @id] | account list | account use <이름> | account rm <이름>
  login   [--account A]                 왼쪽 Chrome 에서 Threads 로그인
  post    --job J --only id [--account A] [--at "2026-10-04 08:30"] [--dry-run]
  plan    --job J [--account A] [--apply]       남은 게시물을 다음 빈 슬롯에 예약(미리보기/적용)
  queue                                 시각이 된 대기열 게시(서버가 30초마다 부른다)
  probe   [--account A]                 작성창 구조 떠 두기(셀렉터 맞출 때)
기타
  ui        패널 서버 + 오른쪽 도킹 창
  fonts     Pretendard 글꼴 받기
  doctor    Codex 로그인·Chrome·글꼴 점검
"""
from __future__ import annotations

import argparse
import shutil
import sys
import urllib.request
from typing import List, Optional

from . import accounts, config, schedule
from .job import Job, all_jobs, need
from .log import detail, log, usage

STAGES = ["s1-copy", "s2-imgjson", "s3-images", "s4-render"]
# 로그 창에 보이는 단계 이름 — 쉬운 말로 (팀원이 읽는다)
STEP_NAMES = {"s1-copy": "1/4 글 쓰기", "s2-imgjson": "2/4 그림 설명 준비",
              "s3-images": "3/4 그림 그리기", "s4-render": "4/4 카드 만들기"}
FONT_URL = "https://cdn.jsdelivr.net/npm/pretendard@1.3.9/dist/web/static/woff2/Pretendard-{w}.woff2"


def _t(s) -> str:
    """게시물 이름(번호 뺀 제목)."""
    import re
    return re.sub(r"^\s*[\d.]+\s*", "", s.title).strip()


def _only(v: Optional[str]) -> Optional[List[str]]:
    return [x.strip() for x in v.split(",") if x.strip()] if v else None


def run_stage(name: str, job: Job, **kw) -> None:
    from . import s1_copy, s2_imgjson, s3_images, s4_render
    mod = {"s1-copy": s1_copy, "s2-imgjson": s2_imgjson,
           "s3-images": s3_images, "s4-render": s4_render}[name]
    log(f"── {STEP_NAMES.get(name, name)}")
    detail(f"stage {name} [{job.name}]")
    mod.run(job, **kw)


def cmd_new(a) -> None:
    from pathlib import Path
    if a.files:
        files = sorted(Path(f) for f in a.files)
        missing = [str(f) for f in files if not f.is_file()]
        if missing:
            raise SystemExit("파일이 없습니다: " + ", ".join(missing))
    elif a.src:
        files = sorted(Path(a.src).glob("*.html"))
    else:
        raise SystemExit("--from <원고폴더> 또는 --files <파일…> 중 하나를 주세요")
    if not files:
        raise SystemExit(f"원고 HTML 이 없습니다: {a.src}")
    job = Job(a.name)
    job.input.mkdir(parents=True, exist_ok=True)
    for f in files:
        shutil.copy2(f, job.input / f.name)
    meta = config.read_json(job.dir / "job.json", {}) or {}
    meta.update({k: v for k, v in {"title": a.title, "topic": a.topic, "handle": a.handle,
                                   "chip": a.chip}.items() if v})
    meta.setdefault("title", a.name)
    meta.setdefault("tone", "친근한 해요체")
    config.write_json(job.dir / "job.json", meta)
    n = len(Job(a.name).slides)
    log(f"원고 {len(files)}개를 불러왔어요. 게시물 {n}개가 만들어져요.")


def cmd_fonts(_a=None) -> None:
    d = config.TEMPLATES / "fonts"
    d.mkdir(parents=True, exist_ok=True)
    for w in ("Regular", "Medium", "Bold", "ExtraBold"):
        p = d / f"Pretendard-{w}.woff2"
        if p.exists():
            continue
        log(f"  글꼴 받는 중 ({w})")
        urllib.request.urlretrieve(FONT_URL.format(w=w), p)
    log("글꼴 준비 완료")


def cmd_doctor(_a=None) -> int:
    bad = 0
    from .llm import codex_transport as t
    from .llm.codex_provider import CodexProvider
    if not t.AUTH_PATH.exists():
        log("✗ Codex 로그인이 필요해요 — 터미널에서 codex login 을 실행해 주세요")
        bad += 1
    else:
        try:
            from .llm import models
            m = models.resolve()
            ok, msg = CodexProvider(model=m, effort="low").ping()
            msg = f"{m} · {msg}"
            log("✓ Codex 연결 정상" + (f" (남은 사용량 {usage().split()[-1]})" if usage() else "") if ok
                else "✗ Codex 연결이 안 돼요 — 터미널에서 codex login 을 다시 해 주세요")
            detail(f"doctor codex: {msg}  {t.limits_line()}")
            bad += 0 if ok else 1
        except Exception as e:
            log("✗ Codex 연결이 안 돼요 — 터미널에서 codex login 을 다시 해 주세요")
            detail(f"doctor codex 실패: {e}")
            bad += 1
    try:
        from .threads import chrome_path
        detail(f"Chrome: {chrome_path()}")
        log("✓ Chrome 확인")
    except SystemExit as e:
        log(f"✗ {e}")
        bad += 1
    fonts = config.TEMPLATES / "fonts" / "Pretendard-Bold.woff2"
    log(("✓ 글꼴 확인" if fonts.exists() else "△ 글꼴이 없어요 — setup 을 다시 실행해 주세요"))
    accs = accounts.all_()
    log(f"✓ Threads 계정 {len(accs)}개" if accs else "△ Threads 계정이 아직 없어요 — 패널 위쪽 ＋ 로 추가해 주세요")
    return bad


def cmd_account(a) -> None:
    if a.action == "add":
        acc = accounts.add(a.name, a.label or "")
        log(f"계정 추가: {acc['name']} (포트 {acc['port']}) — 이제 `python -m aside login --account {acc['name']}`")
    elif a.action == "rm":
        accounts.remove(a.name)
        log(f"계정 목록에서 뺐습니다: {a.name} (profiles/{a.name}/ 폴더는 남겨 둠)")
    elif a.action == "use":
        accounts.set_current(a.name)
        log(f"기본 계정: {a.name}")
    else:
        cur = config.local().get("current")
        for acc in accounts.all_():
            log(f"{'*' if acc['name'] == cur else ' '} {acc['name']:16} {acc.get('label', '')}  port {acc['port']}")


def cmd_post(a) -> None:
    from . import threads
    job = need(a.job)
    acc = accounts.get(a.account)
    cfg = config.load()["threads"]
    targets = job.pick(_only(a.only)) if a.only else []
    if not targets:
        raise SystemExit("--only 로 게시물 하나를 고르세요")
    for s in targets:
        st = job.state().get(s.data_id) or {}
        if st.get("posted") and not a.force:
            log(f"· 「{_t(s)}」은 이미 올렸어요.")
            continue
        post = job.post(s.data_id)
        cards = job.card_files(s.data_id)
        if not post or not cards:
            raise SystemExit(f"{s.data_id}: 문구나 카드가 없습니다 — 먼저 make")
        when = schedule.parse_when(a.at) if a.at else None
        if when and not cfg.get("native_schedule") and not a.native:
            schedule.enqueue(job, s.data_id, when, acc["name"], "queue")
            log(f"⏰ 「{_t(s)}」을 {when:%m월 %d일 %H:%M} 에 올리도록 예약했어요. (이 프로그램이 켜져 있어야 올라가요)")
            continue
        log(f"▶ 「{_t(s)}」 " + ("미리 채워 보기" if a.dry_run else "예약 넣기" if when else "올리기") + f" — 카드 {len(cards)}장")
        detail(f"post {s.data_id} account={acc['name']} when={when} dry={a.dry_run}")
        try:
            res = threads.publish(acc, post, cards, topic=job.get("topic", ""), when=when, dry_run=a.dry_run)
        except threads.ScheduleUnsupported as e:
            log("✗ Threads 자체 예약을 쓰지 못했어요. 이 프로그램의 예약(기본)으로 다시 걸어 주세요.")
            detail(f"native schedule 실패: {e}")
            job.update_state(s.data_id, {"error": str(e)[:300]})
            raise SystemExit(2)
        except threads.PostError as e:
            job.update_state(s.data_id, {"error": str(e)[:300]})
            detail(f"post 실패 {s.data_id}: {e}  (스크린샷: logs/post/)")
            raise SystemExit(f"✗ 올리지 못했어요: {e}")
        if res.get("dry_run"):
            continue
        rec = {"account": acc["name"], **res}
        if when:
            job.update_state(s.data_id, {"scheduled": {"when": when.strftime(schedule.FMT),
                                                       "account": acc["name"], "mode": "native"},
                                         "native": rec, "error": None})
            log(f"✓ Threads 에 예약했어요 ({when:%m월 %d일 %H:%M})")
        else:
            job.update_state(s.data_id, {"posted": rec, "error": None})
            log("✓ 다 올렸어요! 왼쪽 Threads 창에서 확인해 보세요.")


def cmd_plan(a) -> None:
    job = need(a.job)
    acc = accounts.get(a.account)
    st = job.state()
    left = [s for s in job.slides
            if not (st.get(s.data_id) or {}).get("posted") and not (st.get(s.data_id) or {}).get("scheduled")
            and job.card_files(s.data_id)]
    slots = schedule.next_slots(len(left), acc["name"])
    for s, when in zip(left, slots):
        log(f"  {when:%m-%d(%a) %H:%M}  {s.data_id}  {s.title}")
        if a.apply:
            schedule.enqueue(job, s.data_id, when, acc["name"])
    log(("예약했습니다" if a.apply else "미리보기입니다 — 적용하려면 --apply") + f" ({len(left)}건, 계정 {acc['name']})")


def cmd_queue(_a=None) -> None:
    items = schedule.due()
    if not items:
        return
    it = items[0]       # 한 번에 하나 — 몰아올리지 않는다
    log("⏰ 예약 시간이 되어 올려요")
    detail(f"queue due: {it['job']}/{it['data_id']} ({it['account']})")
    ns = argparse.Namespace(job=it["job"], only=it["data_id"], account=it["account"], at=None,
                            dry_run=False, force=False, native=False)
    cmd_post(ns)


def main(argv: Optional[List[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(prog="aside", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("new")
    p.add_argument("name")
    p.add_argument("--from", dest="src", help="원고 폴더 (안의 *.html 전부)")
    p.add_argument("--files", nargs="+", help="원고 파일을 하나씩 지정")
    p.add_argument("--title")
    p.add_argument("--topic")
    p.add_argument("--handle")
    p.add_argument("--chip")

    for name in ["make", *STAGES]:
        p = sub.add_parser(name)
        p.add_argument("--job")
        p.add_argument("--only")
        p.add_argument("--force", action="store_true")
        p.add_argument("--limit", type=int, default=0, help="s3: 이번에 구울 최대 장수")
        p.add_argument("--skip-images", action="store_true", help="make: 그림 굽기 건너뛰기")

    p = sub.add_parser("account")
    p.add_argument("action", choices=["add", "list", "use", "rm"])
    p.add_argument("name", nargs="?")
    p.add_argument("--label")

    for name in ("login", "probe"):
        p = sub.add_parser(name)
        p.add_argument("--account")

    p = sub.add_parser("post")
    p.add_argument("--job")
    p.add_argument("--only")
    p.add_argument("--account")
    p.add_argument("--at")
    p.add_argument("--native", action="store_true", help="Threads 자체 예약으로 넘기기")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true")

    p = sub.add_parser("plan")
    p.add_argument("--job")
    p.add_argument("--account")
    p.add_argument("--apply", action="store_true")

    sub.add_parser("queue")
    p = sub.add_parser("ui")
    p.add_argument("--no-window", action="store_true")
    sub.add_parser("fonts")
    sub.add_parser("doctor")

    a = ap.parse_args(argv)
    try:
        return _dispatch(a, ap)
    except SystemExit:
        raise
    except Exception as e:      # noqa: BLE001 — 팀원 화면에는 쉬운 말, 자세한 건 기록 파일로
        import traceback
        detail(traceback.format_exc())
        raise SystemExit(friendly(e))


def friendly(e: Exception) -> str:
    """기술 오류 → 쉬운 말 한 줄. 원문은 logs/detail.log 에."""
    from .llm.errors import NotAuthenticated, QuotaExceeded
    from .llm.codex_transport import CodexAuthError
    if isinstance(e, (NotAuthenticated, CodexAuthError)):
        return "✗ Codex 로그인이 풀렸어요. 터미널에서 codex login 을 다시 한 뒤 같은 버튼을 눌러 주세요."
    if isinstance(e, QuotaExceeded):
        return "✗ 오늘 쓸 수 있는 양을 다 썼어요. 나중에 같은 버튼을 누르면 남은 것만 이어서 해요."
    msg = str(e).lower()
    if any(w in msg for w in ("timed out", "timeout", "connection", "network", "urlopen")):
        return "✗ 인터넷 연결이 불안정해요. 잠시 뒤 같은 버튼을 다시 눌러 주세요."
    return "✗ 문제가 생겨서 멈췄어요. 같은 버튼을 한 번 더 눌러 보시고, 또 그러면 담당자에게 logs/detail.log 를 보내 주세요."


def _dispatch(a, ap) -> int:
    if a.cmd == "new":
        cmd_new(a)
    elif a.cmd in STAGES or a.cmd == "make":
        job = need(a.job)
        kw = dict(only=_only(a.only), force=a.force, limit=a.limit)
        for st in (STAGES if a.cmd == "make" else [a.cmd]):
            if a.cmd == "make" and st == "s3-images" and a.skip_images:
                continue
            run_stage(st, job, **kw)
    elif a.cmd == "account":
        if a.action in ("add", "use", "rm") and not a.name:
            raise SystemExit("계정 이름을 주세요")
        cmd_account(a)
    elif a.cmd == "login":
        from . import threads
        threads.open_login(accounts.get(a.account))
    elif a.cmd == "probe":
        from . import threads
        threads.probe(accounts.get(a.account))
    elif a.cmd == "post":
        cmd_post(a)
    elif a.cmd == "plan":
        cmd_plan(a)
    elif a.cmd == "queue":
        cmd_queue()
    elif a.cmd == "ui":
        from .web import server
        server.serve(window=not a.no_window)
    elif a.cmd == "fonts":
        cmd_fonts()
    elif a.cmd == "doctor":
        return 1 if cmd_doctor() else 0
    else:
        ap.print_help()
    return 0
