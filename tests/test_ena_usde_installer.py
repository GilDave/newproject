import plistlib
import subprocess
import pathlib
import tempfile
import unittest

SH = pathlib.Path(__file__).resolve().parents[1] / "patches/ena-bot/files/install_usde_forward.sh"


class T(unittest.TestCase):
    def test_render_only_makes_a_valid_plist_with_a_daily_schedule(self):
        with tempfile.TemporaryDirectory() as d:
            work = pathlib.Path(d) / "ena bot & test"      # 공백·& 가 있는 경로도 안전해야 한다
            work.mkdir()
            out = pathlib.Path(d) / "agents"
            r = subprocess.run(["bash", str(SH), "--render-only", str(out)], cwd=work, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            pl = plistlib.loads((out / "com.ena.usdeforward.plist").read_bytes())
        self.assertEqual(pl["Label"], "com.ena.usdeforward")
        self.assertEqual(pl["ProgramArguments"][0], str(work) + "/.venv/bin/python")
        self.assertEqual(pl["ProgramArguments"][-2:], ["tools/research/ena_usde_forward.py", "--daily"])
        self.assertEqual(pl["WorkingDirectory"], str(work))
        self.assertEqual(pl["StartCalendarInterval"], {"Hour": 9, "Minute": 23})
        self.assertIs(pl["RunAtLoad"], False)                   # 설치 스크립트가 직접 1회 실행하므로 이중 실행을 피한다
        self.assertTrue(pl["StandardOutPath"].endswith("/data/com.ena.usdeforward.out.log"))

    def test_requires_a_commit_and_the_ena_folder(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertNotEqual(subprocess.run(["bash", str(SH)], cwd=d, capture_output=True, text=True).returncode, 0)
            r = subprocess.run(["bash", str(SH), "abc123"], cwd=d, capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("ENA 폴더", r.stdout)


if __name__ == "__main__":
    unittest.main()
