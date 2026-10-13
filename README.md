# Diffuzion

Diffuzion is an API migration impact workbench combining deterministic OpenAPI comparison, narrow TypeScript/JavaScript/Python call extraction and evidence-linked AI guidance. The repository currently implements Day 1 foundations only.

The approved requirements are in [the unchanged development blueprint](docs/Diffuzion_Development_Blueprint.md). [Foundation decisions](docs/architecture.md) record the tested dependency set, conservative safety defaults and verification boundaries.

## Local foundation setup

Use Node.js 24.21.0 from `.nvmrc`, Python 3.12.13 from `.python-version`, and uv 0.13.0 or a compatible uv release. Runtime dependencies and generated lockfiles come from the official create-next-app and uv tools.

```sh
nvm install
nvm use
npm ci --prefix apps/web
uv sync --project apps/api --locked
cd apps/web && npx playwright install chromium
```

Return to the repository root to run checks:

```sh
make check
```

This runs ESLint, Ruff lint/format checks, strict TypeScript and mypy checks, backend tests, Chromium browser smoke tests and the standalone Next.js production build. Backend commands explicitly set the API import path. Day 1 tests require no database or provider credentials.

Run the minimal frontend locally:

```sh
npm run dev --prefix apps/web
```

The root page identifies the project and states that analysis is not yet available. Authentication, domain routes, migrations, worker execution and provider integrations belong to subsequent stages.

## Configuration

`.env.example` lists configuration names and placeholders only. Replace provider placeholders with real server credentials when implementing later runtime integrations. Supply values explicitly through the runtime environment; the Python configuration does not implicitly read `.env`. Local Next.js environment files belong in `apps/web` and are ignored by Git. Keep all secrets out of `NEXT_PUBLIC_*`.

Free defaults remain 5 reports per UTC month and 50 MiB stored source. Pro defaults remain INR 199, 50 reports over 30 days and 250 MiB stored source. The demo AI budget is USD 10 per UTC month. These are configuration defaults; Day 1 does not charge or activate entitlements.

## Contract generation

The chosen output is ignored by Git and retained in Docker build contexts. The npm command accepts an OpenAPI document supplied as its input:

```sh
cd apps/web
npm run generate:contracts -- /absolute/path/to/openapi.json
```

The actual domain API document will be generated once the approved API routes exist. Day 1 DTOs reject extra writable fields, bound input scope, use timezone-aware instants, keep deterministic findings separate from guidance and distinguish original recommendations from user edits. Evidence membership and ownership are responsibilities of the future services, not schema validation alone.

## Current validation limits

Local validation uses macOS ARM64. Linux subprocess memory enforcement has not been exercised on a Linux host. Database, OAuth, oasdiff, Gemini and Razorpay integration have not been claimed as verified at this foundation stage. No uploaded code is executed, and no Git commits have been made.
