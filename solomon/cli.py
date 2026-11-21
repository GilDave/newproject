"""Command line: `solomon run "<goal>" --project PATH` and `solomon doctor`."""

from __future__ import annotations

import argparse
import os
import shlex
import signal
import sys
import tempfile
from pathlib import Path

from . import quota as quota_mod, __version__, analysis as analysis_mod, gitops
from .env import present_api_keys, resolve_cli
from .orchestrator import Orchestrator
from .providers import PROVIDER_CLASSES, build_providers
from .runner import run_process
from .status import load_runs, render
from . import bench as bench_mod
from .settings import ALL_ROLES, ConfigError, load_settings

EXIT = {"PASSED": 0, "FAILED": 1, "BLOCKED_QUOTA": 3, "ERROR": 2, "STOPPED": 130}


def _doctor(args: argparse.Namespace) -> int:
    """Preflight: everything the guide's first step needs, each line saying what to do when it is not ok."""
    import platform
    import shutil

    settings = load_settings(args.config)
    providers = build_providers(settings)
    errors = 0
    print(f"solomon {__version__}  ·  python {platform.python_version()}")
    if sys.version_info < (3, 11):
        print("[error] Python 3.11 이상이 필요합니다. 맥: brew install python@3.12 후 새 가상환경을 만드세요.")
        errors += 1
    else:
        print("[ok] python 3.11+")
    git = shutil.which("git")
    if not git:
        print("[error] git이 없습니다. 맥: xcode-select --install")
        errors += 1
    else:
        mail = gitops.git(["config", "--get", "user.email"], ".", check=False).stdout.strip()
        if mail:
            print("[ok] git (사용자 정보 설정됨)")
        else:
            print('[warn] git 사용자 정보가 없습니다. 대시보드에서 커밋하려면: git config --global user.name "이름" && git config --global user.email "메일"')
    print(f"roles: {settings.roles}")
    keys = present_api_keys()
    if keys:
        print(f"note: {', '.join(keys)} 가 이 셸에 설정돼 있습니다. Solomon은 모든 CLI 호출 전에 이 값을 제거하므로 API 요금은 발생하지 않습니다.")
    needed = set(settings.roles.values())
    for name in PROVIDER_CLASSES:
        if name == "ollama":
            st = providers[name].status()  # type: ignore[attr-defined]
            if st["installed"]:
                print(f"[ok] ollama: 로컬 서버 {st['url']} · 모델 {st['model']} (문맥 {st['num_ctx']:,}) · 비용 없음, 읽기 전용 역할만")
            elif st["running"]:
                print(f"[optional] ollama: 서버는 실행 중이지만 모델 '{st['model']}'이 없습니다 (설치된 모델: {', '.join(st['models']) or '없음'}). 설정 [ollama] model 을 고르거나 ollama pull 하세요.")
            else:
                print(f"[optional] ollama: 서버({st['url']})에 연결되지 않습니다 (선택 사항. Ollama 앱을 실행하면 로컬 AI로 쓸 수 있음).")
            continue
        exe = providers[name].locate()  # type: ignore[attr-defined]
        required = name in needed
        if not exe:
            if required:
                print(f"[missing] {name}: CLI를 찾을 수 없습니다 (현재 역할 배정에 필요). 설치하고 구독 계정으로 로그인하세요.")
                errors += 1
            else:
                print(f"[optional] {name}: 설치 안 됨 (선택 사항. 없어도 동작하지만 3번째 AI 검수·분석은 쓸 수 없습니다).")
            continue
        try:
            res = run_process([exe, "--version"], timeout=15)
            version = (res.stdout or res.stderr).strip().splitlines()[:1]
            print(f"[ok] {name}: {exe} {version[0] if version else ''}")
        except OSError as exc:
            print(f"[error] {name}: {exc}")
            errors += 1
    ytdlp = resolve_cli("yt-dlp")
    print(f"[ok] yt-dlp: {ytdlp} (영상 분석의 자막 수집)" if ytdlp else "[optional] yt-dlp: 설치 안 됨 (영상 분석의 자막 수집에 권장. 없어도 유튜브 자막을 직접 가져오거나 붙여넣을 수 있음. 설치: .venv/bin/python -m pip install yt-dlp)")
    root = Path(settings.projects_root).expanduser()
    probe = root if root.exists() else root.parent
    print(("[ok]" if probe.exists() and os.access(probe, os.W_OK) else "[warn]") + f" 새 프로젝트 폴더 위치: {root}")
    home = Path(os.environ.get("SOLOMON_HOME", str(Path.home() / ".solomon")))
    print(f"[ok] 상태 폴더: {home}" if (home.exists() or os.access(home.parent, os.W_OK)) else f"[warn] 상태 폴더를 만들 수 없습니다: {home}")
    print("로그인 상태는 여기서 확인하지 않습니다. 각 CLI를 직접 한 번 실행해 로그인돼 있는지 확인하세요 (가이드 1-3).")
    print("결과: " + ("문제 없음" if not errors else f"고쳐야 할 항목 {errors}개"))
    return 0 if not errors else 2


def _quota(args: argparse.Namespace) -> int:
    """Show (or clear) which AIs are currently avoided because their subscription usage ran out."""
    import time as _t

    board = quota_mod.QuotaBoard.persistent()
    if args.clear:
        board.clear(None if args.clear == "all" else args.clear)
        print("한도 기록을 지웠습니다:", args.clear)
    snap = board.snapshot()
    if not snap:
        print("지금 한도에 걸려 피하는 AI는 없습니다.")
    for name, v in snap.items():
        print(f"{name}: {_t.strftime('%m-%d %H:%M', _t.localtime(float(v['until'])))} 까지 피합니다 ({v.get('detail', '')[:60]})")
    return 0


def _vision_probe(args: argparse.Namespace) -> int:
    """Which installed AIs can really look at a picture? A test image is shown and the colours it contains are asked for."""
    from . import vision
    from .providers import build_providers

    settings = load_settings(args.config)
    provs = build_providers(settings)
    names = [n for n, p in provs.items() if getattr(p, "image_capable", False)]
    print("이미지 입력이 연결된 AI:", ", ".join(names) or "없음")
    out = vision.probe(provs, names)
    for n, r in out.items():
        print(f"[{'ok' if r['ok'] else '실패'}] {n}" + ("" if r["ok"] else f": {r['error']}"))
    print("통과한 AI만 화면 캡처 관문에서 이미지를 보고 검수합니다. 결과는 ~/.solomon/vision.json 에 저장했습니다.")
    return 0 if any(r["ok"] for r in out.values()) else 1


def _ollama(args: argparse.Namespace) -> int:
    """Check the local Ollama connection with a small decision-style question: reachable? valid JSON? how fast?"""
    import json
    import time

    from .jsonutil import extract_json
    from .providers.ollama import OllamaProvider

    settings = load_settings(args.config)
    p = OllamaProvider(url=settings.ollama_url, model=args.model or settings.ollama_model, num_ctx=settings.ollama_num_ctx,
                       keep_alive=settings.ollama_keep_alive)
    st = p.status()
    print(f"서버: {st['url']} · {'실행 중' if st['running'] else '연결 안 됨'} · 설치된 모델: {', '.join(st['models']) or '없음'}")
    if not st["running"]:
        print("Ollama 앱을 실행한 뒤 다시 시도하세요.")
        return 2
    if not st["installed"]:
        print(f"모델 '{p.model}'이 설치돼 있지 않습니다. 설치: ollama pull {p.model}  (또는 --model 로 설치된 모델을 지정)")
        return 2
    prompt = ("You are a cautious arbiter. Reply with ONLY one JSON object: "
              '{"decision": "ADOPT" | "REJECT" | "NEED_HUMAN", "confidence": number between 0 and 1, "reason": string in Korean}.\n'
              "Evidence: automatic tests passed (40/40); reviewer A said PASS; reviewer B said PASS; reviewer C said FAIL only because a variable name "
              "is unclear (style, not a bug); the change touches 2 files, +18 -4 lines, none of them in order execution or risk code.\n"
              "Decide whether to adopt the change.")
    results, rounds = [], args.rounds
    for i in range(rounds):
        t0 = time.time()
        try:
            r = p.run(prompt, ".", False, timeout=240)
            data = extract_json(r.text)
            ok = data.get("decision") in ("ADOPT", "REJECT", "NEED_HUMAN")
            results.append((ok, time.time() - t0, (r.tokens or {}).get("total", 0), data))
            print(f"  시도 {i + 1}: {'유효' if ok else '형식 이상'} · {time.time() - t0:.1f}초 · 토큰 {(r.tokens or {}).get('total', '?')} · "
                  f"{data.get('decision')} (확신 {data.get('confidence')}) {str(data.get('reason', ''))[:60]}")
        except Exception as exc:  # report, do not crash: this is a diagnostic
            results.append((False, time.time() - t0, 0, {}))
            print(f"  시도 {i + 1}: 실패 · {exc}")
    valid = sum(1 for r in results if r[0])
    avg = sum(r[1] for r in results) / max(len(results), 1)
    same = len({json.dumps(r[3].get("decision")) for r in results if r[0]}) <= 1
    print(f"결과: 유효한 JSON {valid}/{rounds} · 평균 {avg:.1f}초 · 같은 질문에 같은 결정 {'예' if same else '아니오 (불안정)'}")
    print("첫 시도는 모델을 메모리에 올리느라 느릴 수 있습니다. 결정이 불안정하거나 형식 이상이 있으면 더 큰 모델이나 다수결이 필요합니다.")
    return 0 if valid == rounds else 1


def _init(args: argparse.Namespace) -> int:
    """Write an editable solomon.toml next to where Solomon is run."""
    import importlib.resources as res

    dest = Path(args.path or "solomon.toml")
    if dest.exists() and not args.force:
        print(f"error: {dest} 가 이미 있습니다 (덮어쓰려면 --force).", file=sys.stderr)
        return 2
    dest.write_text(res.files("solomon").joinpath("solomon.example.toml").read_text(encoding="utf-8"), encoding="utf-8")
    print(f"설정 파일을 만들었습니다: {dest.resolve()}  (필요한 줄의 # 를 지우고 고치세요)")
    return 0


def _shadow(args: argparse.Namespace) -> int:
    import json

    from . import shadow

    if args.bot_db:
        csv_path = Path(args.candles) if args.candles else None
        out = shadow.evaluate_bot_filter(Path(args.bot_db), csv_path, bars=args.bars, fee=args.fee)
        out["signals"] = shadow.evaluate_bot_signals(Path(args.bot_db), csv_path)
    else:
        out = shadow.evaluate(Path(args.project or "."))
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def _selftest(args: argparse.Namespace) -> int:
    from .selftest import run_selftest

    return run_selftest()


def _run(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    if args.on_quota:
        settings.on_quota = args.on_quota
        settings.validate()
    if args.persist:
        settings.max_attempts = 0  # keep going until it passes or a budget / the subscription runs out
    if args.max_attempts is not None:
        settings.max_attempts = args.max_attempts
    if args.max_minutes is not None:
        settings.max_run_minutes = args.max_minutes
    if args.max_tokens is not None:
        settings.max_tokens = args.max_tokens
    if args.no_double_check:
        settings.double_check_tests = False
    if args.no_baseline:
        settings.baseline_check = False
    if getattr(args, "shadow", False):
        settings.arbiter_shadow = True
    settings.validate()
    for role in ALL_ROLES:
        chosen = getattr(args, role, None)
        if chosen:
            settings.roles[role] = chosen  # validated by the Orchestrator (known AI, builder can write, independence)
    if args.no_tests:
        settings.test_command = None
    elif args.test_command:
        settings.test_command = shlex.split(args.test_command)
    orch = Orchestrator(settings, quota_board=quota_mod.QuotaBoard.persistent())
    project = Path(args.project or settings.project_path or ".").expanduser()

    def _stop(signum, frame):  # the dashboard's stop button sends SIGTERM
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _stop)
    from . import attachments

    try:
        pictures = [str(p) for p in attachments.validate_files(args.image)]
    except attachments.AttachError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        return 2
    extra = {"images": pictures} if pictures else {}
    result = orch.run(args.goal, project, keep_worktree=args.keep_worktree, **extra)
    print(f"run:      {result.run_id}")
    print(f"status:   {result.status}")
    print(f"attempts: {result.attempts}")
    print(f"summary:  {result.summary}")
    for w in result.warnings:
        print(f"warning:  {w}")
    print(f"log:      {result.run_dir}/events.jsonl")
    if result.status == "PASSED" and result.patch_path:
        print(f"patch:    {result.patch_path}")
        if args.apply:
            gitops.apply_patch(project.resolve(), Path(result.patch_path))
            print("applied the patch to the project working tree (not committed).")
        else:
            print(f"apply it with: git -C {project} apply {result.patch_path}")
    return EXIT.get(result.status, 2)


def _analyze(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    project = Path(args.project or settings.project_path or ".").expanduser()
    ais = [a for a in (args.ais or "").split(",") if a] or None

    def _stop(signum, frame):  # the dashboard's stop button sends SIGTERM
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _stop)
    result = analysis_mod.run_analysis(settings, project, args.focus or "", ais)
    print(f"analysis: {result.analysis_id}")
    print(f"status:   {result.status}")
    print(f"summary:  {result.summary}")
    cov = result.coverage
    if cov:
        print(f"files:    {cov.get('included')} of {cov.get('total')} analysed ({cov.get('source')}), {cov.get('redactions')} secret-like values hidden")
    for name, info in result.ais.items():
        print(f"  {name:<12} {'ok' if info.get('ok') else 'FAILED: ' + str(info.get('error', ''))}")
    for c in result.clusters[:10]:
        where = f"{c['file']}:{c['line_start']}" if c["file"] else "?"
        mark = "verified" if c["verified"] else "UNVERIFIED"
        print(f"  [{c['severity']:<6}] {c['agreement']}/{len(result.ais)} AIs  {where}  {c['members'][0]['title']}  ({mark})")
    if result.report_path:
        print(f"report:   {result.report_path}")
    return {"DONE": 0, "FAILED": 1, "STOPPED": 130}.get(result.status, 2)


def _autofix(args: argparse.Namespace) -> int:
    from . import autofix as autofix_mod

    settings = load_settings(args.config)
    if args.no_tests:
        settings.test_command = None
    elif args.test_command:
        settings.test_command = shlex.split(args.test_command)
    for role in ALL_ROLES:
        chosen = getattr(args, role, None)
        if chosen:
            settings.roles[role] = chosen
    settings.validate()
    project = Path(args.project or settings.project_path or ".").expanduser()

    def _stop(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _stop)
    try:
        res = autofix_mod.run_autofix(
            settings, project, args.analysis, min_severity=args.min_severity, min_agreement=args.min_agreement,
            verified_only=not args.include_unverified, max_fixes=args.max_fixes, budget_minutes=args.budget_minutes,
            allow_untested=args.allow_untested, ids=[int(i) for i in args.ids.split(',') if i.strip()] if args.ids else None,
            group=args.group_by_file)
    except (autofix_mod.AutofixError, ConfigError, gitops.GitError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"batch:   {res.batch_id}\nstatus:  {res.status}\nsummary: {res.summary}")
    for i, it in enumerate(res.items, 1):
        print(f"  {i}. {it.status:<13} [{it.severity}] {it.title}")
    if res.branch and res.patch_path:
        print(f"branch:  {res.branch}")
    print(f"report:  {res.report_path}")
    try:
        from . import alerts as alerts_mod
        alerts_mod.notify_job_end(f"🏁 Solomon 밤샘 수정 {res.status}", res.summary + ("\n관리 탭에서 확인하고 적용하세요." if res.patch_path else ""))
    except Exception:
        pass
    return {"DONE": 0, "NOTHING": 0, "PARTIAL": 1, "FAILED": 1, "STOPPED": 130}.get(res.status, 2)


def _report_chain(args: argparse.Namespace) -> int:
    from . import autofix as autofix_mod, alerts as alerts_mod

    settings = load_settings(args.config)
    if args.test_command:
        settings.test_command = shlex.split(args.test_command)
    for role in ALL_ROLES:
        chosen = getattr(args, role, None)
        if chosen:
            settings.roles[role] = chosen
    settings.validate()
    project = Path(args.project or settings.project_path or ".").expanduser()

    def _stop(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _stop)
    try:
        res = autofix_mod.run_task_chain(settings, project, args.id, [int(i) for i in args.tasks.split(",") if i.strip()] if args.tasks else None,
                                         budget_minutes=args.budget_minutes,
                                         report_project=Path(args.report_project).expanduser() if args.report_project else None)
    except (autofix_mod.AutofixError, ConfigError, gitops.GitError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"chain:   {res.batch_id}\nstatus:  {res.status}\nsummary: {res.summary}")
    for i, it in enumerate(res.items, 1):
        print(f"  {i}. {it.status:<13} {it.title}")
    if res.branch and res.patch_path:
        print(f"branch:  {res.branch}")
    print(f"report:  {res.report_path}")
    try:  # a phone message when the night is over (only when Bark alerts are set up; never fails the job)
        alerts_mod.notify_job_end(f"🏁 Solomon 순차 실행 {res.status}", res.summary + ("\n관리 탭에서 확인하고 적용하세요." if res.patch_path else ""))
    except Exception:
        pass
    return {"DONE": 0, "NOTHING": 0, "PARTIAL": 1, "FAILED": 1, "STOPPED": 130}.get(res.status, 2)


def _report(args: argparse.Namespace) -> int:
    from . import report as report_mod

    settings = load_settings(args.config)
    ais = [a for a in (args.ais or "").split(",") if a] or None

    def _stop(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _stop)
    try:
        project = Path(args.project or settings.project_path or ".").expanduser()
        res = report_mod.run_report(settings, project, args.question, ais, use_project_context=args.context, merger=args.merger)
    except (report_mod.ReportError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"report:   {res['report_id']}\nstatus:   {res['status']}\nsummary:  {res['summary']}")
    for name, info in res["ais"].items():
        print(f"  {name:<12} {'ok' if info.get('ok') else 'FAILED: ' + str(info.get('error', ''))}")
    if res.get("report_path"):
        print(f"file:     {res['report_path']}")
    return 0 if res["status"] == "DONE" else 1


def _report_tasks(args: argparse.Namespace) -> int:
    from . import report as report_mod

    settings = load_settings(args.config)

    def _stop(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _stop)
    try:
        project = Path(args.project or settings.project_path or ".").expanduser()
        out = report_mod.plan_tasks(settings, project, args.id, args.ai)
    except (report_mod.ReportError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"report:   {out['report_id']}\nwritten by {out['provider']}: {len(out['tasks'])} tasks")
    for t in out["tasks"]:
        print(f"  {t['id']}. [{t['size']}] {t['title']}" + (f" (after {t['depends_on']})" if t["depends_on"] else ""))
    return 0


def _video(args: argparse.Namespace) -> int:
    from . import video as video_mod

    settings = load_settings(args.config)
    ais = [a for a in (args.ais or "").split(",") if a] or None

    def _stop(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _stop)
    try:
        if args.probe:
            res = video_mod.probe_all(settings, args.url, ais)
            video_mod.save_capability(res)
            print(f"영상: {res['video_id']} · 실제 제목: {res['truth_title'] or '(가져오지 못함)'}")
            for name, info in res["ais"].items():
                print(f"  {name:<12} {video_mod.PROBE_KO.get(info['status'], info['status'])}"
                      + (f" · AI가 말한 제목: {info.get('title')}" if info.get("title") else "") + (f" · {info.get('detail')}" if info.get("detail") else ""))
            print("confirmed(직접 시청 확인됨)인 AI만 '직접 시청'으로 쓰세요. guessed는 영상을 보지 않고 지어낸 것입니다.")
            return 0
        text = None
        if args.transcript_file:
            tf = Path(args.transcript_file).expanduser()
            text = tf.read_text(encoding="utf-8", errors="replace")
            if tf.name.startswith("video_input-"):
                tf.unlink(missing_ok=True)  # the dashboard's temporary copy of a pasted transcript
        project = Path(args.project or settings.project_path or ".").expanduser()
        res = video_mod.run_video(settings, project, args.url, args.focus or "", ais, transcript_text=text, use_project_context=args.context,
                                  direct=[a for a in (args.direct or "").split(",") if a])
    except (video_mod.VideoError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"analysis: {res['analysis_id']}\nstatus:   {res['status']}\nsummary:  {res['summary']}")
    v = res["video"]
    if v.get("title"):
        print(f"video:    {v['title']} ({v.get('author') or '-'}) · 자막 {v.get('lang') or '?'} · {v.get('source')}")
    for name, info in res["ais"].items():
        print(f"  {name:<12} {'ok' if info.get('ok') else 'FAILED: ' + str(info.get('error', ''))}")
    for c in res["clusters"][:12]:
        m = c["members"][0]
        print(f"  [{video_mod.KIND_KO[c['kind']]}] {c['agreement']}/{len(res['ais'])} AIs {video_mod.stamp(c['time'])} {m['text'][:70]} ({'verified' if c['verified'] else 'UNVERIFIED'})")
    print(f"report:   {res.get('report_path')}")
    return {"DONE": 0, "FAILED": 1, "STOPPED": 130}.get(res["status"], 2)


def _runtests(args: argparse.Namespace) -> int:
    """Run the project's test command in the working folder (used by the dashboard's test button)."""
    from .verifier import verify

    settings = load_settings(args.config)
    if args.test_command:
        settings.test_command = shlex.split(args.test_command)
    project = Path(args.project or settings.project_path or ".").expanduser()
    from .verifier import environment_hint, resolve_python

    settings.test_command = resolve_python(settings.test_command, project)
    res = verify(settings, project)
    print(f"명령: {res.command or '(없음)'}")
    print(res.output_tail + (environment_hint(res.command, res.output_tail) if not res.passed else ""))
    print("결과: " + ("통과" if res.passed and not res.skipped else "테스트 없음" if res.skipped else f"실패 (종료 코드 {res.exit_code})"))
    return 0 if res.passed else 1


def _status(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    project = Path(args.project or settings.project_path or ".").expanduser().resolve()
    runs = load_runs(project, settings.state_dir)
    if args.run:
        runs = [r for r in runs if r.run_id.startswith(args.run)]
        if not runs:
            print(f"error: no run starting with '{args.run}'", file=sys.stderr)
            return 2
    print(render(runs, project, limit=args.limit))
    return 0


def _bench(args: argparse.Namespace) -> int:
    tasks_dir = Path(args.tasks)
    if not tasks_dir.is_dir():
        print(f"error: task folder not found: {tasks_dir} (run from the repository root or pass --tasks)", file=sys.stderr)
        return 2
    out = Path(args.out)
    if args.action == "report":
        print(bench_mod.format_report(bench_mod.load_results(out)))
        return 0
    only = [t for t in (args.only or "").split(",") if t]
    tasks = bench_mod.load_tasks(tasks_dir, only or None)
    if args.action == "validate":
        with tempfile.TemporaryDirectory() as scratch:
            problems = bench_mod.validate_tasks(tasks, Path(scratch))
        for p in problems:
            print(f"[problem] {p}")
        print(f"{len(tasks)} task(s) checked, {len(problems)} problem(s).")
        return 1 if problems else 0
    settings = load_settings(args.config)
    names = [c for c in (args.configs or ",".join(bench_mod.DEFAULT_CONFIGS)).split(",") if c]
    for name in names:  # fail early on a typo, before any quota is spent
        bench_mod.settings_for(settings, name)
    problems = bench_mod.validate_tasks(tasks, Path(tempfile.mkdtemp()))
    if problems:
        print("\n".join(f"[problem] {p}" for p in problems), file=sys.stderr)
        return 2
    records = bench_mod.run_bench(tasks, names, out, settings, repeat=args.repeat, keep=args.keep)
    print()
    print(bench_mod.format_report(records))
    return 3 if any(r["status"] == "BLOCKED_QUOTA" for r in records) else 0


def _dashboard(args: argparse.Namespace) -> int:
    from . import dashboard

    settings = load_settings(args.config)
    return dashboard.serve(settings, args.project or [], args.config, args.port, not args.no_open)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="solomon", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run the cross-verified pipeline for one goal")
    run.add_argument("goal", help="one-line goal")
    run.add_argument("--project", help="local path of the git project (default: `project` in solomon.toml, else the current folder)")
    run.add_argument("--config", help="path to solomon.toml")
    run.add_argument("--apply", action="store_true", help="apply the passed patch to the working tree")
    run.add_argument("--keep-worktree", action="store_true", help="keep the isolated worktree for inspection")
    run.add_argument("--on-quota", choices=["stop", "wait"], help="override loop.on_quota")
    for role in ALL_ROLES:
        run.add_argument(f"--{role}", help=f"which AI takes the {role} role for this run (overrides the config)")
    run.add_argument("--test-command", help="test command for this run, e.g. 'npm test' (overrides the config)")
    run.add_argument("--image", action="append", default=[], metavar="FILE", help="a screenshot of the problem (repeatable, up to 4): AIs that can look at images see it")
    run.add_argument("--shadow", action="store_true", help="shadow mode: the arbiter judges every attempt but is never obeyed (answers are only recorded and scored)")
    run.add_argument("--persist", action="store_true", help="no attempt limit: keep trying until it passes or a budget runs out")
    run.add_argument("--max-attempts", type=int, help="attempt limit (0 = none)")
    run.add_argument("--max-minutes", type=float, help="wall-clock budget of the run in minutes")
    run.add_argument("--max-tokens", type=int, help="token budget of the run")
    run.add_argument("--no-baseline", action="store_true", help="skip the test run on the untouched code (for a brand-new project)")
    run.add_argument("--no-double-check", action="store_true", help="do not re-run the tests before declaring a pass")
    run.add_argument("--no-tests", action="store_true", help="run without automatic tests (the result is UNVERIFIED)")
    run.set_defaults(func=_run)

    vd = sub.add_parser("video", help="analyse a YouTube video with up to three AIs (transcript-based, quotes are verified); --probe tests which AI can open a video")
    vd.add_argument("url", help="YouTube video address")
    vd.add_argument("--project", help="where the result is stored (default: `project` in solomon.toml, else the current folder)")
    vd.add_argument("--focus", help="what to look for, e.g. 'risk rules and numbers to verify'")
    vd.add_argument("--ais", help="comma-separated AIs to use (default: every installed one)")
    vd.add_argument("--direct", help="comma-separated AIs that may also open the video themselves (check with --probe first)")
    vd.add_argument("--transcript-file", help="use this transcript (text, VTT or SRT) instead of fetching one")
    vd.add_argument("--context", action="store_true", help="also ask for ideas that fit the project (sends its file list and README start)")
    vd.add_argument("--probe", action="store_true", help="test which AIs can really open the video (compares the title they give with YouTube's)")
    vd.add_argument("--config", help="path to solomon.toml")
    vd.set_defaults(func=_video)

    rp = sub.add_parser("report", help="answer a question as a document: several AIs write drafts independently, one merges them (read-only)")
    rp.add_argument("question", help="the question, e.g. 'how to train the bot's model from YouTube captions: report and implementation plan'")
    rp.add_argument("--project", help="where the report is stored (default: `project` in solomon.toml, else the current folder)")
    rp.add_argument("--ais", help="comma-separated AIs to use (default: every installed one)")
    rp.add_argument("--context", action="store_true", help="also send the project's file list and README start so the plan is concrete")
    rp.add_argument("--merger", help="the AI that merges the drafts into one report (default: the first of claude, codex, antigravity that wrote a draft)")
    rp.add_argument("--config", help="path to solomon.toml")
    rp.set_defaults(func=_report)

    rt = sub.add_parser("report-tasks", help="split a finished report into small improvement tasks (one AI, read-only); the dashboard's report tab runs them one by one")
    rt.add_argument("--id", required=True, help="the report number")
    rt.add_argument("--project", help="the project the report is stored in")
    rt.add_argument("--ai", help="which AI splits it (default: the first installed one of claude, codex, antigravity)")
    rt.add_argument("--config", help="path to solomon.toml")
    rt.set_defaults(func=_report_tasks)

    ini = sub.add_parser("init", help="write an editable solomon.toml (all options, commented)")
    ini.add_argument("--path", help="where to write it (default: ./solomon.toml)")
    ini.add_argument("--force", action="store_true")
    ini.set_defaults(func=_init)

    ol = sub.add_parser("ollama", help="check the local Ollama connection (reachable? model installed? valid JSON? how fast?)")
    ol.add_argument("--model", help="model to test (default: [ollama] model, qwen3:8b)")
    ol.add_argument("--rounds", type=int, default=3, help="how many times to ask the same question")
    ol.add_argument("--config", help="path to solomon.toml")
    ol.set_defaults(func=_ollama)

    sh = sub.add_parser("shadow", help="score the shadow-mode arbiter against what really happened (read-only)")
    sh.add_argument("--project", help="project folder whose shadow judgements to score (default: current folder)")
    sh.add_argument("--bot-db", help="instead: a live bot's SQLite file; evaluates its own LLM filter (opened read-only)")
    sh.add_argument("--candles", help="candle CSV (ts_ms/timestamp + close) for the counterfactual outcome of --bot-db decisions")
    sh.add_argument("--bars", type=int, default=10, help="holding period in candles for the counterfactual (default 10)")
    sh.add_argument("--fee", type=float, default=0.0008, help="round-trip cost subtracted from each counterfactual return (default 0.0008)")
    sh.set_defaults(func=_shadow)

    qp = sub.add_parser("quota", help="show which AIs are avoided because their usage limit was reached (plan B routes around them)")
    qp.add_argument("--clear", help="forget a limit: an AI name, or 'all' (for when you know it has reset)")
    qp.set_defaults(func=_quota)

    vp = sub.add_parser("vision-probe", help="test which AIs can really look at an image (needed for the screen-capture gate)")
    vp.add_argument("--config", help="path to solomon.toml")
    vp.set_defaults(func=_vision_probe)

    stt = sub.add_parser("selftest", help="check the installation end to end with fake AIs (no subscription use, no tokens)")
    stt.set_defaults(func=_selftest)

    doc = sub.add_parser("doctor", help="preflight: Python, git, the AI CLIs, folders")
    doc.add_argument("--config", help="path to solomon.toml")
    doc.set_defaults(func=_doctor)

    an = sub.add_parser("analyze", help="read-only analysis of a project by several AIs (never modifies it)")
    an.add_argument("--project", help="local path of the git project (default: `project` in solomon.toml, else the current folder)")
    an.add_argument("--focus", help="what to look at, e.g. 'places that can crash' (default: a general analysis)")
    an.add_argument("--ais", help="comma-separated AIs to use (default: every installed one)")
    an.add_argument("--config", help="path to solomon.toml")
    an.set_defaults(func=_analyze)

    af = sub.add_parser("autofix", help="fix the findings of an analysis one after another, unattended (own branch, original untouched)")
    af.add_argument("--project")
    af.add_argument("--config")
    af.add_argument("--analysis", help="analysis id (default: the latest)")
    af.add_argument("--min-severity", choices=["low", "medium", "high"], default="medium")
    af.add_argument("--min-agreement", type=int, default=1, help="how many AIs must have reported the finding")
    af.add_argument("--include-unverified", action="store_true", help="also fix findings whose quoted evidence was not found")
    af.add_argument("--max-fixes", type=int, default=5, help="0 = no cap (every selected finding)")
    af.add_argument("--ids", help="fix exactly these findings of the analysis, e.g. 1,3,7 (the numbers in the report)")
    af.add_argument("--group-by-file", action="store_true", help="fix all findings of one file in a single run (faster overnight)")
    af.add_argument("--budget-minutes", type=float, default=360.0)
    af.add_argument("--test-command")
    af.add_argument("--no-tests", action="store_true")
    af.add_argument("--allow-untested", action="store_true")
    for role in ALL_ROLES:
        af.add_argument(f"--{role}")
    af.set_defaults(func=_autofix)

    rc = sub.add_parser("report-chain", help="build the tasks of a report one after another, unattended (own branch, original untouched)")
    rc.add_argument("--id", required=True, help="the report number")
    rc.add_argument("--project")
    rc.add_argument("--config")
    rc.add_argument("--tasks", help="only these task numbers, e.g. 1,2,4 (default: every task not applied yet)")
    rc.add_argument("--report-project", help="the project the report is stored in, when the tasks are built in another one (--project)")
    rc.add_argument("--budget-minutes", type=float, default=360.0)
    rc.add_argument("--test-command")
    for role in ALL_ROLES:
        rc.add_argument(f"--{role}")
    rc.set_defaults(func=_report_chain)

    rt = sub.add_parser("runtests", help="run the project's test command in its working folder")
    rt.add_argument("--project")
    rt.add_argument("--config")
    rt.add_argument("--test-command")
    rt.set_defaults(func=_runtests)

    st = sub.add_parser("status", help="show progress of recent runs (read-only)")
    st.add_argument("--project", help="local path of the project (default: `project` in solomon.toml, else the current folder)")
    st.add_argument("--config", help="path to solomon.toml")
    st.add_argument("--run", help="show the run whose id starts with this text")
    st.add_argument("--limit", type=int, default=5, help="how many recent runs to list")
    st.set_defaults(func=_status)

    db = sub.add_parser("dashboard", help="open the local web dashboard (watch runs and give commands)")
    db.add_argument("--project", action="append", help="project folder to show (repeatable)")
    db.add_argument("--port", type=int, default=8765)
    db.add_argument("--no-open", action="store_true", help="do not open the browser")
    db.add_argument("--config", help="path to solomon.toml")
    db.set_defaults(func=_dashboard)

    bn = sub.add_parser("bench", help="G2 experiment: compare single-model and cross-review configurations")
    bn.add_argument("action", choices=["validate", "run", "report"])
    bn.add_argument("--tasks", default="bench/tasks", help="folder with the experiment tasks")
    bn.add_argument("--only", help="comma-separated task ids or prefixes (default: all)")
    bn.add_argument("--configs", help=f"comma-separated, default: {', '.join(bench_mod.DEFAULT_CONFIGS)}; also: {', '.join(c for c in bench_mod.CONFIGS if c not in bench_mod.DEFAULT_CONFIGS)}")
    bn.add_argument("--repeat", type=int, default=1, help="repetitions of every task and config")
    bn.add_argument("--out", default="bench_results", help="folder for results.jsonl and run logs")
    bn.add_argument("--keep", action="store_true", help="keep each working repository for inspection")
    bn.add_argument("--config", help="path to solomon.toml")
    bn.set_defaults(func=_bench)
    return parser


from . import joblock  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    held = None
    if getattr(args, "command", None) in joblock.JOB_COMMANDS and not getattr(args, "probe", False):
        held = joblock.acquire()
        if held is None:
            print(f"error: {joblock.BUSY}", file=sys.stderr)
            return 2
    try:
        return args.func(args)
    except (ConfigError, gitops.GitError, ValueError) as exc:  # user mistakes: show a message, not a traceback
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        from .runner import kill_active

        kill_active()
        print("stopped.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
