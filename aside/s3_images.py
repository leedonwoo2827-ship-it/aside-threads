"""s3-images — 이미지프롬프트.json 을 Codex(ChatGPT 구독)로 굽는다. 할당량을 가장 많이 쓰는 단계.

backplate `s3c` + imgstudio `batch_jobs` 의 규칙만 가져왔다(SQLAlchemy 갤러리는 안 가져옴):
  · 있는 파일은 건너뛴다 = 이어하기. 그래서 **두세 장만 먼저 굽고(--limit 3) 눈으로 본 뒤** 나머지.
  · 워커 3. 토큰 갱신은 `refresh_once` 가 직렬로 한다(동시 갱신 → 401 사고).
  · NotAuthenticated(token_revoked) → 즉시 전체 중단: `codex login`.
  · 429·한도 → 45초 쉬고 재시도, 재시도를 다 쓰면 전체 중단(리셋 뒤 같은 명령).
"""
from __future__ import annotations

import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List

from . import config
from .job import Job
from .llm import codex_transport, models
from .llm.codex_image import generate_image
from .llm.codex_transport import ImageEngineError, NotAuthenticated
from .log import detail, log, usage

RATE_WORDS = ("429", "rate limit", "too many", "usage limit", "quota", "한도")


class Stop(Exception):
    pass


def full_prompt(item: Dict[str, Any], style_hint: str) -> str:
    """imgstudio `compose_prompt` 와 같은 꼴: 지시문 + 스타일 + Avoid."""
    out = item["prompt"].strip()
    if style_hint and style_hint not in out:
        out += f"\n스타일: {style_hint}"
    if item.get("negative"):
        out += f"\nAvoid: {item['negative']}."
    return out


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def run(job: Job, only=None, force: bool = False, limit: int = 0, **_) -> None:
    cfg = config.load()["image"]
    env = config.read_json(job.images / "이미지프롬프트.json")
    if not env:
        raise SystemExit("images/이미지프롬프트.json 이 없습니다 — 먼저 s2-imgjson")
    items: List[Dict[str, Any]] = env["prompts"]
    if only:
        ids = {s.data_id for s in job.pick(only)}
        items = [it for it in items if it["data_id"] in ids]

    baked = config.read_json(job.images / "baked.json", {}) or {}
    todo = []
    for it in items:
        prompt = full_prompt(it, env.get("style_hint", ""))
        it["_full"] = prompt
        exists = (job.images / it["file"]).exists()
        if exists and not force:
            if baked.get(it["file"]) not in (None, _hash(prompt)):
                detail(f"  {it['file']} 지시문이 바뀜 — 새로 구우려면 --force --only {it['data_id']}")
            continue
        todo.append(it)
    pending = len(todo)
    if limit:
        todo = todo[:limit]
    detail(f"s3-images: 이미 있음 {len(items) - pending}장 · 이번에 구움 {len(todo)}장 (남음 {pending - len(todo)})")
    if not todo:
        log("그림은 이미 다 있어요.")
        return

    model = models.resolve_image(force_probe=force)
    if model == models.NO_IMAGE:
        log("그림은 이번에 건너뛰어요. 카드는 깔끔한 기본 표지로 만들어요.")
        log("  (그림을 넣고 싶으면: 게시물 화면 → 「그림 설명 복사」 → ChatGPT 에서 그림 만들기 → 「그림 넣기」)")
        detail("그림 모델 없음 — 요금제가 바뀌었으면 s3-images --force 로 다시 탐지")
        return
    models.apply(model)
    log(f"그림 {len(todo)}장을 그리는 중이에요 (한 장에 1분쯤) …")
    detail(f"  모델 {model} (image_generation 툴)")
    workers = max(1, min(6, int(cfg.get("workers", 3))))
    retries = int(cfg.get("retries", 2))
    stop = threading.Event()
    lock = threading.Lock()
    fatal: List[str] = []

    def bake(it: Dict[str, Any]) -> str:
        for attempt in range(retries + 1):
            if stop.is_set():
                return "stopped"
            try:
                t0 = time.time()
                png, meta = generate_image(it["_full"], size=it.get("size", "auto"))
                (job.images / it["file"]).write_bytes(png)
                with lock:
                    baked[it["file"]] = _hash(it["_full"])
                    config.write_json(job.images / "baked.json", baked)
                log(f"  ✓ 그림 완성 — {it['title']}")
                detail(f"  ✓ {it['file']}  {time.time() - t0:.0f}초")
                return "ok"
            except NotAuthenticated as e:
                detail(f"NotAuthenticated: {e}")
                fatal.append("Codex 로그인이 풀렸어요. 터미널에서 codex login 을 다시 한 뒤 같은 버튼을 눌러 주세요.")
                stop.set()
                return "auth"
            except (ImageEngineError, OSError) as e:
                msg = str(e)
                rate = any(w in msg.lower() for w in RATE_WORDS)
                if attempt >= retries:
                    if rate:
                        fatal.append("오늘 쓸 수 있는 양을 다 썼어요. 나중에 같은 버튼을 누르면 남은 것만 이어서 해요.")
                        stop.set()
                    log(f"  ✗ 그림을 못 그렸어요 — {it['title']}")
                    detail(f"  ✗ {it['file']}  {msg[:300]}")
                    return "fail"
                wait = 45 if rate else 6 * 2 ** attempt
                detail(f"  … {it['file']} 재시도 {attempt + 1}/{retries} ({wait}초 뒤) — {msg[:300]}")
                time.sleep(wait)
        return "fail"

    detail(f"  워커 {workers} · 재시도 {retries}")
    results: Dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for f in as_completed([ex.submit(bake, it) for it in todo]):
            r = f.result()
            results[r] = results.get(r, 0) + 1
    detail("s3-images: " + " · ".join(f"{k} {v}" for k, v in results.items()))
    log(f"그림 {results.get('ok', 0)}장 완성" + (f", {results['fail']}장은 못 그렸어요" if results.get("fail") else ""))
    detail(codex_transport.limits_line())
    if usage():
        log(usage())
    if fatal:
        raise SystemExit(fatal[0])
