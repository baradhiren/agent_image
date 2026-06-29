# orchestrator/tests/test_cli.py
import pytest

from orchestrator import cli
from orchestrator.loop import TaskReport


def test_main_parses_and_invokes(monkeypatch, tmp_path, capsys):
    task = tmp_path / "t.md"; task.write_text("# Do it\n")
    captured = {}

    def fake_run(*, project_dir, role, task_file, compose_dir):
        captured.update(project_dir=project_dir, role=role,
                        task_file=task_file, compose_dir=compose_dir)
        return TaskReport(3, "feat/3-do-it", "approved", "approved", None, 1, "done", [])

    monkeypatch.setattr(cli, "run_one_task", fake_run)
    rc = cli.main(["run", "--role", "developer", str(task)])
    assert rc == 0
    assert captured["role"] == "developer"
    assert captured["task_file"] == str(task)
    out = capsys.readouterr().out
    assert "feat/3-do-it" in out and "approved" in out


def test_main_rejects_bad_role(tmp_path):
    task = tmp_path / "t.md"; task.write_text("# x\n")
    with pytest.raises(SystemExit):  # argparse rejects an invalid --role choice
        cli.main(["run", "--role", "wizard", str(task)])
