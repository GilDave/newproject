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

## Gemini 등 확장
`solomon/providers/gemini.py`에 `CliProvider` 하위 클래스를 만들고 `providers/__init__.py`의 `PROVIDER_CLASSES`에 한 줄 추가하면, 설정의 `[roles]`에서 바로 배정할 수 있습니다.

## 구조
```
solomon/  cli.py  orchestrator.py  settings.py  schemas.py  scope_guard.py  gitops.py
          verifier.py  runner.py  runlog.py  prompts.py  jsonutil.py  env.py
          providers/{base,claude,codex}.py
tests/    가짜 공급자(ScriptedProvider)로 전 단계 검증
```
