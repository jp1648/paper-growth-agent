"""Exercise the real native SDK lifecycle, not a mocked Worker context manager."""

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("runtime_count", [1, 3])
def test_native_callbacks_finish_before_interpreter_finalization(runtime_count):
    helper = Path(__file__).parent / "helpers"
    script = """
import atexit
import sys
import threading
release = threading.Event()
completed = []
atexit.register(lambda: print('callbacks_finished', len(completed), flush=True))
sys.path.insert(0, sys.argv[1])
from temporal_shutdown_callbacks import pending_native_callback
atexit.register(release.set)
runtimes = [pending_native_callback(release, completed) for _ in range(int(sys.argv[2]))]
"""
    result = subprocess.run(
        [sys.executable, "-X", "faulthandler", "-c", script, str(helper), str(runtime_count)],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"callbacks_finished {runtime_count}"
    assert result.stderr == ""
