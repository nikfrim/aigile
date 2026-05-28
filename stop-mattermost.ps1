$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$DockerConfig = Join-Path $Root ".docker-config"
$ComposeFile = Join-Path $Root "mattermost-app\docker-compose.yaml"
$NetworkFile = Join-Path $Root "mattermost-app\docker-compose.ai-network.yaml"
$EnvFile = Join-Path $Root "mattermost-app\.env"

New-Item -ItemType Directory -Force -Path $DockerConfig | Out-Null
$env:DOCKER_CONFIG = $DockerConfig
if (-not $env:DOCKER_HOST -and (Test-NetConnection 127.0.0.1 -Port 2375 -InformationLevel Quiet)) {
    $env:DOCKER_HOST = "tcp://127.0.0.1:2375"
}

docker compose -p mattermost-app -f $ComposeFile -f $NetworkFile --env-file $EnvFile down
