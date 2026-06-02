$ErrorActionPreference = "Stop"

$env:DOCKER_HOST = "tcp://127.0.0.1:2375"
$env:DOCKER_CONFIG = Join-Path (Get-Location) ".docker-config"

docker exec ai-delivery-app-aigile-backend-1 python /app/backend/aigile_backend.py reset-demo
