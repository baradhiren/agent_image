import os
import stat

from memory.startup import snapshot_home


def test_home_is_colocated_when_writable(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    fallback = tmp_path / "fallback"

    home, location = snapshot_home(str(project), fallback_dir=str(fallback))

    assert location == "co-located"
    assert home == str(project / ".agent-memory")
    assert os.path.isdir(home)


def test_home_falls_back_when_unwritable(tmp_path):
    project = tmp_path / "ro-project"
    project.mkdir()
    os.chmod(project, stat.S_IRUSR | stat.S_IXUSR)  # read-only: cannot mkdir inside
    fallback = tmp_path / "fallback"

    try:
        home, location = snapshot_home(str(project), fallback_dir=str(fallback))
    finally:
        os.chmod(project, stat.S_IRWXU)  # restore so tmp cleanup works

    assert location == "fallback-volume"
    assert home == str(fallback)
    assert os.path.isdir(home)
