import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))


@pytest.fixture(autouse=True)
def _own_job_lock(tmp_path, monkeypatch):
    """Tests never touch the real ~/.solomon/job.lock (also for subprocesses they start)."""
    monkeypatch.setenv("SOLOMON_JOB_LOCK", str(tmp_path / "test-job.lock"))
