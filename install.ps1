<#
.SYNOPSIS
  specback installer — interactive or CLI-driven skill installer for coding agents

.DESCRIPTION
  Installs the specback skill to one or more coding agents:
  Claude Code, Codex CLI, OpenCode, GitHub Copilot, Cursor, Other.

.PARAMETER Agent
  Comma-separated agent keys: claude, codex, opencode, copilot, cursor, other, all

.PARAMETER Level
  Install level: user, project, both

.PARAMETER DryRun
  Print what would be done without making any changes.

.PARAMETER Search
  Also install the specback-search companion (CLI + MCP server).
  By default specback-search is SKIPPED (lightweight install).

.EXAMPLE
  .\install.ps1                          interactive mode
  .\install.ps1 -DryRun                  dry-run (interactive)
  .\install.ps1 -Agent claude,opencode -Level user   non-interactive
  .\install.ps1 -Agent all -Level both   all agents, both levels
  .\install.ps1 -Search -Agent claude -Level user    include specback-search

.NOTES
  Environment variables (fallback): $env:SPECBACK_AGENT, $env:SPECBACK_LEVEL,
  $env:SPECBACK_SEARCH (1/true/yes/on to include specback-search)
#>

param(
  [string]$Agent = "",
  [string]$Level = "",
  [switch]$DryRun,
  [switch]$InstallDeps,
  [switch]$Search
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SkillSrc = Join-Path $ScriptDir "skills\specback"
$SearchSkillSrc = Join-Path $ScriptDir "skills\specback-search"

if (-not (Test-Path $SkillSrc)) {
  Write-Host "Error: skills/specback/ not found alongside this script."
  Write-Host "Run this script from the root of the specback repository."
  exit 1
}

# Dev-only names never shipped to the target. Kept in sync with
# scripts/specback_install.py and install.sh.
$DevExcludedDirs = @('tests','__pycache__','.pytest_cache','.mypy_cache','.ruff_cache','.specback','graphify-out')
$DevExcludedFiles = @('dev-requirements.txt')

# Shared assets copied alongside the core skill. Kept in sync with install.sh.
$SharedDirs = @('scripts','references','schemas','agents','templates','variants')

# ── Agent list ─────────────────────────────────────────────────────────
$AgentNames = @()
$AgentKeys  = @()

function Add-Agent($name, $key) {
  $script:AgentNames += $name
  $script:AgentKeys  += $key
}

Add-Agent "Claude Code" "claude"
Add-Agent "Codex CLI" "codex"
Add-Agent "OpenCode" "opencode"
Add-Agent "GitHub Copilot" "copilot"
Add-Agent "Cursor" "cursor"
Add-Agent "Other (.agents/skills/)" "other"

# ── Helper: valid agent key? ──────────────────────────────────────────
function Is-ValidKey($key) {
  return $script:AgentKeys -contains $key
}

# ── Helper: install paths ─────────────────────────────────────────────
function Get-UserPath($key) {
  switch ($key) {
    "claude"   { return "$HOME\.claude\skills\specback" }
    "codex"    { return "$HOME\.codex\skills\specback" }
    "opencode" { return "$HOME\.opencode\skills\specback" }
    "copilot"  { return "$HOME\.copilot\skills\specback" }
    "cursor"   { return "$HOME\.cursor\skills\specback" }
    "other"    { return "$HOME\.agents\skills\specback" }
  }
}

function Get-ProjPath($key) {
  switch ($key) {
    "claude"   { return ".claude\skills\specback" }
    "codex"    { return ".codex\skills\specback" }
    "opencode" { return ".opencode\skills\specback" }
    "copilot"  { return ".github\skills\specback" }
    "cursor"   { return ".cursor\skills\specback" }
    "other"    { return ".agents\skills\specback" }
  }
}

# ── Helper: copy dir excluding dev-only artifacts ─────────────────────
function Copy-TreeExcludingDev($src, $dst) {
  New-Item -ItemType Directory -Force -Path $dst | Out-Null
  Get-ChildItem -Path $src -Force |
    Where-Object { $_.Name -notin $DevExcludedDirs -and -not $_.Name.StartsWith('.') -and $_.Name -notin $DevExcludedFiles } |
    ForEach-Object {
      $target = Join-Path $dst $_.Name
      if ($_.PSIsContainer) {
        Copy-TreeExcludingDev $_.FullName $target
      } else {
        Copy-Item -Force $_.FullName $target
      }
    }
}

# ── Helper: install ───────────────────────────────────────────────────
function Install-Skill($dest, $label) {
  if (-not $dest) { return }

  if ($DryRun) {
    Write-Host "  ⏺  $dest ($label)"
    if ($InstallSearch -and (Test-Path $SearchSkillSrc)) {
      Write-Host "  ⏺  $($dest -replace 'specback$', 'specback-search') ($label, specback-search)"
    }
    return
  }

  Copy-TreeExcludingDev $SkillSrc $dest
  Write-Host "  ✅ $dest ($label)"

  # Copy shared assets (scripts/, references/, schemas/, agents/, templates/, variants/)
  foreach ($dir in $SharedDirs) {
    $sharedSrc = Join-Path $ScriptDir $dir
    if (Test-Path $sharedSrc) {
      Copy-TreeExcludingDev $sharedSrc (Join-Path $dest $dir)
    }
  }

  # Install companion: specback-search (optional, off by default)
  if ($InstallSearch) {
    $searchDest = $dest -replace 'specback$', 'specback-search'
    if (Test-Path $SearchSkillSrc) {
      Copy-TreeExcludingDev $SearchSkillSrc $searchDest
      Write-Host "  ✅ $searchDest ($label, specback-search)"
    }
  }
}

# ── Optional dependency installer ──────────────────────────────────────────
function Install-Deps {
  $req = Join-Path $SkillSrc "scripts\requirements.txt"
  if (-not (Test-Path $req)) {
    Write-Host "  ⚠️  requirements.txt not found at $req"
    return
  }
  if ($DryRun) {
    Write-Host "  ⏺  pip install -r $req"
    return
  }
  Write-Host ""
  Write-Host "Installing optional Python dependencies (tree-sitter grammars)..."
  pip install -r $req 2>&1 | Select-Object -Last 3
  Write-Host "  ✅ Optional dependencies installed"
}

# ── Resolve input source: CLI > env > interactive ────────────────────
$ResolvedAgent = if ($Agent) { $Agent } else { $env:SPECBACK_AGENT }
$ResolvedLevel = if ($Level) { $Level } else { $env:SPECBACK_LEVEL }

# Search companion: -Search switch OR SPECBACK_SEARCH env (1/true/yes/on)
$InstallSearch = $Search
if ($env:SPECBACK_SEARCH -match '^(1|true|yes|on)$') { $InstallSearch = $true }
if (-not $Search -and $env:SPECBACK_SEARCH -and $env:SPECBACK_SEARCH -notmatch '^(1|true|yes|on)$') {
  $InstallSearch = $false
}

# ── Main ──────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "specback installer v0.2.0"
Write-Host "======================="
Write-Host ""

# ── Non-interactive mode ──────────────────────────────────────────────
if ($ResolvedAgent) {
  # Parse agent keys
  $selectedKeys = @()
  $parts = $ResolvedAgent -split ',' | ForEach-Object { $_.Trim() }
  foreach ($part in $parts) {
    if ($part -eq "all") {
      $selectedKeys = $AgentKeys
      break
    } elseif (Is-ValidKey $part) {
      $selectedKeys += $part
    } else {
      Write-Host "Warning: unknown agent key '$part', skipping"
    }
  }

  # Resolve level
  $installUser = $true
  $installProj = $false
  $useLevel = if ($ResolvedLevel) { $ResolvedLevel } else { "both" }
  switch ($useLevel) {
    "project" { $installUser = $false; $installProj = $true }
    "both"    { $installUser = $true;  $installProj = $true }
    default   { $installUser = $true;  $installProj = $false }
  }

  if ($selectedKeys.Count -eq 0) {
    Write-Host "No valid agents selected. Use: claude, codex, opencode, copilot, cursor, other, all"
    exit 1
  }

  Write-Host "Installing specback to:"
  Write-Host ""

  $installed = 0
  foreach ($key in $selectedKeys) {
    # Find display name
    $label = $key
    $idx = $AgentKeys.IndexOf($key)
    if ($idx -ge 0) { $label = $AgentNames[$idx] }

    if ($installUser) {
      $dest = Get-UserPath $key
      Install-Skill $dest $label
      $installed++
    }

    if ($installProj) {
      $dest = Get-ProjPath $key
      if ($dest) {
        Install-Skill $dest $label
        $installed++
      }
    }
  }

  Write-Host ""
  if ($DryRun) {
    Write-Host "Dry-run complete. No changes were made."
  } else {
    if ($InstallDeps) { Install-Deps }
    if ($InstallSearch) {
      Write-Host "Done. specback and specback-search are now installed."
    } else {
      Write-Host "Done. specback is now installed (specback-search skipped)."
      Write-Host "Re-run with -Search to add the search companion."
    }
  }
  Write-Host ""
  exit 0
}

# ── Interactive mode ──────────────────────────────────────────────────

# ── Select search companion ───────────────────────────────────────────
if (-not $env:SPECBACK_SEARCH) {
  Write-Host "Install the specback-search companion (CLI + MCP server)?"
  Write-Host "  [y/N] (default: No — lightweight install)"
  $searchChoice = Read-Host "> "
  Write-Host ""
  $InstallSearch = $searchChoice -match '^(y|Y|yes|YES)$'
} else {
  $InstallSearch = $env:SPECBACK_SEARCH -match '^(1|true|yes|on)$'
}

# ── Select level ──────────────────────────────────────────────────────
if ($ResolvedLevel) {
  switch ($ResolvedLevel) {
    "project" { $installUser = $false; $installProj = $true }
    "both"    { $installUser = $true;  $installProj = $true }
    default   { $installUser = $true;  $installProj = $false }
  }
} else {
  Write-Host "Select install level:"
  Write-Host "  1) User level (available for all projects)"
  Write-Host "  2) Project level (this directory only)"
  Write-Host "  3) Both"
  $levelChoice = Read-Host "> "
  Write-Host ""

  $installUser = $true
  $installProj = $false
  if ($levelChoice -eq "2") { $installUser = $false; $installProj = $true }
  if ($levelChoice -eq "3") { $installUser = $true;  $installProj = $true }
}

# ── Select agents ─────────────────────────────────────────────────────
if ($ResolvedAgent) {
  $selectedKeys = @()
  $parts = $ResolvedAgent -split ',' | ForEach-Object { $_.Trim() }
  foreach ($part in $parts) {
    if ($part -eq "all") {
      $selectedKeys = $AgentKeys
      break
    } elseif (Is-ValidKey $part) {
      $selectedKeys += $part
    }
  }
  $selectedIndices = @()
  foreach ($key in $selectedKeys) {
    $idx = $AgentKeys.IndexOf($key)
    if ($idx -ge 0) { $selectedIndices += $idx }
  }
} else {
  Write-Host "Available agents:"
  for ($i = 0; $i -lt $AgentNames.Count; $i++) {
    Write-Host ("  " + ($i+1) + ") " + $AgentNames[$i])
  }
  Write-Host ""
  Write-Host "Select agents to install (comma separated, e.g. 1,3,6):"
  $agentSel = Read-Host "> "
  Write-Host ""

  $selectedIndices = @()
  $selNums = $agentSel -split ',' | ForEach-Object { $_.Trim() }
  foreach ($n in $selNums) {
    $idx = [int]$n - 1
    if ($idx -ge 0 -and $idx -lt $AgentNames.Count) {
      $selectedIndices += $idx
    }
  }
}

# ── Install ───────────────────────────────────────────────────────────
Write-Host ""
Write-Host "Installing specback to:"
Write-Host ""

$installed = 0
foreach ($idx in $selectedIndices) {
  $key = $AgentKeys[$idx]
  $label = $AgentNames[$idx]

  if ($installUser) {
    $dest = Get-UserPath $key
    Install-Skill $dest $label
    $installed++
  }

  if ($installProj) {
    $dest = Get-ProjPath $key
    if ($dest) {
      Install-Skill $dest $label
      $installed++
    }
  }
}

Write-Host ""
if ($DryRun) {
  Write-Host "Dry-run complete. No changes were made."
} else {
  if ($InstallDeps) { Install-Deps }
  if ($InstallSearch) {
    Write-Host "Done. specback and specback-search are now installed."
  } else {
    Write-Host "Done. specback is now installed (specback-search skipped)."
    Write-Host "Re-run with -Search to add the search companion."
  }
}
Write-Host ""
