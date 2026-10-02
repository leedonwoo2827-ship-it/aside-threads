"""작업(job) 폴더 하나 = 원고 묶음 하나 = Threads 시리즈 하나.

jobs/<job>/
  job.json            시리즈 이름·주제태그·말투 (git 에 올려도 되는 것)
  job.local.json      PC별 덮어쓰기
  input/*.html        원고 (h3 한 판 = 게시물 하나)
  posts/<id>.json     s1 이 쓴 문구 (캐시 = 이어하기)
  overrides/<id>.json 사람이 고친 문구 — **언제나 이긴다** (backplate §6)
  images/             이미지프롬프트.json + 구운 그림
  cards/<id>/NN.png   카드뉴스 완성본
  state.json          게시·예약 기록
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config
from .manuscript import Slide, parse_dir

_STATE_LOCK = threading.Lock()


@dataclass
class Job:
    name: str

    @property
    def dir(self) -> Path:
        return config.JOBS / self.name

    @property
    def input(self) -> Path:
        return self.dir / "input"

    @property
    def posts(self) -> Path:
        return self.dir / "posts"

    @property
    def overrides(self) -> Path:
        return self.dir / "overrides"

    @property
    def images(self) -> Path:
        return self.dir / "images"

    @property
    def cards(self) -> Path:
        return self.dir / "cards"

    def exists(self) -> bool:
        return (self.dir / "job.json").exists()

    @cached_property
    def meta(self) -> Dict[str, Any]:
        base = config.read_json(self.dir / "job.json", {}) or {}
        return config.deep_merge(base, config.read_json(self.dir / "job.local.json", {}) or {})

    def get(self, key: str, default: Any = None) -> Any:
        cur: Any = self.meta
        for part in key.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    @cached_property
    def slides(self) -> List[Slide]:
        return parse_dir(self.input)

    def slide(self, data_id: str) -> Optional[Slide]:
        return next((s for s in self.slides if s.data_id == data_id), None)

    def pick(self, only: Optional[List[str]] = None) -> List[Slide]:
        if not only:
            return list(self.slides)
        want = set(only)
        got = [s for s in self.slides if s.data_id in want or str(s.n) in want]
        if not got:
            raise SystemExit(f"--only 에 맞는 원고가 없습니다: {', '.join(only)}")
        return got

    # ── 문구 ─────────────────────────────────────────────────────────────────
    def raw_post(self, data_id: str) -> Optional[Dict[str, Any]]:
        return config.read_json(self.posts / f"{data_id}.json")

    def post(self, data_id: str) -> Optional[Dict[str, Any]]:
        """s1 결과 위에 사람의 손편집을 덮은 것 — 다른 모든 곳은 이것만 읽는다."""
        base = self.raw_post(data_id)
        if base is None:
            return None
        return config.deep_merge(base, config.read_json(self.overrides / f"{data_id}.json", {}) or {})

    def save_override(self, data_id: str, patch: Dict[str, Any]) -> None:
        cur = config.read_json(self.overrides / f"{data_id}.json", {}) or {}
        config.write_json(self.overrides / f"{data_id}.json", config.deep_merge(cur, patch))

    def reset_override(self, data_id: str) -> None:
        (self.overrides / f"{data_id}.json").unlink(missing_ok=True)

    def card_files(self, data_id: str) -> List[Path]:
        return sorted((self.cards / data_id).glob("[0-9][0-9].png"))

    # ── 게시 상태 ────────────────────────────────────────────────────────────
    def state(self) -> Dict[str, Any]:
        return config.read_json(self.dir / "state.json", {}) or {}

    def update_state(self, data_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
        """다시 읽고 고쳐 쓴다 — 서버(예약)와 하위 프로세스(게시)가 같이 쓴다."""
        with _STATE_LOCK:
            st = self.state()
            cur = dict(st.get(data_id) or {})
            for k, v in patch.items():
                if v is None:
                    cur.pop(k, None)
                else:
                    cur[k] = v
            st[data_id] = cur
            config.write_json(self.dir / "state.json", st)
            return cur


def all_jobs() -> List[Job]:
    if not config.JOBS.exists():
        return []
    return [Job(p.name) for p in sorted(config.JOBS.iterdir())
            if p.is_dir() and (p / "job.json").exists() and p.name != "example"]


def need(name: Optional[str]) -> Job:
    if not name:
        jobs = all_jobs()
        if len(jobs) == 1:
            return jobs[0]
        raise SystemExit("--job 을 지정하세요. 있는 작업: " + ", ".join(j.name for j in jobs))
    job = Job(name)
    if not job.exists():
        raise SystemExit(f"작업이 없습니다: jobs/{name}/job.json — `python -m aside new {name} --from <원고폴더>`")
    return job
