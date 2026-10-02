"""s2-imgjson — 「이미지 JSON 만들기」. 결정적(LLM 없음).

s1 이 적은 장면(cover_scene·spot_scene)에 **코드가** 바탕·글자금지·규격 줄을 붙인다
(backplate `s3a.compose()` 와 같은 분업 — 모델은 규칙 줄을 쓰지 않는다).

내보내는 봉투는 backplate `s3b` 의 `이미지프롬프트.json` 과 같은 꼴이라 imgstudio
(`/imgstudio` 일괄 굽기)에도 그대로 넣을 수 있다:
  {deck, style_hint, aspect, count, prompts:[{n, title, type, prompt, negative, data_id, file, size, role}]}
"""
from __future__ import annotations

from typing import Any, Dict, List

from . import config
from .job import Job
from .log import detail, log

NEGATIVE = ("text, letters, words, numbers, captions, labels, typography, watermark, logo, "
            "signature, dark background, black background, night scene, vignette, dark gradient, "
            "neon glow, low quality, blurry, distorted, extra limbs, deformed hands, photorealistic face")

STYLE = ("에디토리얼 매거진 수준의 플랫 벡터 일러스트, 은은한 입체감과 부드러운 그림자, "
         "균일한 선 굵기, 정돈된 형태, 넉넉한 여백")


def _style(img: Dict[str, Any]) -> str:
    return (f"{STYLE}. 포인트 색은 진한 파랑({img['accent_a']})과 밝은 파랑({img['accent_b']}), "
            f"보조로 따뜻한 베이지·연한 회색. 바탕은 아이보리({img['bg']})")


def compose(scene: str, role: str, img: Dict[str, Any]) -> str:
    bg = img["bg"]
    lines = [
        # ★ 바탕을 **맨 앞 독립 줄**로 (backplate 2026-08-16: 뒤에 두면 파랑이 배경을 먹는다)
        f"바탕: 화면 전체를 밝은 아이보리({bg}) 단색으로 고르게 칠한다. 어두운 배경·비네팅·"
        "어두운 그라데이션·발광 금지. 진한 파랑은 사물과 강조에만 쓴다",
        f"장면: {scene.strip()}",
    ]
    if role == "cover":
        lines.append("구도: 가로 3:2. 주제 사물을 화면 가운데에 크고 또렷하게, 주변 소품 2~4개로 "
                     "이야기를 만든다. 네 가장자리까지 자연스럽게 채우되 위아래 10%는 차분하게")
    else:
        lines.append("구도: 정사각형. 사물 **하나**를 한가운데, 둘레는 아이보리 여백을 넉넉히 "
                     "(카드 구석에 작게 얹힌다). 바닥 그림자 하나 외에 배경 소품 없음")
    lines += [
        f"색/톤: {_style(img)}",
        # ★ 한글은 그림이 망가뜨린다(backplate 9장 실측 `오육먹 쇌므과…`). 글자는 카드가 얹는다.
        "글자: 그림 안에 글자·숫자·기호·라벨·제목·로고를 **하나도 넣지 마라.** 글은 카드가 "
        "진짜 텍스트로 얹는다. 화면·종이·칠판·표가 나오면 글자 자리는 추상적인 선과 막대로만",
        "금지: 글자를 담는 상자·말풍선·카드·테두리, 실존 로고·서비스 화면·실존 인물, 얼굴 묘사",
        "산출물 규격: " + ("3:2 가로(1536×1024)" if role == "cover" else "1:1 정사각형(1024×1024)") +
        ", 고해상도, 선명한 가장자리",
    ]
    return "\n".join(lines)


def plan(job: Job) -> List[Dict[str, Any]]:
    """게시물마다 구울 그림 목록 — 표지 1 + (per_post≥2 이면) 소품 1."""
    img = config.load()["image"]
    out: List[Dict[str, Any]] = []
    for s in job.slides:
        post = job.post(s.data_id)
        if not post:
            continue
        roles = [("cover", post.get("cover_scene", ""), img["cover_size"])]
        if int(img.get("per_post", 2)) >= 2 and post.get("spot_scene"):
            roles.append(("spot", post["spot_scene"], img["spot_size"]))
        for role, scene, size in roles:
            if not scene.strip():
                continue
            out.append({
                "n": len(out) + 1, "title": s.title, "type": "photo", "level": "",
                "role": role, "data_id": s.data_id, "size": size,
                "file": f"{s.data_id}-{role}.png",
                "prompt": compose(scene, role, img),
                "negative": img.get("negative") or NEGATIVE,
                "place": False,
            })
    return out


def run(job: Job, **_) -> None:
    img = config.load()["image"]
    prompts = plan(job)
    if not prompts:
        log("먼저 글을 써야 해요 — 「딸깍 만들기」를 눌러 주세요.")
        return
    env = {
        "deck": job.get("title", job.name),
        "style_hint": _style(img),
        "aspect": "mixed (cover 3:2 · spot 1:1)",
        "count": len(prompts),
        "file_naming": "<data_id>-cover.png / <data_id>-spot.png",
        "prompts": prompts,
    }
    config.write_json(job.images / "이미지프롬프트.json", env)
    have = sum((job.images / p["file"]).exists() for p in prompts)
    log(f"그림 설명 {len(prompts)}개를 준비했어요.")
    detail(f"images/이미지프롬프트.json — {len(prompts)}장 (이미 구움 {have} · 남음 {len(prompts) - have})")
