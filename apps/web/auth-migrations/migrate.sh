set -eu

web_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$web_dir"
export NODE_OPTIONS="${NODE_OPTIONS:-} --conditions=react-server"
cli="./node_modules/.bin/auth"
command=${1:-}
name=${2:-}

case "$name" in
  ''|*[!A-Za-z0-9_-]*) echo 'Provide a version name containing letters, digits, underscores or hyphens' >&2; exit 1 ;;
esac
sql="auth-migrations/versions/$name.sql"

case "$command" in
  generate)
    test ! -e "$sql" || { echo 'SQL history already exists; choose a new version' >&2; exit 1; }
    "$cli" generate --config ./lib/auth.ts --output "$sql" --yes
    test -s "$sql" || { echo 'No migration SQL was generated' >&2; exit 1; }
    ;;
  apply)
    test -s "$sql" || { echo 'Reviewed generated SQL is required' >&2; exit 1; }
    temporary=$(mktemp -d)
    trap 'rm -rf "$temporary"' EXIT HUP INT TERM
    "$cli" generate --config ./lib/auth.ts --output "$temporary/pending.sql" --yes
    test -s "$temporary/pending.sql" || { echo 'No pending migration; run check to verify the installed schema' >&2; exit 1; }
    cmp "$sql" "$temporary/pending.sql" || { echo 'Pending SQL differs from the reviewed version' >&2; exit 1; }
    "$cli" migrate --config ./lib/auth.ts --yes
    "$cli" check schema --config ./lib/auth.ts
    psql "$AUTH_DATABASE_URL" --no-psqlrc --set=ON_ERROR_STOP=1 --file=auth-migrations/verify-schema.sql
    ;;
  check)
    "$cli" check schema --config ./lib/auth.ts
    psql "$AUTH_DATABASE_URL" --no-psqlrc --set=ON_ERROR_STOP=1 --file=auth-migrations/verify-schema.sql
    ;;
  *) echo 'Usage: sh auth-migrations/migrate.sh generate|apply|check VERSION' >&2; exit 1 ;;
esac
