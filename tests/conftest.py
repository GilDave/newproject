import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))


@pytest.fixture(autouse=True)
def _isolated_machine_state(tmp_path, tmp_path_factory, monkeypatch):
    """Tests must not depend on the machine they run on: never the real ~/.solomon (quota marks, picture tests, the Solomon-only Ollama
    model, the job lock) - when Solomon improves Solomon, that is the live state of the very machine the tests run on."""
    monkeypatch.setenv("HOME", str(tmp_path_factory.mktemp("home")))
    monkeypatch.setenv("SOLOMON_JOB_LOCK", str(tmp_path / "test-job.lock"))
