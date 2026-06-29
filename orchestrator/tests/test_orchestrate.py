import subprocess

import pytest

from orchestrator.orchestrate import orchestrate
from orchestrator.loop import TaskReport


def _report():
    return TaskReport(1, "feat/1-x", "approved", "approved", None, 1, "s", [])


def test_dump_before_down_on_success():
    order = []
    orchestrate(run_task_fn=lambda: _report(),
                up=lambda: order.append("up"),
                down=lambda: order.append("down"),
                dump=lambda: order.append("dump"))
    assert order == ["up", "dump", "down"]


def test_dump_failure_skips_down(capsys):
    order = []
    def dump(): order.append("dump"); raise subprocess.CalledProcessError(1, "dump")
    with pytest.raises(subprocess.CalledProcessError):
        orchestrate(run_task_fn=lambda: _report(),
                    up=lambda: order.append("up"),
                    down=lambda: order.append("down"), dump=dump)
    assert "down" not in order
    assert "stack" in capsys.readouterr().err.lower()


def test_run_task_raises_still_dumps():
    order = []
    def boom(): order.append("run"); raise RuntimeError("kaboom")
    with pytest.raises(RuntimeError):
        orchestrate(run_task_fn=boom,
                    up=lambda: order.append("up"),
                    down=lambda: order.append("down"),
                    dump=lambda: order.append("dump"))
    assert order == ["up", "run", "dump", "down"]
