import subprocess

from orchestrator import seams


def _fake_run(record, returncode=0):
    def run(cmd, **kwargs):
        record.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, returncode)
    return run


def test_exec_runner_builds_compose_exec(monkeypatch):
    rec = []
    monkeypatch.setattr(seams.subprocess, "run", _fake_run(rec, returncode=0))
    res = seams.exec_runner("developer", 7, 1, compose_dir="/img", project_dir="/proj")
    assert res.ok is True and res.exit_code == 0
    cmd, kwargs = rec[0]
    assert cmd[:4] == ["docker", "compose", "exec", "-T"]
    assert "developer" in cmd
    assert "--task-id" in cmd and "7" in cmd
    assert "--round" in cmd and "1" in cmd
    assert kwargs["cwd"] == "/img"
    assert kwargs["env"]["PROJECT_DIR"] == "/proj"


def test_exec_runner_nonzero_is_not_ok(monkeypatch):
    rec = []
    monkeypatch.setattr(seams.subprocess, "run", _fake_run(rec, returncode=2))
    res = seams.exec_runner("reviewer", 7, 2, compose_dir="/img", project_dir="/proj")
    assert res.ok is False and res.exit_code == 2


def test_compose_up_uses_wait(monkeypatch):
    rec = []
    monkeypatch.setattr(seams.subprocess, "run", _fake_run(rec))
    seams.compose_up(compose_dir="/img", project_dir="/proj")
    cmd, kwargs = rec[0]
    assert cmd[:3] == ["docker", "compose", "up"]
    assert "-d" in cmd and "--wait" in cmd
    assert kwargs["env"]["PROJECT_DIR"] == "/proj"


def test_snapshot_dump_raises_on_failure(monkeypatch):
    def run(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd)
    monkeypatch.setattr(seams.subprocess, "run", run)
    try:
        seams.snapshot_dump(compose_dir="/img", project_dir="/proj")
        raised = False
    except subprocess.CalledProcessError:
        raised = True
    assert raised is True
