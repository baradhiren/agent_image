import subprocess
import sys
from typing import Callable

from orchestrator.loop import TaskReport


def orchestrate(*, run_task_fn: Callable[[], TaskReport],
                up: Callable[[], None], down: Callable[[], None],
                dump: Callable[[], None]) -> TaskReport:
    up()
    try:
        return run_task_fn()
    finally:
        try:
            dump()
        except subprocess.CalledProcessError:
            print(
                "WARNING: snapshot dump failed; leaving the stack UP so memory is "
                "not lost. Inspect the db, then tear down manually once dumped.",
                file=sys.stderr,
            )
            raise
        else:
            down()
