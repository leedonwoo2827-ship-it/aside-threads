"""원고 HTML 읽기 — h3 하나 = 한 판 = Threads 게시물 하나.

원고 규약(원고 머리 주석 그대로):
  · h3 의 data-id 는 안 바뀌는 이름표 — 그림·카드·게시 기록을 전부 여기에 매단다.
  · h3 의 data-say 는 그 판의 대본(소리). 화면 글(p·li)과 일부러 다르다.
  · 제목 안 .q 배지 = 그 개념을 물은 모의고사 문번. `.q > b` 가 문항 수, `.qn` 이 회차.
  · 몸통은 h3 다음 형제들(p·ul·ol·table·svg)이다. 판마다 표나 그림이 하나씩 있다.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from bs4 import BeautifulSoup, Tag


@dataclass
class Slide:
    n: int                      # 원고 순서(1부터)
    data_id: str
    title: str                  # h3 글(배지 뺀 것)
    say: str                    # data-say
    section: str = ""           # <meta name="section">
    subject: str = ""           # <title> 의 앞부분 (예: 1과목 · 데이터 이해)
    q_count: int = 0
    q_detail: str = ""          # "6회 1번 · 13회 1번"
    paras: List[str] = field(default_factory=list)
    bullets: List[str] = field(default_factory=list)
    figure_html: str = ""       # 원고의 svg 또는 table 원문(카드에 그대로 얹는다)
    figure_kind: str = ""       # svg | table | ""
    figure_label: str = ""      # svg aria-label 또는 표 머리행
    source: str = ""            # 파일 이름

    @property
    def body_hash(self) -> str:
        raw = "\n".join([self.title, self.say, *self.paras, *self.bullets, self.figure_label])
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]

    def brief(self) -> dict:
        """LLM 에 넘기는 것 — 그림 원문은 빼고 뜻만."""
        return {
            "data_id": self.data_id, "section": self.section, "title": self.title,
            "say": self.say, "paras": self.paras, "bullets": self.bullets,
            "figure": f"[{self.figure_kind}] {self.figure_label}" if self.figure_kind else "",
            "exam": f"모의고사 {self.q_count}문항 ({self.q_detail})" if self.q_count else "",
        }


def _text(el: Tag) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()


def _rich(el: Tag) -> str:
    """b/strong 강조를 **…** 로 살린 글 — 카드에서 다시 강조색으로 그린다."""
    parts: List[str] = []
    for node in el.descendants:
        if isinstance(node, str):
            if node.parent.name in ("b", "strong") and node.parent is not el:
                parts.append(f"**{node}**")
            else:
                parts.append(str(node))
    out = re.sub(r"\s+", " ", "".join(parts)).strip()
    return out.replace("****", "")


def parse_file(path: Path, start_n: int = 1) -> List[Slide]:
    soup = BeautifulSoup(Path(path).read_text(encoding="utf-8"), "html.parser")
    meta = soup.find("meta", attrs={"name": "section"})
    section = meta.get("content", "").strip() if meta else ""
    title_tag = soup.find("title")
    subject = ""
    if title_tag:
        bits = [b.strip() for b in title_tag.get_text().split("·")]
        subject = " · ".join(bits[:2])

    slides: List[Slide] = []
    for i, h3 in enumerate(soup.find_all("h3")):
        q = h3.find(class_="q")
        q_count, q_detail = 0, ""
        if q:
            b = q.find("b")
            qn = q.find(class_="qn")
            q_count = int(re.sub(r"\D", "", b.get_text()) or 0) if b else 0
            q_detail = _text(qn) if qn else ""
            q.extract()
        s = Slide(
            n=start_n + i,
            data_id=h3.get("data-id") or f"{Path(path).stem}-{i + 1}",
            title=_text(h3),
            say=(h3.get("data-say") or "").strip(),
            section=section, subject=subject,
            q_count=q_count, q_detail=q_detail, source=Path(path).name,
        )
        for sib in h3.find_next_siblings():
            if sib.name in ("h1", "h2", "h3"):
                break
            if sib.name == "p":
                s.paras.append(_rich(sib))
            elif sib.name in ("ul", "ol"):
                s.bullets += [_rich(li) for li in sib.find_all("li", recursive=False)]
            elif sib.name in ("svg", "table", "figure") and not s.figure_html:
                fig = sib.find(["svg", "table"]) if sib.name == "figure" else sib
                if fig is None:
                    continue
                s.figure_kind = fig.name
                s.figure_html = str(fig)
                if fig.name == "svg":
                    s.figure_label = fig.get("aria-label", "")
                else:
                    head = fig.find("tr")
                    s.figure_label = "표: " + " | ".join(_text(c) for c in head.find_all(["th", "td"])) if head else "표"
        slides.append(s)
    return slides


def parse_dir(folder: Path) -> List[Slide]:
    out: List[Slide] = []
    for f in sorted(Path(folder).glob("*.html")):
        out += parse_file(f, start_n=len(out) + 1)
    seen = set()
    for s in out:
        if s.data_id in seen:
            raise SystemExit(f"data-id 가 겹칩니다: {s.data_id} ({s.source})")
        seen.add(s.data_id)
    return out


def next_of(slides: List[Slide], s: Slide) -> Optional[Slide]:
    i = slides.index(s)
    return slides[i + 1] if i + 1 < len(slides) else None
