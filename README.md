# aside-threads — 딸깍 Threads 카드뉴스 작성기

강의 원고 HTML(h3 한 판)을 넣으면 **고품질 카드뉴스(HTML로 짠 글자 + Codex 그림)** 와
**Threads 본문·이어쓰기 답글**을 만들고, 화면 오른쪽 좁은 패널(aside)에서 확인·수정한 뒤
왼쪽 Chrome 으로 **딸깍 게시하거나 예약**합니다.

- 로그인은 `codex login` 하나 — 문구와 그림이 모두 각자 회사 ChatGPT 구독으로 나갑니다(API 키 없음).
- 글자는 그림에 굽지 않습니다. 그림은 글자 없는 삽화만, 글자는 카드 HTML이 얹습니다 → 한글이 안 깨집니다.
- 네이버 블로그는 별도 프로젝트입니다.

## 처음 한 번 (PC마다)

| | Windows | macOS · Linux |
|---|---|---|
| 설치 | `setup.bat` | `./setup.sh` |
| 로그인 | `codex login` | `codex login` |
| 실행 | `run.bat` | `./run.sh` |

- 설치: venv · 패키지 · Playwright Chromium · 글꼴 · 점검. 필요: Python 3.11+, Google Chrome, Codex CLI.
- `codex login` 은 회사 ChatGPT 계정으로 사람마다 따로 합니다.
- 실행하면 서버 창(콘솔/터미널)과 오른쪽 패널이 뜹니다. **예약을 걸어 뒀으면 서버 창을 켜 두세요**(최소화는 괜찮음). 패널 창은 닫아도 `http://127.0.0.1:5291` 로 다시 열립니다.
- Linux 는 폴더·파일 선택 창에 `python3-tk` 가 필요합니다(`sudo apt install python3-tk`). 없으면 경로를 붙여 넣는 칸이 대신 뜹니다.

패널에서:
1. **＋** 로 계정 추가 → **로그인** → 왼쪽 Chrome 에서 「Instagram으로 계속하기」로 Threads 로그인 (계정마다 프로필이 따로 생김)
2. **＋원고** → **📁 폴더 지정하기**(안의 HTML 전부) 또는 **📄 파일 선택하기**(고른 것만, 테스트로 2개만 등). h3 한 판 = 게시물 하나

## 매번

1. **그림 2장만 먼저** → 톤 확인 (backplate 정석)
2. **딸깍 만들기** → s1 문구 → s2 이미지 JSON → s3 그림 → s4 카드. 캐시라 다시 눌러도 남은 것만 갑니다
3. 게시물을 눌러 카드·본문·답글 확인, 고치면 **저장 + 카드 다시 찍기** (손편집은 언제나 이김)
4. **미리 채워 보기**(게시 안 함) → **지금 게시** 또는 **예약** / 예약 탭의 **남은 것 자동 배치**

## 게시물 구조 (추천안)

| 칸 | 내용 |
|---|---|
| 본문 | 훅 1줄(피드에 보이는 첫 줄) + 핵심 2~4줄 + 저장 유도, 350자 안팎, 주제태그 1개(`job.json` 의 topic) |
| 카드 4~6장 | 01 표지 · 02~03 핵심 · 도식(원고 SVG/표 그대로) · 기출 포인트(함정 보기) · 마무리(다음 편) — 1080×1350 |
| 이어쓰기 답글 | 「시험에선 이렇게 나와요」 + 댓글 유도 질문 |
| 간격 | 하루 1~2회(`aside.config.json` 의 `threads.slots`). 몰아올리지 않습니다 |

## 예약

- 기본은 **aside 대기열**: 시각이 되면 패널 서버가 왼쪽 Chrome 으로 올립니다 → `run.bat` 이 켜져 있어야 합니다(창은 닫아도 됨, 콘솔 창은 유지).
- `threads.native_schedule: true` 로 바꾸면 Threads 자체 예약으로 넘깁니다(PC 꺼도 게시). Threads 화면이 바뀔 수 있어 `python -m aside probe` 로 확인된 PC에서만 켜세요.

## 그림이 안 나올 때 (중요)

Codex 계정·요금제에 따라 **그림 도구가 아예 안 열리는 경우**가 있습니다
(2026-10-03 실측: `gpt-5.5` 404, `gpt-5.6-*` 은 image_generation 툴을 떼어 버림).
aside 는 처음 한 번 탐지해서 `local.json` 의 `codex_image_model` 에 기억합니다.

- 안 되는 계정이면 카드는 **그림 없는 디자인 표지**로 완성됩니다(그대로 올려도 됨).
- 그림을 넣고 싶으면 게시물 화면의 **프롬프트 복사** → ChatGPT 앱에서 생성 → **그림 넣기**.
- 요금제가 바뀌었으면 `run.bat s3-images --job <작업> --force` 로 다시 탐지합니다.
- 생성된 `jobs/<작업>/images/이미지프롬프트.json` 은 backplate 의 이미지 JSON 과 같은 꼴이라 imgstudio 일괄 굽기에도 넣을 수 있습니다.

## CLI

```
run.bat new adsp2 --from D:\원고\slides --title "ADsP 2과목" --topic ADsP --chip "ADsP 2과목"
run.bat new test2 --files D:\원고\a.html D:\원고\b.html      파일을 골라서 (Mac/Linux: ./run.sh …)
run.bat make --job adsp2 [--only q2-01-01] [--force] [--limit 2]
run.bat post --job adsp2 --only q2-01-01 [--dry-run] [--at "2026-10-05 08:30"]
run.bat plan --job adsp2 --apply
run.bat account add study --label @study.id
run.bat probe --account study      작성창 구조를 logs/probe/ 에 떠 둠(게시 안 함)
run.bat doctor
```

## 폴더

```
aside/                 코드 (llm/ 은 backplate 의 Codex OAuth 전송 그대로)
templates/cards/       카드 테마(theme.css) — 색은 aside.config.json 의 image 칸
jobs/<작업>/            input · posts · overrides · images · cards · state.json
profiles/<계정>/        Threads 로그인 쿠키 — 절대 커밋 금지 (.gitignore)
local.json             PC별 계정 목록·탐지된 모델 (커밋 금지)
logs/post/             게시 단계별 스크린샷
```

## 막히면

| 증상 | 할 일 |
|---|---|
| `token_revoked` / 로그인 끊김 | `codex login` 후 같은 버튼 |
| `429`·한도 | 리셋 뒤 같은 버튼 (있는 것은 건너뜀) |
| 「로그인이 안 되어 있습니다」 | 패널 **로그인** → 왼쪽 Chrome 에서 로그인 |
| 게시 버튼·첨부를 못 찾음 | `logs/post/` 스크린샷 확인 → `probe` 결과를 보고 `aside/threads.py` 의 `SEL` 표만 고침 |
| Chrome 디버그 포트가 안 열림 | 같은 프로필 Chrome 창을 닫고 다시 |

## 배치·셸 파일 규칙

`.bat` 파일에는 ASCII 영문과 CRLF 줄바꿈만 씁니다(한국어 Windows cmd가 UTF-8 한글·LF를 잘못 읽음). `.gitattributes`가 CRLF를 고정합니다.

macOS·Linux 용 `setup.sh`·`run.sh` 는 LF 줄바꿈입니다(`.gitattributes` 가 고정). Windows 에서 고칠 때 CRLF 로 바뀌지 않게 주의하세요.
