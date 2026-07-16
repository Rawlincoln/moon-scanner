# Moon Scanner — push to GitHub then deploy on Render
param(
  [string]$GitHubUser = "Rawlincoln",
  [string]$RepoName = "moon-scanner"
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".git")) {
  git init
  git branch -M main
}

git config user.email "97333385+Rawlincoln@users.noreply.github.com"
git config user.name "Rawlincoln"

$remote = "https://github.com/$GitHubUser/$RepoName.git"
$prevErr = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$existing = git remote get-url origin 2>$null
$ErrorActionPreference = $prevErr
if (-not $existing) {
  git remote add origin $remote
} else {
  git remote set-url origin $remote
}

git add -A
$status = git status --porcelain
if ($status) {
  git commit -m "Moon Scanner: deploy update"
}

git push -u origin main 2>$null
if ($LASTEXITCODE -ne 0) {
  git push -u origin main
}

Write-Host ""
Write-Host "Deploy on Render:" -ForegroundColor Green
Write-Host "https://dashboard.render.com/blueprints/new?repo=https://github.com/$GitHubUser/$RepoName" -ForegroundColor Cyan
Write-Host "Live URL: https://$RepoName.onrender.com" -ForegroundColor Cyan