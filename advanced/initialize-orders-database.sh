#!/usr/bin/env bash
set -euo pipefail
# shellcheck source=../scripts/lib.sh
source "$(cd -- "$(dirname -- "$0")/../scripts" && pwd)/lib.sh"

host_name='' admin_login='' api_principal_id='' worker_principal_id=''
api_role='orders_api' worker_role='orders_worker'
parse_args "$@"
for parameter in host_name admin_login api_principal_id worker_principal_id; do
    [[ -n $(parameter_value "$parameter") ]] || die "--${parameter//_/-} is required."
done
[[ $api_principal_id =~ ^[0-9a-fA-F-]{36}$ && $worker_principal_id =~ ^[0-9a-fA-F-]{36}$ ]] \
    || die 'Principal IDs must match ^[0-9a-fA-F-]{36}$.'
[[ $api_role =~ ^[a-z_][a-z0-9_]*$ && $worker_role =~ ^[a-z_][a-z0-9_]*$ ]] \
    || die 'Use lowercase SQL identifiers for database roles.'
command -v psql >/dev/null || die 'psql is required.'

export PGHOST="$host_name" PGUSER="$admin_login" PGSSLMODE='verify-full' PGSSLROOTCERT='system'
trap 'unset PGPASSWORD' EXIT
PGPASSWORD=$(az account get-access-token --resource https://ossrdbms-aad.database.windows.net --query accessToken -o tsv)
[[ -n $PGPASSWORD ]] || die 'Azure returned an empty PostgreSQL access token.'
export PGPASSWORD
# An existing principal is deliberately not silently remapped.
psql --dbname postgres --set ON_ERROR_STOP=1 <<SQL
SELECT * FROM pgaadauth_create_principal_with_oid('$api_role', '$api_principal_id', 'service', false, false);
SELECT * FROM pgaadauth_create_principal_with_oid('$worker_role', '$worker_principal_id', 'service', false, false);
SQL
psql --dbname ordersdb --set ON_ERROR_STOP=1 <<SQL
CREATE TABLE IF NOT EXISTS public.processed_orders (
  order_id text PRIMARY KEY,
  item text NOT NULL,
  processed_at timestamptz DEFAULT now()
);
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT CONNECT ON DATABASE ordersdb TO "$api_role", "$worker_role";
GRANT USAGE ON SCHEMA public TO "$api_role", "$worker_role";
GRANT SELECT ON public.processed_orders TO "$api_role", "$worker_role";
GRANT INSERT ON public.processed_orders TO "$worker_role";
SQL
