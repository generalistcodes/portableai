#!/usr/bin/env python3
"""Bump APP_VERSION, prepend a git-cliff changelog section, commit, tag, push.

    python scripts/release.py v0.6.1
    make release VERSION=v0.6.1
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER_PY = ROOT / "ui" / "server.py"
CHANGELOG = ROOT / "CHANGELOG.md"
CLIFF_TOML = ROOT / "cliff.toml"
VERSION_RE = re.compile(r"^v(\d+\.\d+\.\d+)$")
APP_VERSION_RE = re.compile(r'^APP_VERSION = "[^"]+"', re.M)


class ReleaseError(SystemExit):
    pass


def parse_version(raw: str) -> str:
    """Return a tag like v0.6.1, or raise ReleaseError."""
    tag = (raw or "").strip()
    if not VERSION_RE.match(tag):
        raise ReleaseError(
            f"VERSION must look like v0.6.1 (got {raw!r})"
        )
    return tag


def version_number(tag: str) -> str:
    return tag[1:]


def set_app_version(path: Path, number: str) -> None:
    text = path.read_text(encoding="utf-8")
    updated, n = APP_VERSION_RE.subn(f'APP_VERSION = "{number}"', text, count=1)
    if n != 1:
        raise ReleaseError(f"could not find APP_VERSION assignment in {path}")
    path.write_text(updated, encoding="utf-8")


def git_cliff_bin() -> str:
    sibling = Path(sys.executable).parent / "git-cliff"
    if sibling.is_file():
        return str(sibling)
    return "git-cliff"


def git(*args: str, capture: bool = False) -> str:
    kw: dict = {"cwd": ROOT, "check": True}
    if capture:
        kw["text"] = True
        kw["stdout"] = subprocess.PIPE
    result = subprocess.run(["git", *args], **kw)
    return (result.stdout or "") if capture else ""


def require_clean_worktree() -> None:
    status = git("status", "--porcelain", capture=True)
    if status.strip():
        raise ReleaseError(
            "working tree is dirty; commit or stash before make release"
        )


def origin_actions_url() -> str:
    url = git("remote", "get-url", "origin", capture=True).strip()
    url = re.sub(r"^git@github\.com:", "https://github.com/", url)
    url = re.sub(r"^ssh://git@github\.com/", "https://github.com/", url)
    if url.endswith(".git"):
        url = url[:-4]
    return f"{url}/actions"


def generate_changelog(tag: str) -> None:
    if not CHANGELOG.is_file():
        raise ReleaseError(f"missing {CHANGELOG}")
    cmd = [
        git_cliff_bin(),
        "--unreleased",
        "--tag",
        tag,
        "--prepend",
        str(CHANGELOG),
        "--config",
        str(CLIFF_TOML),
    ]
    try:
        subprocess.run(cmd, cwd=ROOT, check=True)
    except FileNotFoundError as exc:
        raise ReleaseError(
            "git-cliff is not installed. Run: pip install -r requirements-dev.txt"
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise ReleaseError(f"git-cliff failed with exit {exc.returncode}") from exc


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="Release tag, e.g. v0.6.1")
    parser.add_argument(
        "--no-push",
        action="store_true",
        help="Commit and tag locally but do not push (for dry runs).",
    )
    args = parser.parse_args(argv)

    tag = parse_version(args.version)
    number = version_number(tag)
    require_clean_worktree()

    existing = git("tag", "-l", tag, capture=True).strip()
    if existing:
        raise ReleaseError(f"tag {tag} already exists")

    set_app_version(SERVER_PY, number)
    generate_changelog(tag)

    git("add", str(SERVER_PY.relative_to(ROOT)), str(CHANGELOG.relative_to(ROOT)))
    git("commit", "-m", f"chore(release): {tag}")
    git("tag", tag)
    if args.no_push:
        print(f"Created commit and tag {tag} (not pushed).")
        return
    git("push", "-u", "origin", "HEAD")
    git("push", "origin", tag)
    print(f"Pushed {tag}. Check GitHub Actions:")
    print(f"  {origin_actions_url()}")


if __name__ == "__main__":
    main()
