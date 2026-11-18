# Solomon

GPT와 Claude가 서로 검증하며 코드를 만드는 개발 파이프라인 (MVP).
한 모델이 작성하고, **다른 모델**이 리뷰하고, 테스트가 통과한 패치 1개를 만들어 냅니다.
모델 호출은 로그인된 **구독 CLI**(`claude`, `codex`)만 사용하며 종량제 API 키는 쓰지 않습니다.

```
goal ─▶ PLANNER ─▶ ORDER ─▶ BUILDER(격리 worktree) ─▶ Scope Guard ─▶ 테스트 ─▶ REVIEWER ─▶ PASS/FAIL
                               ▲                                                         │
                               └──────────────── FAIL이면 피드백과 함께 재시도 ◀───────────┘
```

## 요구사항 (macOS 기준)
- Python 3.11+ , git
- `claude` (Claude Code) 와 `codex` (OpenAI Codex) CLI 설치 후 각각 구독 계정으로 로그인
- 외부 파이썬 패키지 없음 (테스트에만 pytest)

## 설치와 점검
가장 쉬운 방법: `bash scripts/setup_mac.sh` (Python 3.11+ 탐색, 가상환경 생성, 설치, 테스트, CLI 점검을 한 번에. 아무것도 삭제하지 않음)

이 스크립트는 `00.Solomon` 폴더가 아직 git 저장소가 아니면 **로컬 저장소**(`git init` + 첫 커밋, remote 없음)도 만듭니다. GitHub는 쓰지 않아도 됩니다.

대상 프로젝트는 GitHub가 아니라 **로컬 경로**로 지정합니다. 한 번만 지정해 두려면 `solomon.toml`에 `project = "/Users/dave_gil/00.Claud/my-app"`을 적으면 되고, 실행할 때마다 `--project`로 바꿔도 됩니다 (`--project`가 우선).

수동으로 하려면:
```bash
cd /Users/dave_gil/00.Claud/00.Solomon
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
solomon doctor          # CLI 설치 여부 확인 (로그인 상태는 직접 확인)
python3 -m pytest -q    # 가짜 CLI로 파이프라인 전체 테스트
```

## 사용
대상 프로젝트는 **커밋이 하나 이상 있는 git 저장소**여야 합니다.
```bash
solomon run "로그인 실패 시 에러 메시지를 보여주는 함수를 추가" --project ~/work/my-app
solomon run "..." --project ~/work/my-app --on-quota wait   # 구독 한도 소진 시 대기 후 재개
solomon run "..." --project ~/work/my-app --apply           # 통과한 패치를 작업 폴더에 적용(커밋은 안 함)
```
설정은 `solomon.example.toml`을 `solomon.toml`로 복사해 수정합니다. 기본 역할은 planner=claude, builder=codex, reviewer=claude 입니다 (모델별 강점에 대한 확정 사실이 아니라 시작 가설).

진행 현황은 `solomon status [--project 경로]`로 봅니다 (최근 실행, 단계, AI별 호출 수·CLI 시간·토큰, 진행 기록). 토큰은 CLI가 알려주면 **정확**, 아니면 글자 수로 **추정**(`~` 표시)합니다.

결과는 `<project>/.solomon/runs/<run_id>/` 에 남습니다: `events.jsonl`(단계별 로그), `result.json`, `result.patch`(통과 시), `last_attempt.patch`(마지막 실패 시도).

## 대시보드 (명령을 내리고 진행을 보는 웹 화면)
**한 번에 띄우기** (가상환경 켜기, 경로 설정, AI CLI 점검을 알아서 합니다):
```bash
bash scripts/start_dashboard.sh                         # 기억해 둔 프로젝트로 시작, 브라우저가 열림
bash scripts/start_dashboard.sh --project ~/work/ena-bot
bash scripts/start_dashboard.sh --pull                  # git pull 한 뒤 시작
```
Finder에서 **`Start-Solomon.command`를 더블클릭**해도 터미널이 열리며 같은 일을 합니다 (실행 권한이 없다는 안내가 나오면 `chmod +x Start-Solomon.command scripts/start_dashboard.sh`). 터미널 창을 닫으면 대시보드도 종료됩니다.
프로젝트는 한 번 지정하면 `~/.solomon/dashboard.json`에 기억합니다.

직접 실행하려면:
```bash
solomon dashboard --project /Users/dave_gil/00.Claud/my-app
```
터미널에 `http://127.0.0.1:8765/?t=...` 주소가 나오고 브라우저가 열립니다. 이 컴퓨터에서만 열리며, 주소의 `t=` 값이 접속 키입니다.
- **명령:** 목표(에러 로그를 통째로 붙여 넣어도 됨)와 프로젝트 폴더를 넣고 ▶ 실행. **역할 배정**(계획·구현·검수를 어느 AI가 맡을지)은 화면의 선택창에서 실행마다 고릅니다. 설치된 AI는 모두 가운데에 나타나고, 구현은 같은 AI가 검수까지 하는 조합과 쓰기가 안 되는 AI(Antigravity)는 막힙니다. 명령줄에서는 `solomon run ... --planner claude --builder codex --reviewer antigravity`. ■ 중지는 돌고 있는 AI 호출까지 끝내고 `STOPPED`로 기록합니다.
- **보는 것:** 지금 누가 무엇을 하는지, 세 역할(계획·구현·검수)과 담당 AI, 단계 바, AI별 호출·시간·토큰, 진행 기록, 최근 호출, 결과 요약.
- **원본 보호:** 작업은 격리된 복사본에서만 이뤄집니다. 통과한 패치는 **원본에 적용** 버튼을 눌러야 반영되며 커밋은 하지 않습니다.
- **프로젝트별 테스트 명령:** 화면의 "프로젝트 설정"에서 지정합니다 (예: `npm test`). `없음`이면 테스트 없이 실행하고 결과는 미검증으로 표시됩니다.
- 커밋되지 않은 변경은 격리된 복사본에 들어가지 않습니다 (화면에 경고가 표시됩니다). 먼저 커밋하세요.
- 에러 로그에 나온 파일의 내용은 계획 AI에게 함께 전달되어, 고칠 파일을 더 정확히 짚습니다.

## G2 검증 실험 (교차 검증이 단독보다 나은가)
`bench/tasks/`에 과제 7개가 있습니다. 각 과제는 AI에게 보이는 목표와, AI가 **보지 못하는 숨은 테스트**로 이뤄집니다.
파이프라인이 PASSED라고 했는데 숨은 테스트가 실패하면 **결함 통과**로 셉니다. 정답 기준은 AI가 아니라 숨은 테스트입니다.

```bash
solomon bench validate                       # 과제가 공정한지 확인 (구현이 없으면 실패, 정답 구현이면 통과). AI 호출 없음
solomon bench run --only t01 --out bench_results     # 먼저 과제 1개를 4가지 구성으로 (시범 4회)
solomon bench run --out bench_results                # 전체 7과제 x 4구성 = 28회
solomon bench report --out bench_results             # 구성별 비교표
```
4가지 구성: `codex_solo`, `claude_solo`(한 모델이 계획·구현·검수를 모두 함), `codex_build_claude_review`, `claude_build_codex_review`(서로 다른 모델이 검수).
한도가 소진되면 `BLOCKED_QUOTA`로 멈추고, 같은 명령을 다시 실행하면 끝난 것은 건너뛰고 이어서 합니다. `--repeat N`으로 반복 횟수를 늘릴 수 있습니다.
주의: `claude`가 구현하는 구성과 `codex`가 계획·검수하는 구성은 아직 실제 CLI로 돌려 본 적이 없습니다. 시범 4회로 먼저 확인하세요.

## 안전 장치
| 장치 | 동작 |
| --- | --- |
| 구독 전용 | 실행 전 `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY` 등을 환경에서 제거 |
| 작성자 ≠ 검증자 | builder와 reviewer가 같은 공급자면 시작 거부 (`allow_same_provider`로만 예외, `REDUCED_INDEPENDENCE` 표시) |
| 격리 실행 | 원본이 아닌 git worktree에서만 작성·테스트. 원본은 `--apply` 전에는 변경되지 않음 |
| Scope Guard | git diff 기준으로 허용 경로, 보호 경로, 삭제, 파일 수, diff 줄 수 검사. 위반 변경은 폐기 후 재시도 |
| fail-closed 리뷰 | 리뷰어 오류, 응답 깨짐, 판정 불명은 모두 FAIL |
| 쿼터 소진 | API로 우회하지 않음. `stop`(기본)은 BLOCKED_QUOTA로 종료, `wait`는 대기 후 같은 호출 재시도 |
| 프로세스 관리 | 셸 없이 실행, 전체/무출력 타임아웃, 타임아웃 시 프로세스 그룹 전체 종료 |

## 알려진 한계 (정직하게)
- 이 코드는 가짜 CLI로 만든 테스트(34개)로만 검증했습니다. **실제 `codex`, `claude` 호출은 아직 한 번도 실행해 보지 않았습니다.** `codex exec --sandbox ...`와 `claude --print --permission-mode acceptEdits` 플래그는 설치된 버전에 맞는지 맥에서 확인하세요. 안 맞으면 `solomon.toml`의 `extra_args`나 `providers/*.py`의 `build_argv`를 고치면 됩니다.
- 쿼터 소진 감지는 CLI 출력 문구 일치에 기대므로, CLI 메시지가 바뀌면 일반 오류로 분류될 수 있습니다. 종료 코드나 오류 플래그가 이미 실패를 말할 때만 문구를 봅니다.
- 쿼터 초기화 시각은 해석하지 않고 `quota_wait_sec` 간격으로 다시 시도합니다.
- 최종 판정은 MVP에서 결정론적 규칙(범위, 테스트, 리뷰)이며 PLANNER가 다시 판정하지 않습니다.
- worktree는 커밋된 HEAD 기준이라, 작업 폴더의 미커밋 변경은 포함되지 않습니다 (경고 표시).
- 웹 대시보드, 미션, 토론 모드, Gemini는 MVP 범위 밖입니다.

## Google AI: Antigravity (`agy`)
개인 계정용 **Gemini CLI의 Google 로그인은 지원이 끝났습니다**(로그인 시 "Antigravity로 옮기라"는 안내가 나옴). 구독으로 Google AI를 쓰는 경로는 **Antigravity CLI(`agy`)** 입니다.
설치와 로그인은 [antigravity.google](https://antigravity.google)의 안내를 따르고, **로그인 방식은 계정 로그인**을 고르세요. API 키·Vertex AI 항목은 사용량만큼 과금됩니다. `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `GOOGLE_GENAI_USE_VERTEXAI`는 실행 전에 환경에서 제거됩니다.

```toml
# solomon.toml: 예) 계획과 검수를 Antigravity에게, 구현은 Codex에게
[roles]
planner  = "antigravity"
builder  = "codex"
reviewer = "antigravity"
```
- **지금은 계획·검수만** 맡습니다. 빈 임시 폴더에서 도구 없이 프롬프트에 담긴 근거만으로 판단하므로 안전합니다. **구현(builder) 역할로 지정하면 시작을 거부합니다.** 파일 쓰기 범위를 강제하는 장치(레퍼런스는 권한 규칙과 훅을 설치)가 아직 없기 때문입니다.
- 호출 방식(`--input-format stream-json --output-format stream-json --sandbox`)은 실제 `agy`의 도움말과 실제 호출 1회로 확인했습니다. 토큰은 결과 이벤트의 `usage`(입력·출력·캐시·총합)와 턴 수를 읽습니다.
- **모델은 Gemini로 고정**합니다(기본 `gemini-3.8-flash-high`). `agy models`에는 Claude·GPT-OSS도 나오는데, "auto"가 그 모델을 고르면 Claude가 Claude를 검수하는 꼴이 되어 교차 검증이 무의미해지기 때문입니다. 바꾸려면 `[extra_args] antigravity = ["--model", "<agy models의 id>"]`.
- 비교 실험: `solomon bench run --configs codex_build_antigravity_review,claude_build_antigravity_review` (기본 4구성에는 포함되지 않음).

다른 AI를 더 붙이려면 `providers/<이름>.py`에 `CliProvider` 하위 클래스를 만들고 `providers/__init__.py`의 `PROVIDER_CLASSES`에 한 줄 추가합니다.

## 구조
```
solomon/  cli.py  orchestrator.py  settings.py  schemas.py  scope_guard.py  gitops.py
          verifier.py  runner.py  runlog.py  prompts.py  jsonutil.py  env.py
          providers/{base,claude,codex}.py
tests/    가짜 공급자(ScriptedProvider)로 전 단계 검증
```
