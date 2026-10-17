# Competition baseline

- Source baseline: `acceptance/claude-review-2026-10-03`
- Source commit: `2a2bd21040a8545667f76f138289379448faf3d9`
- Export date: 2026-10-17 (Asia/Shanghai)
- Competition track: Future of Work & Enterprise Productivity

This repository was created from an allowlisted file export. It has a new Git history and does
not contain the source repository's `.git` directory, local environment files, private runtime
data, databases, access logs, authentication caches, cloud credentials, private certificates, or
build output.

The original private repository and its working tree are not modified by competition work.

## Baseline verification commands

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
npm --prefix frontend ci
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

## Fresh local verification

Run on 2026-10-17 from this exported repository before the first commit:

- `pytest -q`: **109 passed** in 7.61 seconds.
- `ruff check .`: **passed**.
- Frontend install, typecheck and build: passed after the competition implementation; ten static
  routes were exported.

The baseline results above cover the imported baseline only. Current implementation results are
recorded in `IMPLEMENTATION_STATUS.md`; they do not verify cloud services, deployment, Firebase
authentication, Firestore persistence, or a live Vertex AI request.
