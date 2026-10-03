# Working on kviss

- **Run the checks CI runs before pushing:** `ruff check .` and
  `python -m unittest discover -s tests -t .`. A `Dockerfile` change should
  also build and start (`docker compose up -d --build`).
- **Commit subjects and PR titles are Conventional Commits.** PRs are
  squash-merged, so the PR title becomes the commit subject on `main`, and
  that subject alone decides whether CI cuts a release
  (`scripts/next-version.sh`, see "CI and releases" in `README.md`):

  | Subject prefix | Release it cuts |
  |---|---|
  | `feat: ...` / `feat(admin): ...` | minor |
  | `fix: ...`, `perf: ...` | patch |
  | `feat!: ...`, or a `BREAKING CHANGE:` footer | major (minor while on `0.x`) |
  | `docs:`, `ci:`, `chore:`, `build:`, `refactor:`, `test:` | none |

  A plain title such as "Add Daily Doubles" cuts **no release**, even if the
  branch's own commits are conventional: the squash moves them into the body,
  which the script ignores. If a PR was created with a title you didn't pick,
  rename it to a conventional subject before it is merged.
- **Don't merge.** Push the branch, make CI green, and leave merging to the
  owner unless asked.
