<#
Local PostgreSQL staging wizard. Not a deployment script.

Usage (PowerShell from the Agent Studio repository root):
    & .\scripts\pg_staging.ps1 -Action Check
    & .\scripts\pg_staging.ps1 -Action Migrate
    & .\scripts\pg_staging.ps1 -Action RlsTest

Requirements:
 - Existing SSH tunnel in a separate terminal:
   127.0.0.1:15429 -> 192.168.1.133 -> 127.0.0.1:54329.
 - Dedicated PostgreSQL 17.11 agent_studio DB with pgvector.
 - This script asks for studio_migrator password interactively.
 - No password is stored in files or printed in console output.
#>
[CmdletBinding()]
param(
  [Parameter()]
  [ValidateSet("Check", "Migrate", "RlsTest")]
  [string]$Action = "Check"
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$api = Join-Path $repo "services\api"
$python = Join-Path $api ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
  throw "未找到项目后端 Python 虚拟环境：$python"
}

$previousUrl = [Environment]::GetEnvironmentVariable("STUDIO_POSTGRES_ADMIN_URL", "Process")
$previousTestUrl = [Environment]::GetEnvironmentVariable("STUDIO_POSTGRES_TEST_ADMIN_URL", "Process")
if ($previousUrl -or $previousTestUrl) {
  throw "当前终端已经设置 PostgreSQL 连接变量。为避免误连接，请先在新 PowerShell 窗口运行本脚本。"
}

$secret = Read-Host "输入独立数据库 studio_migrator 的密码（不会显示）" -AsSecureString
$credential = [System.Management.Automation.PSCredential]::new("studio_migrator", $secret)
$plain = $credential.GetNetworkCredential().Password
$encoded = [Uri]::EscapeDataString($plain)
$url = "postgresql+psycopg://studio_migrator:${encoded}@127.0.0.1:15429/agent_studio?sslmode=disable"
$env:STUDIO_POSTGRES_ADMIN_URL = $url

# Minimize the lifetime of the plaintext password variable.
Remove-Variable plain, encoded, credential, secret -ErrorAction SilentlyContinue

try {
  Push-Location -LiteralPath $api
  try {
    Write-Host "正在执行只读数据库预检 ..."
    & $python "..\..\scripts\check_postgres_staging.py"
    if ($LASTEXITCODE -ne 0) {
      throw "PostgreSQL 目标或数据结构预检失败；迁移已阻止。"
    }

    if ($Action -eq "Check") {
      Write-Host "预检完成。没有更改数据库。"
      return
    }

    if ($Action -eq "Migrate") {
      $confirmation = Read-Host "仅当确认这是专属空的 agent_studio 测试库时输入 MIGRATE"
      if ($confirmation -cne "MIGRATE") {
        Write-Host "未获得迁移确认；没有更改数据库。"
        return
      }
      Write-Host "执行 V0.6 Alembic 初始迁移（不会导入 SQLite）..."
      & $python -m alembic -c alembic.ini upgrade head
      if ($LASTEXITCODE -ne 0) {
        throw "Alembic 迁移失败。请保留错误日志，不要重复手动执行 SQL。"
      }
      Write-Host "再次进行只读版本核验 ..."
      & $python "..\..\scripts\check_postgres_staging.py"
      if ($LASTEXITCODE -ne 0) { throw "迁移后结构核验失败；请停止后续操作。" }
      Write-Host "数据库迁移已执行，下一步单独运行 -Action RlsTest。"
      return
    }

    if ($Action -eq "RlsTest") {
      $env:STUDIO_POSTGRES_TEST_ADMIN_URL = $url
      Write-Host "开始真实 PostgreSQL RLS 测试（测试事务回滚）..."
      & $python "..\..\scripts\smoke_postgres_rls.py"
      if ($LASTEXITCODE -ne 0) {
        throw "RLS 测试失败。请停止数据库切换，先修复权限/迁移。"
      }
      Write-Host "真实 PostgreSQL RLS 集成测试已通过。"
    }
  }
  finally {
    Pop-Location
  }
}
finally {
  Remove-Item Env:\STUDIO_POSTGRES_ADMIN_URL -ErrorAction SilentlyContinue
  Remove-Item Env:\STUDIO_POSTGRES_TEST_ADMIN_URL -ErrorAction SilentlyContinue
  Remove-Variable url -ErrorAction SilentlyContinue
}
