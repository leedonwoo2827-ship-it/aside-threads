"""s1-copy — 원고 한 판 → Threads 문구 + 카드 문구 + 그림 장면 (Codex 텍스트 콜, 할당량).

캐시가 곧 이어하기다: posts/<id>.json 의 body_hash 가 원고와 같으면 건너뛴다.
429 로 죽어도 같은 명령을 다시 돌리면 남은 것만 간다.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List

from . import config
from .job import Job
from .llm import codex_transport, models
from .llm.codex_provider import CodexProvider
from .log import detail, log, usage
from .manuscript import Slide

PROMPT = Path(__file__).parent / "prompts" / "thread_copy.md"
BATCH = 5

_STR = {"type": "string"}
SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "data_id": _STR, "hook": _STR, "post": _STR, "reply": _STR,
            "cover": {"type": "object", "properties": {"kicker": _STR, "title": _STR, "sub": _STR},
                      "required": ["kicker", "title", "sub"]},
            "points": {"type": "array", "items": {
                "type": "object",
                "properties": {"head": _STR, "lines": {"type": "array", "items": _STR}},
                "required": ["head", "lines"]}},
            "diagram_caption": _STR,
            "exam": {"type": "object", "properties": {"trap": _STR, "why": _STR, "tip": _STR},
                     "required": ["trap", "why", "tip"]},
            "outro": _STR, "cover_scene": _STR, "spot_scene": _STR,
        },
        "required": ["data_id", "hook", "post", "reply", "cover", "points",
                     "diagram_caption", "exam", "outro", "cover_scene", "spot_scene"],
    }}},
    "required": ["items"],
}

LIMITS = {"post": 450, "reply": 400}   # Threads 는 500자 — 주제태그 몫을 남긴다


def build_brief(job: Job, batch: List[Slide]) -> str:
    head = {
        "series": job.get("title", ""),
        "subject": batch[0].subject,
        "tone": job.get("tone", "친근한 해요체"),
    }
    return ("## 시리즈\n" + json.dumps(head, ensure_ascii=False) +
            "\n\n## 슬라이드\n" + json.dumps([s.brief() for s in batch], ensure_ascii=False, indent=1))


def _clean(item: Dict[str, Any], s: Slide) -> Dict[str, Any]:
    for k, lim in LIMITS.items():
        if len(item.get(k, "")) > lim:
            log(f"  ⚠ 「{s.title}」 글이 길어서 {lim}자로 줄였어요. 게시물 화면에서 다듬어 주세요.")
            item[k] = item[k][:lim].rstrip()
    item["points"] = [p for p in item.get("points", []) if p.get("lines")][:2]
    item["data_id"] = s.data_id
    item["body_hash"] = s.body_hash
    item["made_at"] = time.strftime("%Y-%m-%d %H:%M")
    return item


def run(job: Job, only=None, force: bool = False, **_) -> None:
    cfg = config.load()
    todo = []
    for s in job.pick(only):
        old = job.raw_post(s.data_id)
        if not force and old and old.get("body_hash") == s.body_hash:
            continue
        todo.append(s)
    if not todo:
        log("글은 이미 다 써 두었어요.")
        return
    log(f"글 {len(todo)}개를 쓰는 중이에요 (보통 1~2분 걸려요) …")
    model = models.resolve()
    models.apply(model)
    p = CodexProvider(model=model, effort=cfg["effort"]["copy"],
                      on_activity=lambda t: detail(f"    … {t}"))
    system = PROMPT.read_text(encoding="utf-8")
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        detail(f"  [{i // BATCH + 1}/{(len(todo) + BATCH - 1) // BATCH}] {batch[0].data_id} … {batch[-1].data_id} · {model}")
        raw = p.structured(system, [{"role": "user", "content": build_brief(job, batch)}], schema=SCHEMA)
        got = {it.get("data_id"): it for it in raw.get("items", [])}
        for s in batch:
            item = got.get(s.data_id)
            if not item:
                log(f"  ⚠ 「{s.title}」 글을 받지 못했어요 — 한 번 더 누르면 이것만 다시 써요")
                continue
            config.write_json(job.posts / f"{s.data_id}.json", _clean(item, s))
            log(f"  ✓ {s.title} — {item['hook']}")
    detail(codex_transport.limits_line())
    if usage():
        log(usage())
