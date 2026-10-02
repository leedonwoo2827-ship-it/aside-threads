"""s4-render — 카드뉴스 HTML 을 짜서 Playwright 로 1080×1350 PNG 를 찍는다. 할당량 없음.

한 게시물 = 카드 4~6장:
  01 표지(그림+제목) · 02~03 핵심 · 도식(원고 SVG/표) · 기출 포인트 · 마무리
그림이 아직 없으면 빗금 자리표시로 찍힌다 — 그림 굽기 전에도 문구·배치를 볼 수 있다.
cards/<id>/NN.html 도 남긴다(브라우저로 열어 손볼 때).
"""
from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config
from .job import Job
from .log import detail, log
from .manuscript import Slide, next_of

FONTS = config.TEMPLATES / "fonts"
THEME = config.TEMPLATES / "cards" / "theme.css"


def md(text: str) -> str:
    """escape 후 **굵게** 만 살린다."""
    out = html.escape(text or "")
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)


def plain_title(title: str) -> str:
    return re.sub(r"^\s*[\d.]+\s*", "", title).strip()


def _theme() -> str:
    img = config.load()["image"]
    css = THEME.read_text(encoding="utf-8")
    return (css.replace("{{FONTS}}", FONTS.as_uri()).replace("{{BG}}", img["bg"])
               .replace("{{A}}", img["accent_a"]).replace("{{B}}", img["accent_b"]))


def _img(path: Path, alt: str) -> str:
    if path.exists():
        return f'<img src="{path.as_uri()}" alt="">'
    return f'<div class="wait">{html.escape(alt)}</div>'


def _page(css: str, kind: str, body: str) -> str:
    return (f'<!doctype html><html lang="ko"><head><meta charset="utf-8"><style>{css}</style></head>'
            f'<body><div class="card {kind}">{body}</div></body></html>')


def _meta() -> bool:
    """작업 이름·원고 번호·기출 횟수 같은 **관리용 표시**를 카드에 찍을지. 기본은 안 찍는다 —
    보는 사람에게는 내용만(2026-10-03 사용자 요청). 켜려면 aside.config(.local).json 의 card.show_meta: true"""
    return bool(config.load()["card"].get("show_meta"))


def _chrome(chip: str, i: int, total: int) -> str:
    left = f'<span class="chip"><i></i>{html.escape(chip)}</span>' if _meta() and chip else "<span></span>"
    return f'<div class="top">{left}<span class="page"><b>{i:02d}</b> / {total:02d}</span></div>'


def _foot(handle: str, i: int, total: int, right: str = "") -> str:
    dots = "".join(f'<span class="{"on" if k == i else ""}"></span>' for k in range(1, total + 1))
    left = html.escape(handle) if handle else ""
    return f'<div class="foot"><span>{left}</span><div class="dots">{dots}</div><span>{right}</span></div>'


ICON_SAVE = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" '
             'stroke-linecap="round" stroke-linejoin="round"><path d="M6 3h12v18l-6-4-6 4z"/></svg>')
ICON_SHARE = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" '
              'stroke-linecap="round" stroke-linejoin="round"><path d="M4 12v8h16v-8M12 3v13M7 8l5-5 5 5"/></svg>')
ICON_FOLLOW = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" '
               'stroke-linecap="round" stroke-linejoin="round"><circle cx="10" cy="8" r="4"/>'
               '<path d="M3 21c0-4 3-6 7-6s7 2 7 6M19 8v6M16 11h6"/></svg>')


ICON_CHECK = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" '
              'stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>')


def _art(s: Slide) -> str:
    """그림이 없을 때의 표지 — 자리표시가 아니라 **그대로 나가도 되는 디자인**.
    계정에 따라 Codex 그림이 아예 안 될 수 있다(2026-10-03 실측) — 그래도 카드는 완성돼야 한다."""
    num = f"{s.n:02d}"
    kind = {"svg": "도식", "table": "표"}.get(s.figure_kind, "개념")
    return (f'<div class="art-fb"><span class="o1"></span><span class="o2"></span><span class="o3"></span>'
            f'<div class="num">{num}</div>'
            f'<div class="tagl">{html.escape(s.section or s.subject)}</div>'
            + (f'<div class="tagr">{kind} · {html.escape(s.data_id)}</div>' if _meta() else "") + '</div>')


def build(job: Job, s: Slide, post: Dict[str, Any]) -> List[tuple]:
    """[(kind, body_html)] — 쪽수는 다 모은 뒤 매긴다."""
    imgdir = job.images
    chip = job.get("chip") or job.get("title") or s.subject
    cards: List[tuple] = []

    cov = post.get("cover", {})
    hot = f'<span class="badge-hot">기출 {s.q_count}문항</span>' if s.q_count and _meta() else ""
    cards.append(("cover", lambda i, n: (
        _chrome(chip, i, n)
        + f'<div class="art">{_img(imgdir / f"{s.data_id}-cover.png", "") if (imgdir / f"{s.data_id}-cover.png").exists() else _art(s)}</div>'
        + f'<div class="kicker">{md(cov.get("kicker") or s.section)}</div>'
        + f'<h1>{md(cov.get("title") or plain_title(s.title))}</h1>'
        + f'<div class="sub">{md(cov.get("sub", ""))}</div>'
        + _foot(job.get("handle", ""), i, n, hot))))

    spot = imgdir / f"{s.data_id}-spot.png"
    for k, pt in enumerate(post.get("points", [])):
        lis = "".join(f'<li><span class="no">{j + 1:02d}</span><span>{md(t)}</span></li>'
                      for j, t in enumerate(pt.get("lines", [])))
        with_spot = k == 0 and spot.exists()
        cards.append(("point" + (" has-spot" if with_spot else ""), lambda i, n, pt=pt, lis=lis, with_spot=with_spot: (
            _chrome(chip, i, n)
            + (f'<div class="spot">{_img(spot, "")}</div>' if with_spot else "")
            + f'<div class="label">Point {i - 1}</div><h2>{md(pt.get("head", ""))}</h2>'
            + f'<div class="mid"><ol>{lis}</ol></div>'
            + _foot(job.get("handle", ""), i, n))))

    if s.figure_html:
        cards.append(("diagram", lambda i, n: (
            _chrome(chip, i, n)
            + f'<div class="label">한눈에 보기</div><h2>{html.escape(plain_title(s.title))}</h2>'
            + f'<div class="mid"><div class="panel">{s.figure_html}</div>'
            + (f'<div class="cap">{md(post.get("diagram_caption", ""))}</div>' if post.get("diagram_caption") else "")
            + "</div>"
            + _foot(job.get("handle", ""), i, n))))

    ex = post.get("exam") or {}
    if ex.get("trap"):
        hist = ""
        if s.q_count and _meta():
            detail = s.q_detail if len(s.q_detail) <= 60 else s.q_detail[:58].rsplit("·", 1)[0] + "· …"
            hist = f'<div class="hist">모의고사 <b>{s.q_count}문항</b> 출제 · {html.escape(detail)}</div>'
        cards.append(("exam", lambda i, n: (
            _chrome(chip, i, n)
            + '<div class="label">기출 포인트</div><h2>이렇게 나오면 틀려요</h2>'
            + '<div class="mid">'
            + f'<div class="box trap"><h3>✗ 함정 보기</h3><p>{md(ex["trap"])}</p></div>'
            + f'<div class="box why"><h3>왜 틀렸나</h3><p>{md(ex.get("why", ""))}</p></div>'
            + f'<div class="box tip"><h3>외우는 법</h3><p>{md(ex.get("tip", ""))}</p></div>'
            + hist + "</div>" + _foot(job.get("handle", ""), i, n))))

    nxt = next_of(job.slides, s)
    medal = imgdir / f"{s.data_id}-cover.png"
    cards.append(("outro", lambda i, n: (
        _chrome(chip, i, n)
        + '<div class="mid">'
        + f'<div class="medal">{_img(medal, "") if medal.exists() else '<div class="art-medal">' + ICON_CHECK + '</div>'}</div>'
        + f'<h2>{md(post.get("outro") or "저장해 두고 시험 전에 다시 봐요")}</h2>'
        + (f'<div class="next"><b>다음 편</b>{html.escape(plain_title(nxt.title))}</div>' if nxt else "")
        + f'<div class="cta"><span>{ICON_SAVE}저장</span><span>{ICON_SHARE}공유</span>'
          f'<span>{ICON_FOLLOW}팔로우</span></div></div>'
        + _foot(job.get("handle", ""), i, n))))
    return cards


def render_pages(pages: List[tuple], out_dir: Path, css: str, browser) -> List[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("[0-9][0-9].*"):
        old.unlink()
    page = browser.new_page(viewport={"width": 1080, "height": 1350}, device_scale_factor=1)
    files = []
    total = len(pages)
    for i, (kind, fn) in enumerate(pages, 1):
        doc = _page(css, kind, fn(i, total))
        h = out_dir / f"{i:02d}.html"
        h.write_text(doc, encoding="utf-8")
        page.goto(h.as_uri())
        page.evaluate("document.fonts.ready")
        # 원고 SVG 는 여백이 넉넉한 가로형이라 카드에선 글자가 작다 — 내용 상자에 맞춰 viewBox 를 조인다
        page.evaluate("""() => document.querySelectorAll('.panel svg').forEach(s => {
            try { const b = s.getBBox(), m = 6;
                  if (b.width > 0) s.setAttribute('viewBox', `${b.x - m} ${b.y - m} ${b.width + 2 * m} ${b.height + 2 * m}`);
            } catch (e) {} })""")
        page.wait_for_timeout(120)
        png = out_dir / f"{i:02d}.png"
        page.screenshot(path=str(png), clip={"x": 0, "y": 0, "width": 1080, "height": 1350})
        files.append(png)
    page.close()
    return files


def run(job: Job, only=None, **_) -> None:
    from playwright.sync_api import sync_playwright

    if not (FONTS / "Pretendard-Bold.woff2").exists():
        detail("Pretendard 글꼴 없음 — python -m aside fonts (지금은 기본 글꼴)")
    css = _theme()
    done = 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for s in job.pick(only):
            post = job.post(s.data_id)
            if not post:
                log(f"  · 「{plain_title(s.title)}」은 글이 아직 없어 건너뛰었어요")
                continue
            files = render_pages(build(job, s, post), job.cards / s.data_id, css, browser)
            done += 1
            log(f"  ✓ {plain_title(s.title)} — 카드 {len(files)}장")
        browser.close()
    log(f"카드 완성! 게시물 {done}개 — 목록에서 눌러 확인해 보세요.")
