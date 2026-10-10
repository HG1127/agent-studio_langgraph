# Agent Studio PostgreSQL staging command (ASCII-only for Windows PowerShell 5.1).
# No changes to database are made by -Action Check.
# Keep SSH tunnel active: Windows 127.0.0.1:15429 -> Baota 127.0.0.1:54329.
# Do not add database passwords to shell history or source control.

[CmdletBinding()]
param(
    [ValidateSet('Check', 'Migrate', 'RlsTest')]
    [string]$Action = 'Check'
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$api = Join-Path $repo 'services\api'
$python = Join-Path $api '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python virtual environment not found: $python"
}
if ([Environment]::GetEnvironmentVariable('STUDIO_POSTGRES_ADMIN_URL', 'Process') -or
    [Environment]::GetEnvironmentVariable('STUDIO_POSTGRES_TEST_ADMIN_URL', 'Process')) {
    throw 'PostgreSQL URL already set. Run from a fresh PowerShell window to prevent accidental target reuse.'
}

$tcp = New-Object System.Net.Sockets.TcpClient
try {
    $connection = $tcp.BeginConnect('127.0.0.1', 15429, $null, $null)
    if (-not $connection.AsyncWaitHandle.WaitOne(3000) -or -not $tcp.Connected) {
        throw 'SSH tunnel not available at 127.0.0.1:15429. Keep the SSH terminal open.'
    }
    $tcp.EndConnect($connection)
}
finally {
    $tcp.Dispose()
}

$url = $null
$securePassword = $null
$credential = $null
$plain = $null
$encoded = $null

try {
    $securePassword = Read-Host 'Enter the studio_migrator PostgreSQL password (hidden)' -AsSecureString
    $credential = [System.Management.Automation.PSCredential]::new('studio_migrator', $securePassword)
    $plain = $credential.GetNetworkCredential().Password
    # Staging was initialized with: openssl rand -hex 32 (64 hex digits).
    # Fail locally when clipboard/Read-Host submits a truncated credential.
    # If the database password was intentionally rotated to another format,
    # update this staging-only validation after verifying the new credential.
    if ($plain -cnotmatch '^[0-9a-fA-F]{64}$') {
        throw 'Password input does not match the original 64-character hexadecimal secret. Check clipboard paste and the admin_password file. No database connection attempted.'
    }
    $encoded = [Uri]::EscapeDataString($plain)
    $url = 'postgresql+psycopg://studio_migrator:' + $encoded + '@127.0.0.1:15429/agent_studio?sslmode=disable'
    $env:STUDIO_POSTGRES_ADMIN_URL = $url

    Remove-Variable plain, encoded, credential, securePassword -ErrorAction SilentlyContinue

    Push-Location -LiteralPath $api
    try {
        Write-Host 'Running READ-ONLY PostgreSQL staging preflight...'
        & $python '..\..\scripts\check_postgres_staging.py'
        if ($LASTEXITCODE -ne 0) {
            throw 'Preflight failed. No migration was started. Check the error before retrying.'
        }

        if ($Action -eq 'Check') {
            Write-Host 'Preflight complete. No database objects were changed.'
            return
        }

        if ($Action -eq 'Migrate') {
            Write-Host 'CAUTION: This will create Alembic tables/RLS roles in the dedicated agent_studio database.'
            $approval = Read-Host 'Type MIGRATE to confirm the verified staging database'
            if ($approval -cne 'MIGRATE') {
                Write-Host 'Migration cancelled before modifying the database.'
                return
            }
            & $python -m alembic -c alembic.ini upgrade head
            if ($LASTEXITCODE -ne 0) {
                throw 'Alembic migration failed. Do not manually DROP tables or rerun SQL.'
            }
            Write-Host 'Checking the schema after migration...'
            & $python '..\..\scripts\check_postgres_staging.py'
            if ($LASTEXITCODE -ne 0) {
                throw 'Post-migration verification failed. Stop and review the output.'
            }
            Write-Host 'Migration verified. RLS tests are a separate step.'
            return
        }

        if ($Action -eq 'RlsTest') {
            $env:STUDIO_POSTGRES_TEST_ADMIN_URL = $url
            Write-Host 'Running PostgreSQL RLS integration tests on synthetic rollback-only data...'
            & $python '..\..\scripts\smoke_postgres_rls.py'
            if ($LASTEXITCODE -ne 0) {
                throw 'RLS integration test failed. Do not activate the PostgreSQL backend.'
            }
            Write-Host 'PostgreSQL RLS integration test passed.'
        }
    }
    finally {
        Pop-Location
    }
}
finally {
    Remove-Item Env:\STUDIO_POSTGRES_ADMIN_URL -ErrorAction SilentlyContinue
    Remove-Item Env:\STUDIO_POSTGRES_TEST_ADMIN_URL -ErrorAction SilentlyContinue
    Remove-Variable url, plain, encoded, credential, securePassword -ErrorAction SilentlyContinue
}
