# Builds and pushes the three images to the ACR provisioned by infra/bicep/main.bicep.
# Usage: ./scripts/build_and_push.ps1 -AcrName acrfoundryocrxxxxx
param(
    [Parameter(Mandatory = $true)][string]$AcrName,
    [string]$Tag = "latest"
)

$root = Resolve-Path "$PSScriptRoot/.."
az acr login --name $AcrName

az acr build --registry $AcrName --image "paddle-ocr:$Tag" `
    -f "$root/infra/docker/Dockerfile.paddle" "$root"

az acr build --registry $AcrName --image "rapid-ocr:$Tag" `
    -f "$root/infra/docker/Dockerfile.rapidocr" "$root"

az acr build --registry $AcrName --image "orchestrator:$Tag" `
    -f "$root/infra/docker/Dockerfile.orchestrator" "$root"
