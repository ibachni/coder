from pathlib import Path

from tools.get_ticket import Repo

REPO_PATHS: dict[Repo, Path] = {
    Repo.CODER: Path.home() / "Code" / "coder",
    Repo.RESEARCH: Path.home() / "Code" / "research",
}


def resolve_repo(repo: Repo) -> Path:
    if repo not in REPO_PATHS:
        raise ValueError(f"Repo {repo!r} is not in the allowlist")
    path = REPO_PATHS[repo].resolve()
    if not path.is_dir():
        raise FileNotFoundError(f"Repo path missing or not a directory: {path}")
    if not (path / ".git").exists():
        raise ValueError(f"Repo path is not a git working tree: {path}")
    return path
