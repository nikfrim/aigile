$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$DockerConfig = Join-Path $Root ".docker-config"
$ComposeFile = Join-Path $Root "plane-app\docker-compose.yaml"
$NetworkFile = Join-Path $Root "plane-app\docker-compose.ai-network.yaml"
$EnvFile = Join-Path $Root "plane-app\plane.env"

New-Item -ItemType Directory -Force -Path $DockerConfig | Out-Null
$env:DOCKER_CONFIG = $DockerConfig
if (-not $env:DOCKER_HOST -and (Test-NetConnection 127.0.0.1 -Port 2375 -InformationLevel Quiet)) {
    $env:DOCKER_HOST = "tcp://127.0.0.1:2375"
}

docker network create ai-delivery-net 2>$null | Out-Null
docker compose -f $ComposeFile -f $NetworkFile --env-file $EnvFile up -d --pull if_not_present

Write-Host ""
Write-Host "Plane is starting at http://localhost:8080"
