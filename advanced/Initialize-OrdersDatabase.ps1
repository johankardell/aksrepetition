[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$HostName,
    [Parameter(Mandatory)][string]$AdminLogin,
    [Parameter(Mandatory)][ValidatePattern('^[0-9a-fA-F-]{36}$')][string]$ApiPrincipalId,
    [Parameter(Mandatory)][ValidatePattern('^[0-9a-fA-F-]{36}$')][string]$WorkerPrincipalId,
    [string]$ApiRole = 'orders_api',
    [string]$WorkerRole = 'orders_worker'
)
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
if ($ApiRole -notmatch '^[a-z_][a-z0-9_]*$' -or $WorkerRole -notmatch '^[a-z_][a-z0-9_]*$') {
    throw 'Use lowercase SQL identifiers for database roles.'
}
Get-Command psql -ErrorAction Stop | Out-Null
$env:PGHOST = $HostName
$env:PGUSER = $AdminLogin
$env:PGSSLMODE = 'verify-full'
$env:PGSSLROOTCERT = 'system'
$env:PGPASSWORD = az account get-access-token --resource https://ossrdbms-aad.database.windows.net --query accessToken -o tsv
try {
    # Run once per role pair. An existing principal is deliberately not silently remapped.
    @"
SELECT * FROM pgaadauth_create_principal_with_oid('$ApiRole', '$ApiPrincipalId', 'service', false, false);
SELECT * FROM pgaadauth_create_principal_with_oid('$WorkerRole', '$WorkerPrincipalId', 'service', false, false);
"@ | psql --dbname postgres --set ON_ERROR_STOP=1
    @"
CREATE TABLE IF NOT EXISTS public.processed_orders (
  order_id text PRIMARY KEY,
  item text NOT NULL,
  processed_at timestamptz DEFAULT now()
);
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT CONNECT ON DATABASE ordersdb TO "$ApiRole", "$WorkerRole";
GRANT USAGE ON SCHEMA public TO "$ApiRole", "$WorkerRole";
GRANT SELECT ON public.processed_orders TO "$ApiRole", "$WorkerRole";
GRANT INSERT ON public.processed_orders TO "$WorkerRole";
"@ | psql --dbname ordersdb --set ON_ERROR_STOP=1
} finally {
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
}
