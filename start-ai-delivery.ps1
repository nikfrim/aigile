$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$DockerConfig = Join-Path $Root ".docker-config"
$ComposeFile = Join-Path $Root "ai-delivery-app\docker-compose.yaml"
$EnvFile = Join-Path $Root "ai-delivery-app\.env"

New-Item -ItemType Directory -Force -Path $DockerConfig | Out-Null
$env:DOCKER_CONFIG = $DockerConfig
if (-not $env:DOCKER_HOST -and (Test-NetConnection 127.0.0.1 -Port 2375 -InformationLevel Quiet)) {
    $env:DOCKER_HOST = "tcp://127.0.0.1:2375"
}

docker network create ai-delivery-net 2>$null | Out-Null
docker compose -p ai-delivery-app -f $ComposeFile --env-file $EnvFile up -d --pull if_not_present

Write-Host ""
Write-Host "n8n:        http://localhost:5678"
Write-Host "Ollama:     http://localhost:11434"
Write-Host "Open WebUI: http://localhost:3001"
