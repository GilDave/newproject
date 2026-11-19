#!/usr/bin/env python3
"""Build the code-structure & behaviour PDF (flowcharts + explanation) from docs/Solomon-코드구조-동작설명서.md.

    python3 scripts/build_flow_pdf.py [out.pdf]
"""
import ast
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from flowfig import Fig  # noqa: E402
from solomon import __version__, mdhtml  # noqa: E402

JS = open(ROOT / "scripts" / "build_guide_pdf.py", encoding="utf-8").read().split('JS = r"""')[1].split('"""')[0].replace("Solomon 사용 가이드", "Solomon 코드 구조 · 동작 설명서")


def fig_system():
    f = Fig(760, 390, "그림 1. 전체 구성 — 누가 누구를 부르는가")
    f.box("user", 20, 70, 120, 46, "사용자\n(브라우저 화면)", "io")
    f.box("dash", 190, 60, 180, 66, "대시보드 서버 dashboard.py\n127.0.0.1:8765 · 접속 키\n화면(HTML) + API", "main")
    f.box("jobs", 430, 60, 160, 66, "작업 프로세스(별도)\nsolomon run / analyze\nautofix / video", "main")
    f.box("orch", 430, 170, 160, 70, "Orchestrator\n계획→구현→검증\n→검수→결정", "main")
    f.box("prov", 630, 150, 112, 110, "AI 제공자\nClaude · Codex\nAntigravity\nOllama(로컬)", "ai")
    f.box("files", 430, 285, 160, 60, "프로젝트/.solomon/\nruns · batches\nanalyses (기록)", "io")
    f.box("home", 630, 285, 112, 60, "~/.solomon/\nquota · vision\nbotlab · 설정", "io")
    f.box("sched", 190, 170, 180, 50, "스케줄러 스레드\n(1분마다 확인)", "main")
    f.box("lab", 190, 255, 180, 56, "봇 연구실 botlab.py\n읽기 전용 평가", "bot")
    f.box("bot", 20, 255, 130, 56, "ena-bot\n(실계좌 · 읽기만)", "bot")
    f.arrow("user", "dash", "화면·버튼")
    f.arrow("dash", "jobs", "실행 요청")
    f.arrow("jobs", "orch")
    f.arrow("orch", "prov", "AI 호출")
    f.arrow("orch", "files", "기록 저장")
    f.arrow("dash", "files", "1.5초마다 읽어 화면에", dash=True, via=[(330, 315)], lx=-20, ly=24)
    f.arrow("orch", "home", "한도 기록", dash=True)
    f.arrow("dash", "sched")
    f.arrow("sched", "lab")
    f.arrow("lab", "bot", "DB 읽기 전용", color="#0f8f84")
    f.arrow("sched", "jobs", "주기 분석 시작", dash=True, via=[(400, 195)])
    return f.svg()


def fig_prepare():
    f = Fig(760, 150, "그림 2. 한 번의 작업 — 준비 단계")
    xs = [14 + i * 124 for i in range(6)]
    labels = [("목표 입력\n(작업실·자동 수정)", "io"), ("① 계획 planner\n작업 지시서 초안", "ai"), ("계획 검토 2·3\n완료 기준만 추가", "ai"),
              ("지시서 확정\n+ 보호 경로 합침", "gate"), ("격리 복사본\n(git worktree)", "main"), ("기준선 테스트\n수정 전 코드", "gate")]
    for i, (t, k) in enumerate(labels):
        f.box(f"p{i}", xs[i], 50, 110, 56, t, k)
        if i:
            f.arrow(f"p{i - 1}", f"p{i}")
    f.box("err", 590, 118, 160, 26, "실패 → 즉시 중단(AI 호출 안 함)", "bad")
    f.arrow("p5", "err", "원본도 실패", color="#d33f49")
    return f.svg()


def fig_loop():
    f = Fig(760, 640, "그림 3. 반복 루프 — 구현에서 통과까지 (시도마다 한 바퀴)")
    ys = [40 + i * 62 for i in range(9)]
    names = [("② 구현 builder\n(쓰기 가능 AI)", "ai"), ("변경 수집\n파일이 바뀌었나?", "gate"), ("③ 범위 검사\n허용·보호 경로, 줄 수 한도", "gate"),
             ("④ 테스트 실행\n프로젝트 테스트 명령", "gate"), ("화면 캡처 (선택)\n화면이 그려지는가?", "gate"), ("⑤ 검수 1~3\nblocker만 FAIL", "ai"),
             ("이중 실행\n통과가 반복되는가?", "gate"), ("⑥ 결정권자\n채택·반려·사람 확인", "ai"), ("통과 → 패치 저장\n(사람이 적용)", "ok")]
    for i, (t, k) in enumerate(names):
        f.box(f"b{i}", 30, ys[i], 210, 44, t, k)
        if i:
            f.arrow(f"b{i - 1}", f"b{i}")
    f.box("fail", 330, 255, 190, 62, "실패 처리\n피드백 작성 · 서명 기록\n(실패 종류를 기억)", "bad")
    f.box("judge", 560, 238, 180, 96, "끝낼까?\n시간·횟수·토큰 초과\n같은 실패 반복 한도", "gate", "diamond")
    f.box("end", 560, 380, 180, 54, "중단: FAILED\n(진전 없음 / 예산)", "bad")
    f.box("next", 330, 380, 190, 62, "다음 시도 준비\n교대 · 초기화 · 조언", "main")
    for i, lab in [(1, "변경 없음"), (2, "위반(되돌림)"), (3, "실패(검수 생략)"), (4, "화면 깨짐"), (5, "blocker FAIL"), (6, "반복 안 됨"), (7, "반려")]:
        f.arrow(f"b{i}", "fail", lab, dash=True, color="#d33f49", lx=34 if i % 2 else 8, ly=(i - 4) * 4)
    f.arrow("fail", "judge")
    f.arrow("judge", "end", "예", color="#d33f49")
    f.arrow("judge", "next", "아니오", via=[(650, 411)] if False else None)
    f.arrow("next", "b0", "다음 시도", via=[(420, 62)], color="#2f6fdb")
    f.note(250, 612, "사람 확인이 필요한 통과(NEED_HUMAN, 화면 미확인)도 '통과'로 저장되며 표시만 달라집니다.", 10.5, "#475569")
    return f.svg()


def fig_stagnation():
    f = Fig(760, 360, "그림 4. 같은 실패 감지 · 교대 · 초기화 (v1.10.1)")
    f.box("a", 20, 50, 170, 50, "실패 발생", "bad")
    f.box("b", 220, 50, 220, 50, "서명 만들기\n실패한 테스트 이름 · 규칙 종류\n(문장·시간·경로는 버림)", "gate")
    f.box("c", 470, 40, 270, 74, "최근 6번 중 같은 서명이\n3번 이상?", "gate", "diamond")
    f.box("d", 470, 170, 270, 56, "코드 초기화 + '완전히 다른 접근' 지시\n재시작 횟수 +1 · 구현 AI 교대", "main")
    f.box("e", 220, 170, 220, 56, "재시작이 한도(2회) 초과?", "gate", "diamond")
    f.box("g", 20, 170, 170, 56, "중단\n'진전 없음 + 반복된 실패 이름'", "bad")
    f.box("h", 20, 270, 330, 60, "교대 규칙: 연속 실패 2번마다 · CLI 실패 · 정체\n→ 다음 쓰기 가능 AI(구현 보조)로 교대. 읽기 전용 AI는 조언만.", "ai")
    f.box("i", 400, 270, 340, 60, "예산: 시도 횟수 · 시간 · 토큰\n자동 수정은 항목당 12번·45분이 한도(백업)", "gate")
    f.arrow("a", "b")
    f.arrow("b", "c")
    f.arrow("c", "d", "예")
    f.arrow("d", "e")
    f.arrow("e", "g", "예", color="#d33f49")
    return f.svg()


def fig_failover():
    f = Fig(760, 330, "그림 5. 플랜 B — 구독 사용 한도 소진 시 자동 전환 (v1.10.0)")
    f.box("a", 20, 50, 150, 50, "AI 호출 직전", "main")
    f.box("b", 210, 40, 170, 70, "한도 기록에 있나?\n(quota.json)", "gate", "diamond")
    f.box("c", 430, 50, 150, 50, "호출 실행", "ai")
    f.box("d", 610, 40, 130, 70, "한도 소진\n응답?", "gate", "diamond")
    f.box("e", 610, 150, 130, 50, "결과 사용", "ok")
    f.box("f", 400, 150, 170, 56, "소진 기록(재개 시각)\n다른 AI 후보 찾기", "main")
    f.box("g", 210, 150, 150, 70, "후보 있나?\n(독립성 규칙 지킴)", "gate", "diamond")
    f.box("h", 20, 150, 150, 56, "역할 교체 후\n같은 호출 재시도", "ok")
    f.box("i", 210, 260, 280, 52, "없음 → 가장 빠른 재개 시각까지 대기\n(최대 1시간 × 8번) 후 재시도", "bad")
    f.arrow("a", "b")
    f.arrow("b", "c", "아니오")
    f.arrow("b", "g", "예", via=[(295, 130)] if False else None)
    f.arrow("c", "d")
    f.arrow("d", "e", "아니오")
    f.arrow("d", "f", "예", via=[(675, 130), (485, 130)])
    f.arrow("f", "g")
    f.arrow("g", "h", "있음", color="#1f9d6b")
    f.arrow("g", "i", "없음", color="#d33f49")
    f.note(20, 345, "결정권자는 대신할 AI가 없어 '사람 확인'으로 갑니다. 결제형 API로는 절대 넘어가지 않습니다.", 10.5, "#475569")
    return f.svg()


def fig_autofix():
    f = Fig(760, 420, "그림 6. 밤샘 자동 수정 — 분석 결과를 하나씩 순서대로")
    f.box("a", 20, 50, 160, 50, "분석 결과\n(근거 확인된 문제)", "io")
    f.box("b", 210, 50, 160, 50, "선택: 심각도·개수\n(체크한 항목)", "main")
    f.box("c", 400, 50, 170, 50, "같은 파일끼리 묶기\n(묶음당 최대 3개)", "main")
    f.box("d", 600, 50, 140, 50, "항목 큐", "main")
    f.box("e", 20, 160, 210, 60, "항목 하나를 run()으로 실행\n(항목당 12번·45분, 파일 8·줄 400 한도)", "ai")
    f.box("f", 280, 160, 150, 70, "결과?", "gate", "diamond")
    f.box("g", 470, 150, 270, 50, "통과 → 브랜치에 커밋\n다음 항목은 그 위에서 시작", "ok")
    f.box("h", 470, 215, 270, 50, "묶음이 실패 → 문제를 하나씩 쪼개\n큐 맨 앞에 넣고 다시 시도", "main")
    f.box("i", 280, 300, 220, 54, "연속 3건 실패 · 한도 · 중지\n→ 남은 항목 건너뛰고 종료", "bad")
    f.box("j", 20, 300, 220, 54, "보고서 + combined.patch\n→ 아침에 사람이 적용", "ok")
    f.arrow("a", "b"); f.arrow("b", "c"); f.arrow("c", "d")
    f.arrow("d", "e", via=[(670, 130), (125, 130)])
    f.arrow("e", "f")
    f.arrow("f", "g", "PASSED", color="#1f9d6b")
    f.arrow("f", "h", "FAILED(묶음)", color="#c98a00")
    f.arrow("f", "i", "계속 실패/한도", color="#d33f49")
    f.arrow("i", "j")
    f.arrow("g", "e", "다음 항목", dash=True, via=[(605, 128), (125, 128)] if False else None)
    return f.svg()


def fig_lab():
    f = Fig(760, 330, "그림 7. 봇 연구실 — 스케줄러가 하는 일 (모두 읽기 전용)")
    f.box("a", 20, 50, 150, 50, "1분마다 확인\n(대시보드 켜진 동안)", "main")
    f.box("b", 210, 40, 150, 70, "평가 주기\n(6시간) 됐나?", "gate", "diamond")
    f.box("c", 400, 50, 170, 56, "최신 가격 받기\n봇의 .venv 파이썬 fetch\n→ 우리 폴더에 저장", "bot")
    f.box("d", 600, 50, 140, 56, "봇 DB 읽기 전용\n(mode=ro)", "bot")
    f.box("e", 600, 150, 140, 70, "채점\nml2: 24시간 뒤 방향\n뉴스: 낙폭 비교", "bot")
    f.box("f", 400, 150, 170, 56, "기록 저장\nhistory/*.json\n→ 화면에 추이", "io")
    f.box("g", 210, 150, 150, 70, "분석 주기\n(24시간) 됐나?", "gate", "diamond")
    f.box("h", 20, 150, 150, 56, "3개 AI 읽기 전용\n개선 분석 시작", "ai")
    f.box("i", 20, 250, 340, 54, "표본 30건 미만이면 '결론 아님'만 표시\n코드 변경은 사람이 적용할 때만 (보호 구역 파일은 AI가 못 바꿈)", "gate")
    f.arrow("a", "b"); f.arrow("b", "c", "예"); f.arrow("c", "d"); f.arrow("d", "e"); f.arrow("e", "f"); f.arrow("f", "g"); f.arrow("g", "h", "예")
    f.arrow("h", "i", dash=True)
    return f.svg()


def fig_bot():
    f = Fig(760, 470, "그림 8. ena-bot 논리 구조 — 지금까지 확인된 범위 (코드 전체는 아직 확인 못 함)")
    f.box("cfg", 20, 50, 150, 50, "config.json · .env\n(설정·키, 값은 비공개)", "io")
    f.box("data", 20, 130, 150, 50, "데이터: 가격 CSV\n(3분봉 · 4시간봉)", "io")
    f.box("trend", 220, 50, 190, 70, "추세 엔진 (trend_engine)\n4시간 주기 · trend_cycle\n목표 노출(exposure) 계산", "bot")
    f.box("mult", 220, 150, 190, 70, "배수(mult)\nBTC 200일선 · 뉴스 위험 점수\n→ target_exposure", "bot")
    f.box("risk", 450, 50, 140, 70, "리스크 게이트\nrisk.py", "gate")
    f.box("ord", 450, 150, 140, 70, "주문 실행\nexchange.py\n(Bybit)", "gate")
    f.box("db", 630, 100, 110, 70, "bot.db\nevents · kv", "io")
    f.box("ml", 220, 260, 190, 70, "그림자 학습기 (매매 안 함)\nlearn · learn2 · ml_shadow\nml2_pred: 24시간 뒤 방향 확률", "bot")
    f.box("news", 450, 260, 140, 70, "뉴스 점수\nnews.py\n(LLM→위험 0~1)", "ai")
    f.box("old", 20, 260, 150, 70, "구 3분 엔진 + LLM 필터\nengine · llm_filter\n(지금 이벤트 없음)", "io")
    f.box("svc", 20, 370, 720, 70, "launchd 서비스: com.ena.trend(추세 엔진) · com.ena.dashboard(화면 8787) · com.ena.report(리포트) · com.ena.ollama(로컬 모델 서버)\n확인 필요: 각 모듈이 서로 부르는 정확한 순서 · 청산 규칙 · 학습기가 결정에 쓰이는지 여부", "gate")
    f.arrow("cfg", "trend"); f.arrow("data", "trend", via=[(130, 85)] if False else None)
    f.arrow("trend", "mult"); f.arrow("mult", "risk", via=[(430, 185), (430, 85)]); f.arrow("risk", "ord"); f.arrow("trend", "db", via=[(430, 20), (690, 20)] if False else None, dash=True)
    f.arrow("ord", "db", dash=True); f.arrow("ml", "db", dash=True, via=[(430, 295), (690, 295)])
    f.arrow("news", "mult", "배수", via=[(520, 235), (320, 235)] if False else None)
    return f.svg()


FIGS = {1: fig_system, 2: fig_prepare, 3: fig_loop, 4: fig_stagnation, 5: fig_failover, 6: fig_autofix, 7: fig_lab, 8: fig_bot}


MODDESC = {
    "__main__.py": "`python -m solomon` 진입점", "analysis.py": "읽기 전용 코드 분석 (여러 AI가 독립 분석, 인용 검증, 묶기)",
    "arbiter.py": "결정권자: 모든 관문을 통과한 결과의 채택·반려·사람 확인 판정", "autofix.py": "밤샘 자동 수정: 분석 결과를 하나씩 순서대로 고침",
    "bench.py": "AI 조합 성능 비교 실험 도구", "botlab.py": "봇 연구실: 실계좌 봇의 그림자 신호를 읽기 전용으로 자동 채점",
    "cli.py": "명령줄(run, analyze, autofix, shadow, quota, vision-probe 등)", "context.py": "목표가 가리키는 파일 내용을 계획 AI에게 전달",
    "dashboard.py": "대시보드 서버: 화면·API·스케줄러·작업 실행", "env.py": "종량제 API 키 제거, 로그인된 CLI 찾기",
    "folders.py": "폴더 찾아보기·이름 변경·git 초기화 (터미널 대신)", "gitops.py": "git 작업: 격리 복사본(worktree), 변경 수집, 패치 적용",
    "jsonutil.py": "AI 응답에서 JSON만 꺼내기", "manage.py": "결과함: 사람이 결정할 것(적용·되돌리기·커밋)과 5W1H 설명",
    "mdhtml.py": "사용 가이드용 마크다운→HTML 변환", "orchestrator.py": "핵심 파이프라인: 계획→구현→검증→검수→결정, 실패 처리, 플랜 B",
    "policy.py": "프로젝트별 정책(.solomon/policy.json): 보호 경로·그림자·화면 캡처", "portfolio.py": "프로젝트 카드와 AI별 성능 집계",
    "projects.py": "Solomon이 만든 프로젝트 폴더 생성·휴지통·복원", "prompts.py": "계획·구현·검수 AI에게 보내는 지시문",
    "quota.py": "플랜 B: 사용 한도에 걸린 AI 기록과 재개 시각", "runlog.py": "모든 단계를 events.jsonl에 덧붙여 기록", "runner.py": "AI CLI 프로세스 실행(시간 제한·출력 감시)",
    "schemas.py": "작업 지시서(Order)와 검수 결과 형식 검증", "scope_guard.py": "범위 검사: 지시서 경계를 벗어난 변경 거부", "selftest.py": "가짜 AI로 전체 기능을 점검하는 자체 점검",
    "settings.py": "설정 기본값·solomon.toml 읽기·검증", "shadow.py": "그림자 모드 채점(결정권자·봇 신호)", "status.py": "events.jsonl을 화면용 상태로 해석",
    "tokens.py": "토큰 집계(CLI가 알려주면 정확, 아니면 추정)", "verifier.py": "격리 복사본에서 테스트 명령 실행", "video.py": "유튜브 영상 분석(자막 기반, 인용 검증)",
    "vision.py": "어느 AI가 이미지를 볼 수 있는지 측정", "visual.py": "화면 캡처 관문: 캡처 명령 실행과 결과 분류",
    "providers/__init__.py": "AI 제공자 목록과 생성", "providers/antigravity.py": "Antigravity(agy) CLI 어댑터 (읽기 전용)", "providers/base.py": "제공자 공통 규칙과 오류 종류",
    "providers/claude.py": "Claude Code CLI 어댑터", "providers/codex.py": "Codex CLI 어댑터", "providers/ollama.py": "로컬 Ollama 어댑터(HTTP, 읽기 전용)",
}


def module_table() -> str:
    rows = ["| 파일 | 줄 수 | 맡은 일 |", "|---|---|---|"]
    for f in sorted((ROOT / "solomon").glob("*.py")) + sorted((ROOT / "solomon" / "providers").glob("*.py")):
        if f.name == "__init__.py" and f.parent.name == "solomon":
            continue
        src = f.read_text(encoding="utf-8")
        doc = ast.get_docstring(ast.parse(src)) or ""
        rel = f.relative_to(ROOT / "solomon")
        first = MODDESC.get(str(rel)) or (doc.strip().splitlines()[0][:90] if doc.strip() else "")
        rows.append(f"| `{rel}` | {len(src.splitlines())} | {first.replace('|', '/')} |")
    return "\n".join(rows)


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "release" / "Solomon-코드구조-동작설명서.pdf").resolve()
    md = (ROOT / "docs" / "Solomon-코드구조-동작설명서.md").read_text(encoding="utf-8").replace("@@MODULES@@", module_table())
    page = mdhtml.print_page(md, __version__, time.strftime("%Y-%m-%d"), "코드 구조 · 동작 설명서", "논리 순서도 · 동작 방식 · 문제 짚는 법")
    for n, fn in FIGS.items():
        page = re.sub(rf"<p>@@FIG:{n}@@</p>", lambda m: fn(), page)
    with tempfile.TemporaryDirectory() as tmp:
        html_file = Path(tmp) / "doc.html"
        html_file.write_text(page, encoding="utf-8")
        script = Path(tmp) / "pdf.js"
        script.write_text(JS, encoding="utf-8")
        out.parent.mkdir(parents=True, exist_ok=True)
        res = subprocess.run(["node", str(script), str(html_file), str(out)], capture_output=True, text=True)
    if res.returncode != 0:
        print("PDF 생성 실패:\n" + res.stderr, file=sys.stderr)
        return 1
    print(f"만들었습니다: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
