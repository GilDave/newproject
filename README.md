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
가장 쉬운 방법: `bash install.sh` (Python 3.11+ 탐색, 가상환경 생성, 설치, 무비용 자체 점검 `solomon selftest`, 환경 점검 `solomon doctor`을 한 번에. 아무것도 삭제하지 않음). 사용법은 **[USER_GUIDE.md](USER_GUIDE.md)** (대시보드 우측 상단 '사용 가이드'에서도 볼 수 있습니다).

GitHub는 쓰지 않아도 됩니다. 대상 프로젝트는 모두 로컬 폴더입니다.

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
**시작과 종료 (백그라운드, 터미널을 돌려줍니다)**
```bash
bash scripts/start.sh                              # 시작하고 브라우저를 엽니다. 이미 실행 중이면 그 화면을 다시 엽니다
bash scripts/start.sh --project ~/work/ena-bot     # 프로젝트 지정 (한 번 지정하면 기억)
bash scripts/start.sh --pull                       # git pull 한 뒤 시작
bash scripts/stop.sh                               # 대시보드 종료 (진행 중인 작업은 계속됩니다)
bash scripts/stop.sh --with-runs                   # 대시보드가 시작한 작업도 함께 중지 (STOPPED로 기록)
```
Finder에서 **`Start-Solomon.command` / `Stop-Solomon.command`를 더블클릭**해도 됩니다 (실행 권한이 없다는 안내가 나오면 `chmod +x *.command scripts/*.sh`).
- 시작하면 터미널은 바로 돌아오고 창을 닫아도 대시보드는 계속 실행됩니다. 접속 주소(접속 키 포함)는 `~/.solomon/dashboard.url`에 저장되고(본인만 읽을 수 있음), 로그는 `~/.solomon/dashboard.log`입니다.
- 프로젝트는 한 번 지정하면 `~/.solomon/dashboard.json`에 기억합니다.
- `stop.sh`는 기록된 pid가 실제 Solomon 대시보드일 때만 종료합니다 (pid가 재사용돼도 다른 프로그램을 죽이지 않습니다).
- 창을 열어 둔 채 보면서 쓰고 싶다면 `bash scripts/start_dashboard.sh` (포그라운드, Ctrl+C로 종료).

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

## 분석 (코드를 바꾸지 않고 여러 AI가 프로젝트를 읽는다)
대시보드 명령 칸의 **분석 · 읽기 전용** 탭에서 프로젝트 폴더를 넣고 ▶ 분석 시작을 누르거나, 명령줄에서:
```bash
solomon analyze --project /Users/dave_gil/00.Claud/01.ena-bot --focus "시작할 때 죽을 수 있는 곳 위주로"
solomon analyze --project ~/work/app --ais claude,codex      # 참여 AI 고르기 (기본: 설치된 모두)
```
- **git 저장소가 아니어도 됩니다.** 분석은 아무 폴더나 읽습니다(git 저장소면 `.gitignore`를 따르고, 아니면 `node_modules`·숨김 폴더·심볼릭 링크를 건너뜁니다). 코드를 고치는 **개선은 git 저장소(커밋 1개 이상)가 필요**합니다. 분석 기록은 프로젝트 안의 `.solomon/` 폴더에 남습니다.
- **원본은 절대 바뀌지 않습니다.** 파일 내용은 프롬프트에 담아 보내고, **모든 AI를 프로젝트가 아닌 빈 임시 폴더에서** 실행하므로 AI가 프로젝트의 다른 파일(`.env` 등)을 직접 열어 볼 수 없습니다. 그 위에 Claude는 도구 없이, Codex는 읽기 전용 샌드박스에서, Antigravity는 전용 빈 폴더에서 실행합니다.
- **비밀은 보내지 않습니다.** `.env`, `*.pem`, `*.key` 같은 파일은 내용 없이 이름만 남기고, 토큰·API 키·비밀번호처럼 보이는 값(텔레그램·디스코드·OpenAI·GitHub·AWS·슬랙 토큰, 비공개 키, `TOKEN = "..."` 형태)은 `<redacted>`로 가립니다. 가린 개수는 보고서에 적힙니다.
- **큰 프로젝트는 예산 안에서 고릅니다** (README·설정·진입점 먼저, 테스트는 나중). 분석한 파일 수와 **제외된 파일과 이유**가 보고서에 적힙니다.
- AI는 **각자 독립적으로 병렬 분석**하고, 같은 위치를 여러 AI가 지적하면 "N/M 동의"로 묶어 보여줍니다.
- AI가 인용한 코드가 **실제 파일에 있는지 자동으로 대조**합니다. 없으면 `근거 불일치`로 표시되니 그 항목은 AI가 지어냈을 수 있습니다. 줄 번호가 틀려도 실제 위치로 고칩니다.
- 한 AI가 한도 소진이나 오류로 실패해도 나머지 결과는 살립니다.
- 결과는 `<프로젝트>/.solomon/analyses/<번호>/`의 `report.md`, `analysis.json`, `events.jsonl`에 남습니다. 대시보드의 **이 문제 고치기** 버튼은 발견한 문제를 개선 모드의 목표로 채워 줍니다.

## 대시보드의 작업실 탭
상단의 **작업실** 탭은 한 번의 실행을 자세히 봅니다: 단계별 카드(계획·구현·테스트·검수·판정), AI별 작업 현황, **시도별 결과**(바뀐 파일, 범위 위반, 테스트 출력, 검수 지적), AI별 호출·시간·토큰 합계, AI별 카드(상태·역할·최근 작업·오늘 호출), CLI 연결도.

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
- **모델은 Gemini로 고정**합니다(기본 `gemini-3.8-flash-medium`. `high`는 몇 분씩 생각해 시간 초과가 잦았습니다).
- `agy`는 조용히 오래 생각하므로 **출력이 없다는 이유로 끊지 않고**, 제한 시간은 최소 600초입니다. `agy models`에는 Claude·GPT-OSS도 나오는데, "auto"가 그 모델을 고르면 Claude가 Claude를 검수하는 꼴이 되어 교차 검증이 무의미해지기 때문입니다. 바꾸려면 `[extra_args] antigravity = ["--model", "<agy models의 id>"]`.
- 비교 실험: `solomon bench run --configs codex_build_antigravity_review,claude_build_antigravity_review` (기본 4구성에는 포함되지 않음).

다른 AI를 더 붙이려면 `providers/<이름>.py`에 `CliProvider` 하위 클래스를 만들고 `providers/__init__.py`의 `PROVIDER_CLASSES`에 한 줄 추가합니다.

## 구조
```
solomon/  cli.py  orchestrator.py  settings.py  schemas.py  scope_guard.py  gitops.py
          verifier.py  runner.py  runlog.py  prompts.py  jsonutil.py  env.py
          providers/{base,claude,codex}.py
tests/    가짜 공급자(ScriptedProvider)로 전 단계 검증
```

## 끈질기게 시도 (persistent loop) · 교차·이중 검증

- 기본은 3번 시도 후 실패입니다. `solomon run --persist` (대시보드: "끈질기게 시도", 기본 켜짐·최대 180분)는 횟수 제한 없이 **통과할 때까지** 반복합니다. 멈추는 조건: 통과, `--max-minutes`, `--max-tokens`, 구독 한도 소진(BLOCKED_QUOTA), 같은 실패가 반복돼 접근을 바꿔도 진전이 없을 때, 사용자의 중지.
- 같은 실패가 `stagnation_limit`(기본 3)번 연속이면 코드를 깨끗한 상태로 되돌리고 "완전히 다른 접근"을 지시합니다 (`max_restarts` 기본 2회).
- **시작 전 점검**: 수정 전 코드에서도 테스트가 실패하면 AI를 호출하지 않고 멈춥니다 (환경 문제로 토큰 낭비 방지).
- **이중 검증**: 테스트는 검수 통과 뒤 한 번 더 실행합니다 (불안정한 테스트 차단). `--reviewer2 <ai>`를 주면 2차 검수 AI도 통과시켜야 합니다 (1차가 통과한 코드만 2차에 보냄). `--no-double-check`로 끌 수 있습니다.
- solomon.toml `[loop]`: `max_attempts`(0=무제한), `max_run_minutes`, `max_tokens`, `stagnation_limit`, `max_restarts`, `double_check_tests`, `baseline_check`.

## 밤샘 자동 수정 (overnight autofix)

분석 결과의 문제를 **차례대로 알아서** 고칩니다. 자는 동안 돌리고 아침에 확인하세요.

```
solomon autofix --project /경로 --min-severity medium --max-fixes 5 --budget-minutes 360
```
(대시보드: 작업 대시보드 → "밤샘 자동 수정" 카드. 맥은 작업 중 잠자기를 막습니다(caffeinate).)

- 대상: 가장 최근 분석(또는 `--analysis ID`)에서 심각도·근거 확인·AI 동의 기준을 넘은 항목만. 근거가 확인되지 않은 항목은 기본 제외(`--include-unverified`).
- 각 수정은 일반 실행과 같은 파이프라인(계획→구현→범위 검사→테스트→교차 검수→테스트 재확인)을 거치며, **앞 수정 결과 위에서** 다음 수정을 합니다. 항목당 파일 8개·400줄 제한.
- 통과한 수정은 새 브랜치 `solomon/autofix-<id>`에 "Solomon" 이름으로 커밋됩니다. **현재 브랜치와 작업 폴더는 건드리지 않습니다.**
- 멈추는 조건: 시간 예산, 구독 한도 소진, 3건 연속 실패, 수정 전 테스트 실패, 중지 버튼. 실패한 항목은 건너뛰고 계속합니다.
- 테스트 명령이 없으면 시작하지 않습니다 (`--allow-untested`로 해제).
- 결과: `<프로젝트>/.solomon/batches/<id>/report.md` (아침에 확인하는 명령 포함), `combined.patch`, `events.jsonl`. 대시보드의 "원본에 적용"은 커밋 없이 패치만 적용합니다.
- 라이브로 돌아가는 프로그램(예: 자동매매 봇)은 적용 전에 직접 테스트하고, 장이 닫힌 뒤에 반영하세요.

## 관리 탭 — 결과 확인·적용·커밋·되돌리기·폐기를 대시보드 한 곳에서

터미널 명령 없이 모든 결정을 대시보드 **관리** 탭에서 합니다.

- **결과함**: 통과한 개선 결과와 밤샘 수정 배치(항목별 포함)가 모입니다. 항목마다 `변경 보기 · 적용 · 테스트 실행 · 커밋 · 되돌리기 · 폐기` 버튼이 있고, 충돌로 적용할 수 없으면 이유가 표시됩니다.
- **커밋**은 그 항목이 바꾼 파일만 커밋합니다 (봇이 갱신하는 파일 등 다른 변경은 건드리지 않습니다). 커밋 메시지는 입력받습니다.
- **5W1H**: 목록의 번호(실행·배치·분석)를 누르면 누가(AI·역할·토큰) / 무엇을(목표·결과·바뀐 파일) / 언제 / 어디서(경로·브랜치·패치) / 왜(요청·근거가 된 분석 지적) / 어떻게(시도·테스트·검수·진행 기록)를 한 화면에 보여줍니다. 아래에 변경 내용(diff)도 보입니다.
- **작업 폴더 테스트 실행** 버튼으로 적용 후 테스트를 바로 돌립니다.
- 대시보드 첫 화면의 '결과' 카드는 줄글 대신 구조화된 요약과 관리 탭 바로가기를 보여줍니다.
- **커밋 안 된 변경 자동 포함**: 작업 폴더에 커밋하지 않은 수정이 있어도 AI는 그 상태 그대로(스냅샷) 작업합니다. 별도 커밋이 필요 없습니다. `.env`, `*.pem`, `*.key` 같은 비밀 파일은 AI에게 보내지 않습니다. 끄려면 solomon.toml `[loop] include_uncommitted = false`.

## 신규 프로젝트 · 프로젝트 폴더 관리 · 복원

- **작업 종류가 셋으로 나뉩니다** (명령 카드 맨 위): `개선 · 코드 수정`(기존 프로젝트), `분석 · 읽기 전용`, `신규 프로젝트`(처음부터).
- **신규 프로젝트**: 이름·종류(Python/Node/빈 프로젝트)·만들 것을 적으면 폴더, git, 테스트 틀을 만들고 첫 구현을 시작합니다. 위치는 `projects_root`(기본 `~/Solomon-Projects`, solomon.toml로 변경). 첫 실행은 "수정 전 테스트" 점검을 건너뜁니다.
- **프로젝트 탭**: 프로젝트 목록(선택 · 목록에서 제거 · 폴더 삭제)과 휴지통(복원 · 영구 삭제). 삭제는 **Solomon이 만든 프로젝트만** 가능하고, 이름을 정확히 입력해야 하며, 휴지통(`~/.solomon/trash`)으로 이동하므로 복원할 수 있습니다. 기존 프로젝트(예: 실제 매매 봇)는 목록에서만 제거되고 파일은 절대 건드리지 않습니다. 작업 중인 프로젝트는 삭제·제거할 수 없습니다.
- **복원(되돌림)이 기본입니다**: 폐기한 항목 → `복원`, 적용한 것 → `되돌리기`, 대시보드에서 커밋한 것 → `커밋 취소`(취소 커밋을 새로 만들며 기록은 지우지 않음), 삭제한 프로젝트 → 휴지통 `복원`. 폐기는 아무것도 지우지 않습니다 (밤샘 수정 브랜치도 그대로).
- **상단 현재 작업 표시줄**: 어느 탭에서든 지금 무슨 작업이 어느 단계인지, 결정이 필요한 항목이 몇 건인지 보입니다.
- **적용 판단 + 단계별 진행 과정**: 관리 탭에서 항목을 열면 맨 위에 점검표(범위 검사 · 테스트 · 테스트 재확인 · 교차 검수 · 작성자≠검수자 · 변경 규모 · 작업 폴더 충돌 · 적용 가능 여부)와 권고(권장/확인 필요/비권장)가 나옵니다. 그 아래에 계획(작업 지시서) → 시도별 구현 → 바뀐 파일 → 범위 검사 → 테스트 → 검수(지적 내용) → 재확인이 단계별로 보이고, 진행 중인 실행은 자동 갱신됩니다.

## 단계마다 AI 최대 3개 · 프로젝트 포트폴리오 (v1.1)

- **단계마다 AI 최대 3개**: 계획·구현·검수 각각 `AI 1(담당) · AI 2 · AI 3`. 계획 AI 2·3은 계획을 검토해 완료 기준만 추가하고(범위는 못 넓힘), 구현 AI 2·3은 실패가 이어지면 교대하거나(쓰기 가능) 조언하며(읽기 전용), 검수 AI는 지정한 모두가 PASS해야 통과합니다(구현한 AI는 자기 코드를 검수하지 않음). 범위 검사와 테스트는 AI가 아니라 기계가 판정합니다. CLI: `--planner2 --planner3 --builder2 --builder3 --reviewer2 --reviewer3`, 설정: `[roles]`.
- **프로젝트 탭 = 포트폴리오**: 프로젝트마다 카드(분석 → 개선 → 밤샘 수정 → 결정 대기 → 커밋 흐름, 통과율·평균 시도·시간·토큰, 최근 실행, AI별 사용 비율, 다음 할 일 버튼), 단계별 AI 참여 도식, AI별 성능(호출·응답 시간·토큰·구현 성공률·검수 불합격 지적). 모두 실행 기록에서 집계한 값입니다.
- **탭 연결**: 카드·AI 원·작업실·관리·상단 표시줄의 버튼이 서로 해당 화면(그 프로젝트·그 항목)으로 이동합니다. 주소 끝 `#work` `#mg` `#pj` 로 탭을 바로 엽니다.
- **PDF 가이드**: `Solomon-사용가이드.pdf` (다시 만들기: `python3 scripts/build_guide_pdf.py`).

## 영상 분석 (v1.2)

유튜브 영상을 최대 3개 AI가 **자막을 읽고** 분석합니다: 요약 · 핵심 · **확인할 주장**(말한 것이지 사실이 아님) · 해볼 것 · (선택) 프로젝트에 적용할 아이디어. AI가 인용한 문장은 자막에 실제로 있는지 기계가 대조하고(`근거 확인됨`/`근거 불일치`), 시각 링크의 시각은 자막에서 가져옵니다. 자막 수집 순서: `yt-dlp`(권장, `brew install yt-dlp`) → 유튜브 자막 직접 수집 → 사용자가 붙여넣기. 대시보드 `영상 분석` 모드 또는 `solomon video <주소> [--project P] [--focus F] [--context] [--transcript-file F]`.
`solomon video <주소> --probe` (대시보드: `AI별 영상 직접 시청 시험`)는 각 AI가 영상을 실제로 열 수 있는지 시험합니다: 유튜브가 알려주는 진짜 제목을 맞히면 `확인됨`, 열 수 있다면서 틀리면 `추측`(지어낸 것)입니다. API 키는 쓰지 않으며 접속하는 곳은 youtube.com(제목·자막)뿐입니다.
