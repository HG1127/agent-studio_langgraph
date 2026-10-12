# Agent Studio self-hosted CI checker: no GitHub Actions minutes.
# Intended for Windows Task Scheduler 15-minute runs (one iteration).
# Only tests trusted origin/develop in an isolated temporary worktree.
# Reports ci/windows-isolated; NEVER merges or pushes code.

[CmdletBinding()]
param(
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$expectedRemote = 'https://github.com/HG1127/agent-studio_langgraph.git'
$repository = 'HG1127/agent-studio_langgraph'
$stateDir = Join-Path $repo '.local\ci'
$statePath = Join-Path $stateDir 'poll-state.json'
$lockFile = Join-Path $stateDir 'poll.lock'
$lock = $null
$revision = $null
$githubAuthenticated = $false
$refVerified = $false

function Post-CiStatus {
    param(
        [ValidateSet('pending','success','failure')]
        [string]$State,
        [string]$Message
    )
    if (-not $githubAuthenticated -or -not $refVerified -or -not $revision) { return }
    # Narrow context: this is NOT PostgreSQL RLS or proxy integration testing.
    $cli = @(
        'api', '-X', 'POST',
        ('repos/' + $repository + '/statuses/' + $revision),
        '-f', ('state=' + $State),
        '-f', 'context=ci/windows-isolated',
        '-f', ('description=' + $Message),
        '--jq', '.state'
    )
    & gh @cli | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw 'Failed to publish GitHub commit status'
    }
}

try {
    if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
        throw 'GitHub CLI (gh) is missing in the scheduled account'
    }
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw 'Git is not on PATH'
    }
    $actual = & git -C $repo config --local --get remote.origin.url
    if ($actual -ne $expectedRemote) {
        throw 'Refusing unexpected Git remote; only the authorized private repo is supported'
    }
    New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
    try {
        $lock = [IO.File]::Open($lockFile, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    } catch [IO.IOException] {
        Write-Host 'CI watcher already running; skipping overlap.'
        exit 0
    }
    $private = & gh api ('repos/' + $repository) --jq '.private'
    if ($LASTEXITCODE -ne 0 -or $private -ne 'true') {
        throw 'Refusing repository not verified private or no GitHub authorization'
    }
    $githubAuthenticated = $true
    & git -C $repo fetch --no-tags origin '+refs/heads/develop:refs/remotes/origin/develop'
    if ($LASTEXITCODE -ne 0) {
        throw 'Could not fetch authorized origin/develop'
    }
    $revision = (& git -C $repo rev-parse --verify 'refs/remotes/origin/develop^{commit}').Trim()
    if ($LASTEXITCODE -ne 0 -or $revision -notmatch '^[0-9a-fA-F]{40}$') {
        throw 'Invalid fetched develop commit'
    }
    $githubSha = & gh api ('repos/' + $repository + '/branches/develop') --jq '.commit.sha'
    if ($LASTEXITCODE -ne 0 -or $githubSha -ne $revision) {
        throw 'Remote SHA mismatch; retry at next polling interval'
    }
    $refVerified = $true
    if (-not $Force -and (Test-Path -LiteralPath $statePath)) {
        try {
            $previous = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
            if ($previous.revision -eq $revision -and $previous.outcome -eq 'passed') {
                Write-Host ('SKIP: previously passed ' + $revision.Substring(0, 12))
                exit 0
            }
        } catch {
            Write-Warning 'Previous state invalid; testing again.'
        }
    }
    Write-Host ('CI_WATCH_TEST_SHA=' + $revision)
    Post-CiStatus 'pending' 'Windows CI running (PG17/proxy not included)'
    & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'ci.ps1') -Mode Full -Revision $revision
    $passed = ($LASTEXITCODE -eq 0)
    $state = $(if ($passed) { 'passed' } else { 'failed' })
    $record = [ordered]@{
        revision = $revision
        outcome = $state
        tested_at_utc = [datetime]::UtcNow.ToString('o')
        gate = 'ci/windows-isolated'
        scope = 'Ruff, unittest, architecture, Next checks/build, LangGraph stub smoke, offline Alembic'
    }
    [IO.File]::WriteAllText(
        $statePath,
        ($record | ConvertTo-Json -Depth 4),
        [Text.UTF8Encoding]::new($false)
    )
    if ($passed) {
        Post-CiStatus 'success' 'Local suite passed; real PG17/proxy not included'
        Write-Host ('CI_WATCH_PASS=' + $revision)
    } else {
        Post-CiStatus 'failure' 'Local CI failed; main not merged'
        throw 'Local CI suite failed'
    }
}
catch {
    $safeError = $_.Exception.Message
    if ($githubAuthenticated -and $refVerified -and $revision) {
        try {
            Post-CiStatus 'failure' 'Self-hosted CI interrupted; manual review required'
        } catch {
            Write-Warning 'Could not post failure status to GitHub; manual inspection required.'
        }
    }
    Write-Error -Message ('CI WATCH FAILED: ' + $safeError) -ErrorAction Continue
    exit 1
}
finally {
    if ($lock) { $lock.Dispose() }
}
