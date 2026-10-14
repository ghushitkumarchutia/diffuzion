# Better Auth schema ownership

Better Auth owns only the `auth` schema. Alembic owns application tables and its version table in `app`. Bootstrap both schemas and pgvector before either migration tool. Never run Alembic against Better Auth tables or use the auth CLI to modify application tables.

The approved additional history location is `versions/`. Every SQL file here must come from the local CLI pinned in `package-lock.json`; never hand-author its generated statements. `0001_auth.sql` was generated with Better Auth/auth CLI 1.7.7 and reviewed before application. The generated `auth.user.id` and its referencing auth columns are PostgreSQL `text`; app foreign keys use that verified type.

## Generate, review, apply

Inject `PUBLIC_ORIGIN`, `AUTH_DATABASE_URL`, `BETTER_AUTH_SECRET` and both providers' client IDs/secrets into the environment. The PostgreSQL connection must explicitly select `auth` with `options=-csearch_path%3Dauth`. The configured pool also sets `search_path=auth`. Use Node.js 24.21.0 and the local locked npm installation. `psql` is required for the read-only schema assertions.

From `apps/web`:

```sh
sh auth-migrations/migrate.sh generate 0001_auth
sh auth-migrations/migrate.sh apply 0001_auth
sh auth-migrations/migrate.sh check 0001_auth
```

Generation refuses to overwrite an existing history file. Review and commit each new generated version before applying it to the deployment database. `apply` regenerates pending SQL into a temporary directory and requires it to match the reviewed history file before invoking the pinned CLI's migrate command. It then runs the CLI schema check and `verify-schema.sql`. Any command failure stops the runner. If the schema is already current, use `check`; `apply` intentionally refuses to claim an empty migration was applied.

Only the application operator runs this script. Run auth migrations before dependent application migrations. The runner passes the React server condition so explicit relative `.ts` imports and the server-only environment module work in Node-run tooling. It does not introduce a second auth configuration, Admin plugin, password login or provider-account merging.

Better Auth's initial missing-table diagnostic during generation is expected on an empty auth schema. Successful CLI completion still must be followed by the live schema checks. Generated files are retained unchanged, including the CLI's exact formatting; the schema verifier does not alter them.
