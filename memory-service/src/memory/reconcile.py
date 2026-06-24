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


def main() -> None:
    import sys

    from memory.config import Settings
    from memory.db import apply_schema, connect
    from memory.embeddings.factory import build_embedder

    root = sys.argv[1] if len(sys.argv) > 1 else "."
    settings = Settings.from_env()
    conn = connect(settings)
    apply_schema(conn, settings.code_embed.dim, settings.doc_embed.dim)
    repo = Repository(conn)
    repo.ensure_embedding_config("code", settings.code_embed.provider, settings.code_embed.model, settings.code_embed.dim)
    repo.ensure_embedding_config("doc", settings.doc_embed.provider, settings.doc_embed.model, settings.doc_embed.dim)
    worker = Worker(repo, build_embedder(settings.code_embed), build_embedder(settings.doc_embed))
    print(reconcile(repo, worker, root))


if __name__ == "__main__":
    main()
