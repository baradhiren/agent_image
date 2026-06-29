from orchestrator.branch import branch_name, derive_tagline


def test_tagline_from_h1():
    assert derive_tagline("# Add CSV export\n\nbody", fallback="x") == "add-csv-export"


def test_tagline_strips_punct_and_caps():
    assert derive_tagline("# Fix: the *Bug*!! (urgent)", fallback="x") == "fix-the-bug-urgent"


def test_tagline_falls_back_when_no_h1():
    assert derive_tagline("no heading here", fallback="my-task-file") == "my-task-file"


def test_tagline_length_capped_at_40():
    long = "# " + "word " * 30
    assert len(derive_tagline(long, fallback="x")) <= 40
    assert not derive_tagline(long, fallback="x").endswith("-")


def test_branch_name_format():
    assert branch_name(42, "add-csv-export") == "feat/42-add-csv-export"
