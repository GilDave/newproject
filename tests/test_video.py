import json
import os
import stat
import sys
from pathlib import Path

import pytest

from solomon import video as V
from solomon.providers import ProviderError
from solomon.settings import Settings

from helpers import ScriptedProvider, make_repo

VID = "dQw4w9WgXcQ"
URL = f"https://youtu.be/{VID}"

VTT = """WEBVTT
Kind: captions
Language: ko

00:00:01.000 --> 00:00:03.000 align:start position:0%
안녕하세요 오늘은 <c>자동매매</c> 손절 규칙을

00:00:03.000 --> 00:00:05.000 align:start position:0%
안녕하세요 오늘은 자동매매 손절 규칙을
이야기해 보겠습니다

00:00:05.000 --> 00:00:08.000 align:start position:0%
이야기해 보겠습니다
손절은 진입가 대비 2퍼센트로 고정하는 게 가장 좋습니다

00:01:30.500 --> 00:01:34.000
&amp; 수수료와 슬리피지를 반드시 계산하세요
"""

SEGS = [(1.0, "안녕하세요 오늘은 자동매매 손절 규칙을 이야기해 보겠습니다"),
        (5.0, "손절은 진입가 대비 2퍼센트로 고정하는 게 가장 좋습니다"),
        (90.5, "수수료와 슬리피지를 반드시 계산하세요"),
        (200.0, "마지막으로 백테스트 결과를 꼭 표본 외 구간에서 확인하세요")]


def tr(segs=None, **kw):
    return V.Transcript(VID, segs or SEGS, kw.pop("source", "pasted"), **kw)


# ------------------------------------------------------------------ url
@pytest.mark.parametrize("url", [URL, f"https://www.youtube.com/watch?v={VID}&t=10s", f"https://m.youtube.com/watch?v={VID}",
                                 f"https://www.youtube.com/shorts/{VID}", f"https://www.youtube.com/live/{VID}?feature=share"])
def test_valid_urls(url):
    assert V.parse_video_url(url) == VID


@pytest.mark.parametrize("url", ["", "ftp://youtu.be/" + VID, "https://evil.example/watch?v=" + VID, f"https://youtube.com.evil.io/watch?v={VID}",
                                 "https://www.youtube.com/playlist?list=PL123", "https://www.youtube.com/@channel", "https://youtu.be/short",
                                 "javascript:alert(1)", f"https://www.youtube.com/watch?v={VID}x"])
def test_refused_urls(url):
    with pytest.raises(V.VideoError):
        V.parse_video_url(url)


def test_http_get_only_talks_to_youtube_over_https():
    for bad in ("http://www.youtube.com/x", "https://example.com/x", "https://www.youtube.com.evil.io/x", "file:///etc/passwd"):
        with pytest.raises(V.VideoError):
            V.http_get(bad)


# ------------------------------------------------------------------ parsers
def test_vtt_drops_the_repeated_lines_of_automatic_captions_and_cleans_markup():
    segs = V.parse_vtt(VTT)
    assert [s[0] for s in segs] == [1.0, 3.0, 5.0, 90.5]
    texts = " ".join(s[1] for s in segs)
    assert texts.count("안녕하세요") == 1 and "<c>" not in texts and "& 수수료" in texts
    assert "이야기해 보겠습니다" in segs[1][1] and segs[2][1].startswith("손절은")  # the repeated tail line was not emitted twice


def test_srt_and_json3_and_xml():
    srt = "1\n00:00:01,000 --> 00:00:02,500\nhello world\n\n2\n01:02:03,250 --> 01:02:04,000\nlate line\n"
    assert V.parse_vtt(srt) == [(1.0, "hello world"), (3723.25, "late line")]
    j = {"events": [{"tStartMs": 1500, "segs": [{"utf8": "안녕"}, {"utf8": " 하세요"}]}, {"tStartMs": 4000, "segs": [{"utf8": "\n"}]}, {"tStartMs": 9000, "segs": [{"utf8": "둘째"}]}]}
    assert V.parse_json3(j) == [(1.5, "안녕 하세요"), (9.0, "둘째")]
    xml = '<?xml version="1.0"?><transcript><text start="0.5" dur="2">a &amp;amp; b</text><text start="3" dur="1">second &#39;x&#39;</text></transcript>'
    assert V.parse_timedtext_xml(xml) == [(0.5, "a & b"), (3.0, "second 'x'")]


def test_pasted_transcripts_with_and_without_times():
    timed = V.parse_plain("[0:05] 첫 문장입니다 아주 길게\n[0:40] 둘째 문장입니다 역시 길게\n1:02:03 셋째")
    assert [t for t, _ in timed] == [5, 40, 3723] and timed[0][1].startswith("첫 문장")
    plain = V.parse_plain("그냥 붙여넣은 글입니다.\n\n두 번째 문단입니다.")
    assert [t for t, _ in plain] == [None, None] and len(plain) == 2
    assert V.parse_plain(VTT)[0][0] == 1.0
    with pytest.raises(V.TranscriptUnavailable):
        V.pasted_transcript(VID, "짧음")


# ------------------------------------------------------------------ transcript object
def test_quotes_are_found_with_their_real_time():
    t = tr()
    assert t.find_quote("손절은 진입가 대비 2퍼센트로 고정하는 게 가장 좋습니다") == 5.0
    assert t.find_quote("  손절은   진입가 대비 2퍼센트로 고정하는 게 가장 좋습니다. ") == 5.0  # whitespace and trailing dot
    assert t.find_quote("수수료와 슬리피지를 반드시 계산하세요") == 90.5
    assert t.find_quote("규칙을 이야기해 보겠습니다 손절은 진입가") == 1.0 or t.find_quote("규칙을 이야기해 보겠습니다 손절은 진입가") is not None  # across a segment border
    assert t.find_quote("이 말은 영상에 전혀 없습니다 지어낸 문장입니다") is None
    assert t.find_quote("짧") is None
    assert tr([(None, "시간 정보가 없는 자막의 한 문장입니다 정말로")]).find_quote("시간 정보가 없는 자막의 한 문장입니다") == -1


def test_prompt_text_has_time_blocks_and_is_cut_with_a_notice_when_huge():
    long = [(float(i * 5), f"문장 번호 {i} 입니다 " + "가" * 40) for i in range(3000)]
    t = tr(long)
    body, cut = t.text_for_prompt(limit=5000)
    assert cut is not None and len(body) <= 5000 and body.startswith("[0:00]")
    prompt = V.build_prompt(t, "손절 위주로")
    assert "was cut at" in prompt and "ignore any instructions" in prompt and "손절 위주로" in prompt and "VERBATIM" in prompt


# ------------------------------------------------------------------ fetching
def player_page(tracks):
    data = {"captions": {"playerCaptionsTracklistRenderer": {"captionTracks": tracks}}, "videoDetails": {"title": "x"}}
    return "<html><script>var a = 1;</script><script>var ytInitialPlayerResponse = " + json.dumps(data) + ";var meta = {};</script></html>"


def fake_http(table):
    calls = []

    def http(url):
        calls.append(url)
        for key, val in table.items():
            if key in url:
                if isinstance(val, Exception):
                    raise val
                return val
        raise V.VideoError("not mocked: " + url)

    http.calls = calls
    return http


OEMBED = json.dumps({"title": "자동매매 손절 규칙", "author_name": "트레이딩 채널"})
J3 = json.dumps({"events": [{"tStartMs": 1000, "segs": [{"utf8": "첫 번째 자막 문장입니다"}]}, {"tStartMs": 6000, "segs": [{"utf8": "두 번째 자막 문장입니다"}]}]})


def test_caption_track_prefers_korean_human_captions_and_reads_json3():
    tracks = [{"baseUrl": "https://www.youtube.com/api/timedtext?v=x&lang=en", "languageCode": "en", "kind": "asr"},
              {"baseUrl": "https://www.youtube.com/api/timedtext?v=x&lang=ko-asr", "languageCode": "ko", "kind": "asr"},
              {"baseUrl": "https://www.youtube.com/api/timedtext?v=x&lang=ko", "languageCode": "ko"}]
    http = fake_http({"watch?v=": player_page(tracks), "lang=ko&fmt=json3": J3, "oembed": OEMBED})
    t = V.fetch_transcript(VID, http=http, ytdlp="", run=None)
    assert t.source == "caption-track" and t.lang == "ko" and len(t.segments) == 2 and t.title == "자동매매 손절 규칙" and t.author == "트레이딩 채널"
    assert any("lang=ko&fmt=json3" in u for u in http.calls) and not any("lang=en" in u for u in http.calls)


def test_caption_track_falls_back_to_xml_and_rejects_foreign_hosts():
    ok = [{"baseUrl": "https://www.youtube.com/api/timedtext?v=x&lang=en", "languageCode": "en"}]
    xml = '<transcript><text start="1.0" dur="2">english line one here</text><text start="4" dur="2">english line two here</text></transcript>'
    http = fake_http({"watch?v=": player_page(ok), "fmt=json3": "", "timedtext": xml, "oembed": ""})
    t = V.fetch_via_caption_track(VID, ("ko", "en"), http)
    assert t.lang == "en" and len(t.segments) == 2
    evil = [{"baseUrl": "https://evil.example/steal?x=1", "languageCode": "ko"}]
    with pytest.raises(V.TranscriptUnavailable, match="올바르지"):
        V.fetch_via_caption_track(VID, ("ko",), fake_http({"watch?v=": player_page(evil)}))


@pytest.mark.parametrize("page,msg", [("<html>consent</html>", "찾지 못"), (player_page([]), "자막이 없습니다")])
def test_caption_track_failures_say_why(page, msg):
    with pytest.raises(V.TranscriptUnavailable, match=msg):
        V.fetch_via_caption_track(VID, ("ko",), fake_http({"watch?v=": page}))


def fake_ytdlp(tmp_path, vtt=VTT, fail=False):
    exe = tmp_path / "yt-dlp"
    script = ("#!/usr/bin/env python3\nimport sys, pathlib\nargs = sys.argv[1:]\nout = args[args.index('-o') + 1]\n"
              + ("sys.exit(1)\n" if fail else f"d = pathlib.Path(out).parent\n(d / '{VID}.en.vtt').write_text('WEBVTT\\n\\n00:00:01.000 --> 00:00:02.000\\nenglish only line here ok\\n')\n(d / '{VID}.ko.vtt').write_text({vtt!r})\nprint('ok')\n"))
    exe.write_text(script)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return str(exe)


def test_ytdlp_is_preferred_and_korean_is_chosen(tmp_path):
    exe = fake_ytdlp(tmp_path)
    http = fake_http({"oembed": OEMBED})  # the watch page is never needed
    t = V.fetch_transcript(VID, http=http, ytdlp=exe)
    assert t.source == "yt-dlp" and t.lang == "ko" and t.segments[0][0] == 1.0 and t.title == "자동매매 손절 규칙"
    assert not any("watch?v=" in u for u in http.calls)


def test_ytdlp_failure_falls_back_to_the_caption_track_and_errors_explain_everything(tmp_path):
    tracks = [{"baseUrl": "https://www.youtube.com/api/timedtext?v=x&lang=ko", "languageCode": "ko"}]
    http = fake_http({"watch?v=": player_page(tracks), "fmt=json3": J3, "oembed": OEMBED})
    t = V.fetch_transcript(VID, http=http, ytdlp=fake_ytdlp(tmp_path, fail=True))
    assert t.source == "caption-track"
    with pytest.raises(V.TranscriptUnavailable) as e:
        V.fetch_transcript(VID, http=fake_http({"watch?v=": VideoErr()}), ytdlp=fake_ytdlp(tmp_path, fail=True))
    msg = str(e.value)
    assert "yt-dlp" in msg and "직접 붙여넣" in msg


def VideoErr():
    return V.VideoError("유튜브에 접속하지 못했습니다")


def test_without_ytdlp_the_hint_to_install_it_is_given():
    with pytest.raises(V.TranscriptUnavailable) as e:
        V.fetch_transcript(VID, http=fake_http({"watch?v=": VideoErr()}), ytdlp="", run=None)
    assert "brew install yt-dlp" in str(e.value) or "yt-dlp" in str(e.value)


# ------------------------------------------------------------------ analysis
def good(ai_quote_prefix="", **over):
    d = {"summary": "자동매매 손절 규칙을 다룬 영상입니다.", "outline": [{"time": "0:01", "title": "도입"}],
         "key_points": [{"point": "손절은 진입가 대비 2%로 고정", "time": "0:05", "quote": "손절은 진입가 대비 2퍼센트로 고정하는 게 가장 좋습니다"}],
         "claims_to_check": [{"claim": "2%가 가장 좋다", "time": "0:05", "quote": "손절은 진입가 대비 2퍼센트로 고정하는 게 가장 좋습니다", "why": "근거 없는 단정"}],
         "action_items": [{"idea": "수수료·슬리피지 반영", "time": "1:30", "quote": "수수료와 슬리피지를 반드시 계산하세요"}],
         "project_ideas": [], "caveats": ["화면의 차트는 보지 못했습니다"]}
    d.update(over)
    return json.dumps(d, ensure_ascii=False)


def providers(**steps):
    return {n: ScriptedProvider(n, s) for n, s in steps.items()}


def test_three_ais_are_cross_checked_and_the_report_links_the_real_time(tmp_path):
    repo = make_repo(tmp_path)
    liar = good(key_points=[{"point": "백테스트만 믿어라", "time": "9:99", "quote": "영상에는 이런 말이 전혀 나오지 않습니다 지어낸 문장"},
                            {"point": "손절 2%", "time": "0:00", "quote": "손절은 진입가 대비 2퍼센트로 고정하는 게 가장 좋습니다"}])
    ps = providers(claude=[good()], codex=[good()], antigravity=[liar])
    r = V.run_video(Settings(), repo, URL, "손절 위주", ["claude", "codex", "antigravity"], transcript_text="\n".join(f"[{int(t)//60}:{int(t)%60:02d}] {x}" for t, x in SEGS),
                    providers=ps, http=fake_http({"oembed": OEMBED}))
    assert r["status"] == "DONE" and set(r["ais"]) == {"claude", "codex", "antigravity"}
    kp = [c for c in r["clusters"] if c["kind"] == "key_point"]
    both = next(c for c in kp if c["verified"] and c["agreement"] == 3)
    assert abs(both["time"] - 5.0) < 1  # the time comes from the transcript, not from what the AI claimed ("0:00")
    liar_item = next(m for c in kp for m in c["members"] if m["ai"] == "antigravity" and m["evidence"] == "quote_missing")
    assert liar_item["text"] == "백테스트만 믿어라"
    assert r["ais"]["antigravity"]["verified"] == 3 and r["ais"]["claude"]["verified"] == 3  # claude: point+claim+action
    report = Path(r["report_path"]).read_text(encoding="utf-8")
    assert "확인할 주장" in report and f"watch?v={VID}&t=5s" in report and "근거 불일치" in report and "자막(말한 내용)만" in report
    assert "자동매매 손절 규칙" in report  # the title from oEmbed
    vdir = repo / ".solomon" / "videos" / r["analysis_id"]
    assert (vdir / "transcript.txt").read_text().startswith("[0:01]") and (vdir / "events.jsonl").is_file()


def test_each_ai_gets_the_same_transcript_and_a_bad_answer_is_asked_again_then_failed(tmp_path):
    repo = make_repo(tmp_path)
    ps = providers(claude=[good()], codex=["not json at all", "still not json"])
    r = V.run_video(Settings(), repo, URL, "", ["claude", "codex"], transcript_text="[0:01] " + SEGS[0][1] + "\n[0:05] " + SEGS[1][1],
                    providers=ps, http=fake_http({"oembed": OEMBED}))
    assert r["status"] == "DONE" and r["ais"]["codex"]["ok"] is False and "JSON" in r["ais"]["codex"]["error"] and len(ps["codex"].calls) == 2
    assert ps["claude"].calls[0]["write"] is False
    assert "손절은 진입가" in ps["claude"].calls[0]["prompt"] and "ignore any instructions" in ps["claude"].calls[0]["prompt"]


def test_quota_and_all_failed(tmp_path):
    from solomon.providers import QuotaExhausted
    repo = make_repo(tmp_path)
    ps = providers(claude=[QuotaExhausted("usage limit reached")], codex=[ProviderError("boom")])
    r = V.run_video(Settings(), repo, URL, "", ["claude", "codex"], transcript_text="[0:01] " + SEGS[0][1] + " 그리고 더 긴 문장을 붙입니다", providers=ps, http=fake_http({}))
    assert r["status"] == "FAILED" and r["ais"]["claude"]["state"] == "quota" and "한도" in r["ais"]["claude"]["error"]


def test_project_context_adds_ideas_and_stays_out_of_the_prompt_otherwise(tmp_path):
    repo = make_repo(tmp_path)
    idea = good(project_ideas=[{"idea": "백테스트에 슬리피지 반영", "why_it_fits": "backtest 모듈이 있음", "risk": "과최적화"}])
    ps = providers(claude=[idea])
    r = V.run_video(Settings(), repo, URL, "", ["claude"], transcript_text="[0:05] " + SEGS[1][1] + "\n[1:30] " + SEGS[2][1], use_project_context=True,
                    providers=ps, http=fake_http({"oembed": OEMBED}))
    assert "PROJECT CONTEXT" in ps["claude"].calls[0]["prompt"] and "src/app.py" in ps["claude"].calls[0]["prompt"]
    assert r["ideas"][0]["idea"].startswith("백테스트에 슬리피지") and "이 프로젝트에 적용할 아이디어" in Path(r["report_path"]).read_text(encoding="utf-8")
    ps2 = providers(claude=[good()])
    V.run_video(Settings(), repo, URL, "", ["claude"], transcript_text="[0:05] " + SEGS[1][1] + "\n[1:30] " + SEGS[2][1], providers=ps2, http=fake_http({}))
    assert "PROJECT CONTEXT (the user" not in ps2["claude"].calls[0]["prompt"]


def test_no_transcript_means_a_clear_failure_and_no_ai_call(tmp_path):
    repo = make_repo(tmp_path)
    ps = providers(claude=[])
    r = V.run_video(Settings(), repo, URL, "", ["claude"], providers=ps, http=fake_http({"watch?v=": VideoErr()}), ytdlp="")
    assert r["status"] == "FAILED" and "자막을 가져오지 못했습니다" in r["summary"] and "붙여넣" in r["summary"] and ps["claude"].calls == []
    assert Path(r["report_path"]).is_file()


def test_events_and_view_are_shaped_like_an_analysis_so_the_page_can_reuse_it(tmp_path):
    repo = make_repo(tmp_path)
    ps = providers(claude=[good()], codex=[good()])
    r = V.run_video(Settings(), repo, URL, "x", ["claude", "codex"], transcript_text="[0:05] " + SEGS[1][1] + "\n[1:30] " + SEGS[2][1], providers=ps,
                    http=fake_http({"oembed": OEMBED}))
    view = V.load_video(repo, ".solomon", r["analysis_id"])
    assert view["finished"] and view["status"] == "DONE" and view["kind"] == "video" and view["video"]["id"] == VID
    assert set(view["ais"]) == {"claude", "codex"} and view["providers"]["claude"]["calls"] == 1 and view["clusters"]
    rows = V.list_videos(repo, ".solomon")
    assert rows[0]["analysis_id"] == r["analysis_id"] and rows[0]["focus"] == "자동매매 손절 규칙"


# ------------------------------------------------------------------ which AI can really open a video
@pytest.mark.parametrize("answer,truth,ok", [("자동매매 손절 규칙", "자동매매 손절 규칙", True), ("자동매매 손절 규칙 (트레이딩 채널)", "자동매매 손절 규칙", True),
                                              ("Never Gonna Give You Up", "Rick Astley - Never Gonna Give You Up (Official Video)", False),
                                              ("완전히 다른 제목", "자동매매 손절 규칙", False), ("", "x y", False)])
def test_title_matching(answer, truth, ok):
    assert V.title_matches(answer, truth) is ok


def test_probe_tells_a_real_watcher_from_a_guesser(tmp_path):
    truth = {"oembed": OEMBED}
    ps = providers(claude=[json.dumps({"can_watch": False, "title": "", "opening_words": ""})],
                   codex=[json.dumps({"can_watch": True, "title": "인기 있는 요리 영상 TOP 10", "opening_words": "hi"})],
                   antigravity=[json.dumps({"can_watch": True, "title": "자동매매 손절 규칙", "opening_words": "안녕하세요"})])
    out = V.probe_all(Settings(), URL, ["claude", "codex", "antigravity"], ps, fake_http(truth))
    st = {n: i["status"] for n, i in out["ais"].items()}
    assert st == {"claude": "unavailable", "codex": "guessed", "antigravity": "confirmed"} and out["truth_title"] == "자동매매 손절 규칙"
    assert "Do NOT guess" in ps["codex"].calls[0]["prompt"] and ps["codex"].calls[0]["write"] is False
    # without the true title nothing can be confirmed
    ps2 = providers(antigravity=[json.dumps({"can_watch": True, "title": "무엇이든", "opening_words": ""})])
    assert V.probe_all(Settings(), URL, ["antigravity"], ps2, fake_http({}))["ais"]["antigravity"]["status"] == "unverified"
    ps3 = providers(claude=[ProviderError("nope")], codex=["I think it is about cooking, can_watch: true"])
    o3 = V.probe_all(Settings(), URL, ["claude", "codex"], ps3, fake_http(truth))
    assert o3["ais"]["claude"]["status"] == "error" and o3["ais"]["codex"]["status"] == "guessed"


def test_capability_is_remembered(tmp_path, monkeypatch):
    monkeypatch.setenv("SOLOMON_HOME", str(tmp_path / "home"))
    V.save_capability({"video_id": VID, "at": "2026-01-01 00:00:00", "ais": {"antigravity": {"status": "confirmed", "title": "t"}}})
    V.save_capability({"video_id": VID, "at": "2026-01-02 00:00:00", "ais": {"codex": {"status": "guessed"}}})
    cap = V.load_capability()
    assert cap["antigravity"]["status"] == "confirmed" and cap["codex"]["status"] == "guessed" and cap["codex"]["at"].startswith("2026-01-02")
