from pathlib import Path

from memory.repository import Repository
from memory.worker import Worker

_SUPPORTED = (".py", ".md")


def scan_paths(root: str) -> list[str]:
    root_path = Path(root)
    return sorted(
        str(p.relative_to(root_path))
        for p in root_path.rglob("*")
        if p.is_file() and p.suffix in _SUPPORTED
    )


def reconcile(repo: Repository, worker: Worker, root: str) -> dict:
    on_disk = scan_paths(root)
    for rel_path in on_disk:
        worker.process_file(root, rel_path)
    on_disk_set = set(on_disk)
    removed = [p for p in repo.list_db_files() if p not in on_disk_set]
    for rel_path in removed:
        repo.delete_file(rel_path)
    repo.reresolve_all_edges()
    pruned = repo.prune_spec_links()
    return {"processed": len(on_disk), "removed": removed, "pruned_links": pruned}
