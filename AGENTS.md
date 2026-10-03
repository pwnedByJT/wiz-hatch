# Repository Agent Instructions

These rules apply to every coding agent and contributor, including Codex, Wibey,
Code Puppy, GitHub Copilot, and similar automation.

## Rule 1: Never Commit to `main`

Never modify files or commit directly on `main`. Before modifying any file, run:

```bash
git fetch origin
git checkout main
git pull --ff-only origin main
git checkout -b <type>/<short-description>
```

Use a descriptive branch with one of these prefixes: `feat/`, `fix/`, `chore/`,
`sec/`, or `docs/`.

## Rule 2: Pull Request Workflow

Commit and push all changes only to the feature branch. Open a GitHub Pull
Request targeting `main`. Merge only through the Pull Request after CI passes,
including `ruff`, `bandit`, and `pytest`. Never bypass the Pull Request or its
required checks.

## Rule 3: Strict Semantic Versioning

Follow Semantic Versioning and change versions only when the Pull Request is
merged into `main`. Advance versions sequentially: `0.0.1` to `0.0.2` for a
patch, then to `0.1.0` for the first feature release. Create release tags only
from the resulting commit on `main` after merge.
