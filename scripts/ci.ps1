# Agent Studio: free, local, isolated CI on a committed Git revision.
# Windows PowerShell 5.1-compatible; ASCII-only. No cloud billing.
# Never touches the development SQLite file or the Baota PostgreSQL database.

[CmdletBinding()]
param(
    [ValidateSet('Quick', 'Full')]
    [string]$Mode = 'Full',
    [ValidatePattern('^[a-zA-Z0-9_./-]{1,100}$')]
    [string]$Revision = 'HEAD',
    [switch]$IncludeProxySmoke
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$apiPython = Join-Path $repo 'services\api\.venv\Scripts\python.exe'
$nodeModules = Join-Path $repo 'apps\web\node_modules'
$ciHome = Join-Path $repo '.local\ci'
$reports = Join-Path $ciHome 'reports'
$checkouts = Join-Path $ciHome 'worktrees'
$worktree = Join-Path $checkouts ([guid]::NewGuid().ToString('N'))
$created = $false
$success = $false
$failure = $null
$stepResults = [System.Collections.Generic.List[object]]::new()
$sha = $null
$savedEnv = @{}
$protectedEnv = @(
    'AGENT_STUDIO_DB_PATH',
    'AGENT_STUDIO_CHECKPOINT_PATH',
    'MODEL_API_KEY',
    'MODEL_BASE_URL',
    'MODEL_NAME',
    'STUDIO_MODEL_ENCRYPTION_KEY',
    'STUDIO_POSTGRES_ADMIN_URL',
    'STUDIO_POSTGRES_TEST_ADMIN_URL',
    'STUDIO_RUNTIME_PROVISION_APPROVED',
    'STUDIO_DATABASE_BACKEND',
    'APP_ENV',
    'GH_TOKEN',
    'GITHUB_TOKEN',
    'GITHUB_ACTIONS',
    'NEXT_TELEMETRY_DISABLED',
    'CI'
)

function Run-Gate {
    param(
        [string]$Name,
        [string]$Directory,
        [string]$Executable,
        [string[]]$Arguments,
        [switch]$DiscardStdout
    )
    $started = [datetime]::UtcNow
    Write-Host ('--- CI STEP: ' + $Name + ' ---')
    Push-Location -LiteralPath $Directory
    try {
        if ($DiscardStdout) {
            & $Executable @Arguments | Out-Null
        } else {
            & $Executable @Arguments
        }
        $code = $LASTEXITCODE
        if ($code -ne 0) {
            $stepResults.Add([ordered]@{ step=$Name; outcome='failed'; exit_code=$code; elapsed_seconds=[math]::Round(([datetime]::UtcNow-$started).TotalSeconds, 1) })
            throw ('CI step failed: ' + $Name + ' (exit ' + $code + ')')
        }
        $stepResults.Add([ordered]@{ step=$Name; outcome='passed'; exit_code=0; elapsed_seconds=[math]::Round(([datetime]::UtcNow-$started).TotalSeconds, 1) })
        Write-Host ('PASS: ' + $Name)
    } finally {
        Pop-Location
    }
}

try {
    if (-not (Test-Path -LiteralPath $apiPython -PathType Leaf)) {
        throw 'Python development venv is missing. Install services/api/requirements-dev.txt first.'
    }
    if (-not (Test-Path -LiteralPath $nodeModules -PathType Container)) {
        throw 'Web node_modules missing. Run npm ci from apps/web first.'
    }
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw 'Git must be installed and on PATH.'
    }
    if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
        throw 'Node/npm must be installed and on PATH.'
    }
    $sha = (& git -C $repo rev-parse --verify ($Revision + '^{commit}')).Trim()
    if ($LASTEXITCODE -ne 0 -or $sha -notmatch '^[0-9a-fA-F]{40}$') {
        throw 'Expected a locally known Git commit SHA/ref.'
    }
    New-Item -ItemType Directory -Path $checkouts, $reports -Force | Out-Null
    Write-Host ('CI_REVISION=' + $sha)
    Write-Host ('CI_MODE=' + $Mode)
    # A separate temporary Git worktree ensures Next build and tests cannot
    # overwrite the currently running local Web/API or its SQLite database.
    & git -C $repo worktree add --detach --quiet $worktree $sha
    if ($LASTEXITCODE -ne 0) {
        throw 'Isolated git worktree creation failed; user checkout is unchanged.'
    }
    $created = $true
    if ($Mode -eq 'Quick') {
        # Quick checks can reuse read-only dependencies; no Turbopack build.
        New-Item -ItemType Junction -Path (Join-Path $worktree 'apps\web\node_modules') -Target $nodeModules | Out-Null
    }
    foreach ($name in $protectedEnv) {
        $savedEnv[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        Remove-Item -Path ('Env:\' + $name) -ErrorAction SilentlyContinue
    }
    $env:APP_ENV = 'development'
    $env:STUDIO_DATABASE_BACKEND = 'sqlite'
    $env:CI = '1'
    $env:NEXT_TELEMETRY_DISABLED = '1'
    $env:AGENT_STUDIO_DB_PATH = Join-Path $worktree '.local\ci-db.sqlite3'
    # Leave AGENT_STUDIO_CHECKPOINT_PATH unset. Each smoke script replaces
    # DB_PATH with its own temporary database and derives its own checkpoint.
    $api = Join-Path $worktree 'services\api'
    $web = Join-Path $worktree 'apps\web'

    if ($Mode -eq 'Full') {
        # Turbopack forbids a junction node_modules pointing outside its root.
        # npm ci creates a separate locked dependency tree within the worktree.
        Run-Gate 'web-locked-dependency-install' $web 'npm.cmd' @('ci','--no-audit','--no-fund','--prefer-offline')
    }

    Run-Gate 'python-ruff-lint' $api $apiPython @('-m','ruff','check','.')
    Run-Gate 'python-ruff-format' $api $apiPython @('-m','ruff','format','--check','.')
    Run-Gate 'python-scripts-lint' $api $apiPython @('-m','ruff','check','--config','pyproject.toml','../../scripts/plan_sqlite_migration.py','../../scripts/smoke_postgres_rls.py','../../scripts/provision_pg_runtime.py')
    Run-Gate 'python-scripts-format' $api $apiPython @('-m','ruff','format','--check','--config','pyproject.toml','../../scripts/plan_sqlite_migration.py','../../scripts/smoke_postgres_rls.py','../../scripts/provision_pg_runtime.py')
    Run-Gate 'python-architecture' $api $apiPython @('../../scripts/check_architecture.py')
    Run-Gate 'python-unit-contracts' $api $apiPython @('-m','unittest','discover','-s','tests','-q')
    Run-Gate 'web-eslint' $web 'npm.cmd' @('run','lint')
    Run-Gate 'web-prettier' $web 'npm.cmd' @('run','format:check')
    Run-Gate 'web-typescript' $web 'npm.cmd' @('run','typecheck')

    if ($Mode -eq 'Full') {
        Run-Gate 'postgres-alembic-offline-sql' $api $apiPython @('-m','alembic','-c','alembic.ini','upgrade','head','--sql') -DiscardStdout
        Run-Gate 'langgraph-process-smoke-stub' $api $apiPython @('../../scripts/smoke_process.py')
        Run-Gate 'web-production-build' $web 'npm.cmd' @('run','build')
        if ($IncludeProxySmoke) {
            # Unlike the unit tests, the legacy proxy test binds port 3000 and
            # must not disturb an already running preview server.
            $socket = New-Object System.Net.Sockets.TcpClient
            try {
                $attempt = $socket.BeginConnect('127.0.0.1', 3000, $null, $null)
                if ($attempt.AsyncWaitHandle.WaitOne(300, $false) -and $socket.Connected) {
                    throw 'Port 3000 is in use; cannot run proxy smoke without disturbing local preview.'
                }
            } finally {
                $socket.Dispose()
            }
            Run-Gate 'web-cookie-proxy-smoke' $api $apiPython @('../../scripts/smoke_web_proxy.py')
        } else {
            Write-Host 'SKIPPED: web-cookie-proxy-smoke (opt in: -IncludeProxySmoke; port 3000 must be free)'
        }
    }
    $success = $true
}
catch {
    $failure = $_.Exception.Message
    Write-Error -Message ('CI FAILED: ' + $failure) -ErrorAction Continue
}
finally {
    foreach ($name in $protectedEnv) {
        $original = $savedEnv[$name]
        if ($null -eq $original) {
            Remove-Item -Path ('Env:\' + $name) -ErrorAction SilentlyContinue
        } else {
            [Environment]::SetEnvironmentVariable($name, $original, 'Process')
        }
    }
    if ($created) {
        & git -C $repo worktree remove --force $worktree 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Write-Warning 'Temporary CI worktree cleanup failed; inspect git worktree list, do not delete other worktrees.'
            $success = $false
        }
    }
    if ($sha -and (Test-Path -LiteralPath $reports)) {
        $stamp = [datetime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
        $reportPath = Join-Path $reports ($stamp + '-' + $sha.Substring(0, 12) + '.json')
        $report = [ordered]@{
            schema_version = 1
            revision = $sha
            mode = $Mode
            proxy_smoke = [bool]$IncludeProxySmoke
            outcome = $(if ($success) { 'passed' } else { 'failed' })
            failure = $failure
            completed_at_utc = [datetime]::UtcNow.ToString('o')
            steps = @($stepResults.ToArray())
            notes = @(
                'No production database used; PostgreSQL real RLS not tested here',
                'Model smoke uses a local fake HTTP model',
                'GitHub Actions hosted billing is not involved'
            )
        }
        [IO.File]::WriteAllText($reportPath, ($report | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))
        Write-Host ('CI_REPORT=' + $reportPath)
    }
}
if ($success) {
    Write-Host ('LOCAL_CI_PASS=' + $sha)
    exit 0
}
Write-Host ('LOCAL_CI_FAIL=' + $sha)
exit 1
