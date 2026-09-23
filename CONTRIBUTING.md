# Contributing to PortableAI

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/)
so `make release` can group the changelog automatically:

| Prefix | When to use | Changelog group |
| --- | --- | --- |
| `feat:` | User-visible feature | Features |
| `fix:` | Bug fix | Fixes |
| `chore:` | Tooling, deps, packaging, release | Chores |
| `docs:` | Documentation only | Documentation |

Scopes are optional (`feat(ui): …`). A blank line then a longer body is
fine; git-cliff uses the first line.

Examples:

```
feat: show first-run onboarding when no models are installed
fix: skip the terminal prompt when stdin is not a TTY
docs: document Conventional Commits
chore: add git-cliff to requirements-dev.txt
```

## Releases

Do not hand-edit `CHANGELOG.md` on a feature PR. At release time:

```
make release VERSION=vX.Y.Z
```

That command sets `APP_VERSION` in `ui/server.py`, prepends a git-cliff
section to `CHANGELOG.md` from commits since the previous tag, commits
`chore(release): vX.Y.Z`, and pushes the tag.

The pull-request workflow **Version bump (advisory)** warns when a
release-worthy change does not touch `APP_VERSION`. It is not a required
status check and does not block merge.
