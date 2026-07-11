# cake-pricing

Custom cake **offer/pricing webapp** for a one-person pastry business. Replaces a
hand-maintained spreadsheet with a proper app: grouped offer form, component catalog with
temporal pricing, customer history, box/plate inventory.

> Design & requirements live in a **private** planning doc (not in this public repo).

## Stack

FastAPI · SQLAlchemy 2.0 · Alembic · Jinja + HTMX · Pico.css · PostgreSQL. Packaged with
**uv**, built as a multi-arch (amd64 + arm64) Docker image, deployed to a k3s HomeLab via
ArgoCD GitOps.

## Local development

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync                       # create .venv from uv.lock
uv run uvicorn app.main:app --reload
# http://localhost:8000  ·  /healthz  ·  /readyz
uv run pytest                 # tests
uv run ruff check .           # lint
```

If you change dependencies in `pyproject.toml`, regenerate the lockfile and commit it:

```bash
uv lock
```

## Docker

```bash
docker build -t cake-pricing:dev .
docker run --rm -p 8000:8000 cake-pricing:dev
```

## Releases (CI/CD)

Pushing a semver tag builds and pushes a multi-arch image to Docker Hub
(`asztalosgyula/cake-pricing`) and GHCR (`ghcr.io/gyulaasztalos/cake-pricing`):

```bash
git tag v0.1.0
git push origin v0.1.0
```

Renovate in the ArgoCD repo then bumps the deployed image tag → ArgoCD syncs to the cluster.

## Endpoints (skeleton)

| Path       | Purpose                                  |
|------------|------------------------------------------|
| `/`        | Placeholder landing page (Hungarian)     |
| `/healthz` | Liveness/readiness probe (no deps)       |
| `/readyz`  | Readiness incl. dependencies (DB later)  |
