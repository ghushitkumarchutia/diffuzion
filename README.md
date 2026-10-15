# Diffuzion

Diffuzion is an API migration impact workbench combining deterministic OpenAPI comparison, narrow TypeScript/JavaScript/Python call extraction and evidence-linked AI guidance. The repository implements Day 1 foundations, Day 2 authentication/database infrastructure and Day 3 parsing, comparison, mapping, storage helpers and fixtures.

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

This runs ESLint, Ruff lint/format checks, strict TypeScript and mypy checks, backend tests, Chromium browser smoke tests and the standalone Next.js production build. Backend commands explicitly set the API import path. Install the pinned oasdiff engine before running the Day 3 tests. The Day 1 helper tests need no database. Day 2 integration tests require the isolated test database and explicit runtime values described below. Without `TEST_DATABASE_URL`, PostgreSQL integration tests are visibly skipped.

Run the minimal frontend locally:

```sh
npm run dev --prefix apps/web
```

The root page identifies the project and states that analysis is not yet available. The Better Auth handler is mounted at `/api/auth/*`. Sign-in UI, protected domain routes, worker execution and AI/payment integrations belong to subsequent stages.

## Configuration

`.env.example` lists configuration names and placeholders only. Replace OAuth placeholders with registered Google/GitHub app credentials for real login. The configured auth callbacks are `/api/auth/callback/google` and `/api/auth/callback/github` on `PUBLIC_ORIGIN`. Supply values explicitly through the runtime environment; the Python configuration does not implicitly read `.env`. Local Next.js environment files belong in `apps/web` and are ignored by Git. Keep all secrets out of `NEXT_PUBLIC_*`.

Free defaults remain 5 reports per UTC month and 50 MiB stored source. Pro defaults remain INR 199, 50 reports over 30 days and 250 MiB stored source. The demo AI budget is USD 10 per UTC month. These are configuration defaults; These foundation stages do not charge or activate entitlements.

## Contract generation

The chosen output is ignored by Git and retained in Docker build contexts. The npm command accepts an OpenAPI document supplied as its input:

```sh
cd apps/web
npm run generate:contracts -- /absolute/path/to/openapi.json
```

The actual domain API document will be generated once the approved API routes exist. Day 1 DTOs reject extra writable fields, bound input scope, use timezone-aware instants, keep deterministic findings separate from guidance and distinguish original recommendations from user edits. Evidence membership and ownership are responsibilities of the future services, not schema validation alone.

## Current validation limits

Validation covers macOS ARM64 and Linux ARM64 Docker subprocesses. PostgreSQL/pgvector migrations and genuine signed Better Auth sessions have been verified. OAuth initiation and sign-out are tested using synthetic OAuth credentials. A complete real Google/GitHub authorization round trip still requires registered credentials and manual validation. The pinned oasdiff adapter is verified. Gemini and Razorpay integrations remain deferred. No uploaded code is executed. The assistant has not staged files or executed commits.

## Day 2 database and authentication setup

For the Docker deployment, bootstrap a private PostgreSQL 17 service with pgvector, without publishing its database port. Docker remains the deployment target. Local Day 2 validation used an isolated temporary PostgreSQL 17 instance with pgvector 0.8.2 and a private Unix socket, as approved for this environment.

Use an operator database connection with schema/extension creation privileges as `BOOTSTRAP_DATABASE_URL`. Supply existing auth/app connection roles with access to their respective schemas. If those roles differ, the app migration role also needs USAGE on `auth` and REFERENCES on `auth.user`; the operator CLI needs SELECT on `auth.user.id`. Runtime URLs are separate from the bootstrap connection. Role provisioning is an operator prerequisite, not a public application feature.

Apply schema changes in order, with credentials injected explicitly:

```sh
psql "$BOOTSTRAP_DATABASE_URL" --no-psqlrc --set=ON_ERROR_STOP=1 --file=infra/postgres/bootstrap.sql
cd apps/web && sh auth-migrations/migrate.sh apply 0001_auth
```

Run application migrations from the repository root:

```sh
PYTHONPATH=apps/api uv run --project apps/api --locked alembic -c apps/api/alembic.ini upgrade head
PYTHONPATH=apps/api uv run --project apps/api --locked alembic -c apps/api/alembic.ini check
```

The initial application revision is `0001_initial_app` in the approved `initial_app_schema.py` file. Online migrations assert the auth tables, actual text user key, app schema and vector extension before changing application objects. Offline SQL generation cannot inspect a database; run the read-only auth verification before applying offline SQL. Never use ORM `create_all` as a migration substitute.

[Auth migration ownership and SQL review procedure](apps/web/auth-migrations/README.md) explains the pinned CLI workflow and approved generated history directory.

After an existing user has accessed a protected application resource through a verified session, the operator can assign the initial Admin:

```sh
PYTHONPATH=apps/api uv run --project apps/api --locked python -m app.cli promote-admin --user-id VERIFIED_USER_ID --reason "Initial project operator assignment"
```

The CLI requires an existing active application account, locks it, records the role change and audit reason, and does not expose an HTTP endpoint. Repeating an already completed assignment does not duplicate the audit event.

## Day 2 integration tests

Provision a dedicated database and role both named `diffuzion_test`. Bootstrap and migrate that database first. Set `TEST_DATABASE_URL` equal to the psycopg `APP_DATABASE_URL`, `DIFFUZION_TEST_MODE=1`, a loopback `PUBLIC_ORIGIN` on port 3100, and synthetic OAuth credentials/auth secret prefixed with `diffuzion-test-`. Test connection passwords must be `test` or `diffuzion-test-password`. Runtime Gemini/Razorpay settings can be explicit test-only values; no provider calls are made. The test helpers accept only loopback connections or the approved `/tmp/diffuzion-` Unix socket paths and confirm the actual connected database identity.

Run `make check` with the selected Node/uv runtimes. The backend tests start a local Next.js handler, create real signed sessions using Better Auth's separate test-only instance, verify revocation/current account status, and remove their users/sessions afterward. The migration cycle test refuses to downgrade a database containing application rows. It preserves auth records and the vector extension.

The guarded helper is available only in test tooling, never mounted as a route:

```sh
NODE_ENV=test node --conditions=react-server apps/web/tests/support/test-sessions.ts create user
```

Its output contains a test session cookie. Capture that output privately rather than publishing it. Use the helper's `revoke SESSION_ID` and `delete USER_ID` commands to clean manually created fixtures. The normal production auth configuration has no test or Admin plugin.

## Day 3 parsing and fixture checks

Install oasdiff 1.33.0 with the checked-in installer. It verifies the architecture-specific official release SHA-256 before installation. Choose a writable absolute destination locally; the shared Linux image uses the default `/usr/local/bin/oasdiff`.

```sh
sh infra/docker/install-oasdiff.sh /absolute/private-tools/oasdiff
export OASDIFF_PATH=/absolute/private-tools/oasdiff
PYTHONPATH=apps/api uv run --project apps/api --locked pytest apps/api/tests/unit
```

Day 3 helpers parse safe single-document JSON/YAML OpenAPI 3.0.x, index at most 100 operations per 2 MiB specification and extract direct fetch/unaliased requests calls from the approved three languages. Source files are limited to 256 KiB. Parser tasks run in a bounded isolated child; uploaded modules are never imported or executed, and networking is denied. Dynamic old-spec servers require a confirmed literal base URL override. The first root server is used when multiple servers are declared, with a warning.

URL matching uses the old contract, declared origin/base path, static precedence and unique parameter matches. Leading-slash paths are accepted; query/fragment are excluded from matching. Ambiguity, wrappers, aliases, shadowing, syntax errors and dynamic expressions remain visible. Manual overrides require a detected call, an existing candidate operation and files belonging to the selected revision. There is no rename inference, import resolution or value propagation.

The comparison adapter retains raw JSON and engine version. Its locations are verified enclosing operation/document pointers, explicitly marked as coarse; oasdiff does not emit exact JSON Pointers. An empty result says “No detected consumer-facing contract changes” and does not assert identical files.

Local storage creates private files under server-generated opaque keys and rejects overwrites and symlink reads. S3 helpers use explicit server credentials, private objects and conditional writes. Both stream with caller-supplied byte limits. Calling services remain responsible for ownership and quotas. S3 request/error contracts are tested with Botocore Stubber; a live private Lightsail bucket has not been exercised.

`samples/migration-demo/manifest.json` describes six uploadable sample inputs. It records the intended later import/preview/guidance/review/export workflow; the import service and analysis routes are not implemented by Day 3. The sample includes required-field, enum, response-property and endpoint changes plus one dynamic call per language.

`evals/cases/development.jsonl` contains exactly twenty development families, each with embedded specs, all three source variants and notes. Gold source coordinates/mappings, relevant note passages, unknowns and acceptable actions are stored separately in `evals/labels/development.jsonl`. Provenance pins the Petstore and Box excerpts to verified commits and preserves their actual licenses and internal references. Synthetic modifications are labelled. Five representative families are marked for a future AI spike; no AI calls or held-out cases are included here.

Day 3 database regression checks use disposable Docker PostgreSQL 17/pgvector 0.8.2, with a test-only port bound to loopback. Deployment databases must remain private without published ports. Follow the existing guarded test database setup above; the Unix-socket-only listener assertion still applies when a Unix socket is selected.
