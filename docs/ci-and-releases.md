# CI and releases

`.github/workflows/ci.yml` runs on every pull request and push to `main`:

- **Lint:** `ruff check` (settings in `ruff.toml`), hadolint on the `Dockerfile`, shellcheck on `scripts/`.
- **Test:** the unit tests on Python 3.11 (Debian 12, the systemd deploy) and 3.12 (the Docker image).
- **Docker build:** builds the image and starts it with the same hardening as `compose.yaml` (read-only root
  filesystem, no capabilities), then checks that it gets healthy, asks for the password and serves the start page.

**Releases are cut automatically** from `main`. After a push to `main` passes every job, the Tag release job runs
`scripts/next-version.sh`, which reads the [Conventional Commits](https://www.conventionalcommits.org) subjects of
the commits since the latest `vX.Y.Z` tag and takes the largest bump:

| Commit subject | Bump |
|---|---|
| `feat: ...` / `feat(admin): ...` | minor |
| `fix: ...`, `perf: ...` | patch |
| `feat!: ...`, or a `BREAKING CHANGE:` footer in the body | major (minor while the version is `0.x`) |
| anything else (`docs:`, `ci:`, `chore:`, `refactor:`, `test:`, a plain subject) | no release |

If there is a bump, it tags the commit and runs the pipeline again on the tag. That run's Release job pushes the
image to `ghcr.io/sonhal/kviss` (tags `X.Y.Z`, `X.Y`, `latest`, and `X` from 1.0 on) with a signed build provenance
attestation, and creates a GitHub Release with generated notes. Pull requests are squash-merged, so **the PR title
is the commit subject** that decides the release.

- `scripts/next-version.sh` prints what the current branch would release (the reasoning goes to stderr).
- To release by hand, push a tag: `git tag -a v0.3.0 -m v0.3.0 && git push origin v0.3.0`. A tag with a hyphen
  (`v0.3.0-rc.1`) becomes a pre-release, which doesn't move `latest`. Leaving `0.x` is deliberate: push `v1.0.0`
  by hand.
- The systemd deploy can follow releases too: `git checkout v0.3.0` instead of `git pull`.
- Dependabot opens weekly PRs for the Python packages, the base image and the GitHub Actions. Their `build(deps):`
  titles don't cut a release on their own.
