import re
import shutil
import subprocess
from pathlib import Path

import pytest

from solomon import cli
from test_roles import env, run_body, sleeper  # noqa: F401

HTML = (Path(__file__).resolve().parent.parent / "solomon" / "dashboard.html").read_text(encoding="utf-8")
SCRIPT = re.search(r"<script>(.*)</script>", HTML, re.S).group(1)


def test_every_static_button_has_a_handler():
    ids = re.findall(r'<button[^>]*\bid="([\w-]+)"', HTML)
    assert len(ids) >= 15
    missing = [i for i in ids if f'$("{i}").onclick' not in SCRIPT and f'getElementById("{i}").onclick' not in SCRIPT]
    assert not missing, f"buttons without a click handler: {missing}"


def test_every_selector_and_input_the_script_uses_exists_in_the_markup():
    used = set(re.findall(r'\$\("([\w-]+)"\)', SCRIPT))
    defined = set(re.findall(r'\bid="([\w-]+)"', HTML))
    dynamic_prefix = ("r-", "ai-", "pc-")  # built at run time
    missing = sorted(u for u in used if u not in defined and not u.startswith(dynamic_prefix))
    assert not missing, f"script refers to ids that are not on the page: {missing}"
    for r in ["planner", "planner2", "planner3", "builder", "builder2", "builder3", "reviewer", "reviewer2", "reviewer3"]:
        assert f'id="r-{r}"' in HTML


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_page_script_is_valid_javascript(tmp_path):
    f = tmp_path / "page.js"
    f.write_text(SCRIPT, encoding="utf-8")
    res = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


def test_all_four_tabs_and_the_cross_links_exist():
    for tab in ("dash", "work", "mg", "pj"):
        assert f'id="tab-{tab}"' in HTML and f'id="view-{tab}"' in HTML
    for link in ("openProject(", "goNext(", "w-to-mg", "w-to-pj", "mgProjectSel", "pjNew", "관리 탭에서 5W1H"):
        assert link in HTML, link


def test_cli_has_a_flag_for_every_role_slot():
    parser = cli.build_parser()
    run = next(a for a in parser._actions if a.dest == "command").choices["run"]
    flags = {o for a in run._actions for o in a.option_strings}
    for r in ["planner", "planner2", "planner3", "builder", "builder2", "builder3", "reviewer", "reviewer2", "reviewer3"]:
        assert f"--{r}" in flags


def test_dashboard_passes_all_slots_and_rejects_bad_combinations(env):
    st, _, _ = env.call("POST", "/api/run", run_body(env, planner2="antigravity", builder2="antigravity", reviewer2="antigravity",
                                                     reviewer3="claude"))
    assert st == 200
    args = sleeper.calls[-1]
    for flag, val in (("--planner2", "antigravity"), ("--builder2", "antigravity"), ("--reviewer2", "antigravity"), ("--reviewer3", "claude")):
        assert args[args.index(flag) + 1] == val
    env.call("POST", "/api/stop", {})
    for roles in ({"reviewer3": "codex"},                      # codex is the default builder
                  {"builder2": "claude", "reviewer": "claude"}):  # claude would be its own only reviewer when it builds
        sleeper.calls.clear()
        st, body, _ = env.call("POST", "/api/run", run_body(env, **roles))
        assert st == 400 and not sleeper.calls, (roles, body)
    assert env.call("POST", "/api/run", run_body(env, builder3="nope"))[0] == 400
    assert env.call("POST", "/api/run", run_body(env, planner=""))[0] == 400  # a required slot cannot be empty


def test_an_optional_slot_cleared_on_the_page_is_not_passed(env):
    st, _, _ = env.call("POST", "/api/run", run_body(env, planner2="", reviewer2=""))
    assert st == 200 and "--planner2" not in sleeper.calls[-1] and "--reviewer2" not in sleeper.calls[-1]


def test_the_analysis_report_can_start_a_whole_batch_in_one_click():
    for needle in ('id = "fixGo"', "/api/autofix", "ids: [...sel]", "group: fixOpts.group", "analysis_id: aid", "전체 선택", "근거 확인된 것만",
                   "옛 버전입니다"):
        assert needle in HTML, needle
    assert 'id="bf-g"' in HTML and 'value="0">전부' in HTML
