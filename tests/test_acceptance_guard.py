import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest

spec = importlib.util.spec_from_file_location(
    "guard", Path(__file__).parents[1] / "deploy/acceptance-guard.py"
)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def test_guard_restores_pause_after_child_dies_before_restart(tmp_path, monkeypatch):
    pause, backup = tmp_path / "pause", tmp_path / "backup"
    pause.write_bytes(b"original pause\n")
    pause.chmod(0o600)
    guard.copy_pause(pause, backup)
    pause.unlink()  # A killed child never ran its Python finally.
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if "systemctl" in command[0]:
            assert pause.read_bytes() == b"original pause\n"
            assert pause.stat().st_mode & 0o777 == 0o600
        return Mock(stdout="")

    monkeypatch.setattr(guard.subprocess, "run", run)
    guard.cleanup(pause, backup)
    assert calls[-1] == ["/usr/bin/systemctl", "enable", "--now", "always-on.service"]


def test_guard_restore_failure_never_restarts_listener(tmp_path, monkeypatch):
    run = Mock(return_value=Mock(stdout=""))
    monkeypatch.setattr(guard.subprocess, "run", run)
    with pytest.raises(FileNotFoundError):
        guard.cleanup(tmp_path / "pause", tmp_path / "missing-backup")
    assert run.call_count == 1


def test_guard_stops_surviving_child_before_restoring_pause(tmp_path, monkeypatch):
    pause, backup = tmp_path / "pause", tmp_path / "backup"
    backup.write_bytes(b"paused")
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if "stop" in command:
            assert not pause.exists()
        if "systemctl" in command[0]:
            assert pause.read_bytes() == b"paused"
        return Mock(stdout="container-id")

    monkeypatch.setattr(guard.subprocess, "run", run)
    guard.cleanup(pause, backup)
    assert calls[1] == ["/usr/bin/docker", "stop", "--time", "30", "trader-acceptance"]


def test_guard_failed_child_stop_never_restarts_listener(tmp_path, monkeypatch):
    run = Mock(side_effect=[Mock(stdout="container-id"), guard.subprocess.CalledProcessError(1, "docker")])
    monkeypatch.setattr(guard.subprocess, "run", run)
    with pytest.raises(guard.subprocess.CalledProcessError):
        guard.cleanup(tmp_path / "pause", tmp_path / "backup")
    assert run.call_count == 2
