# Deploy (or update) the FinAssist services to Kubernetes.
# Usage, from the project root, with $hub set to your Docker Hub username:
#   $hub = "yourdockerhubname"
#   .\k8s\deploy.ps1
# The YAML files contain the placeholder DOCKERHUB_USER; it is replaced with $hub while each
# file is sent to Kubernetes, so the username is never written into the repository.

if (-not $hub) { throw 'Set $hub to your Docker Hub username first, e.g. $hub = "yourname"' }

$files = "20-config.yaml", "30-nav-service.yaml", "40-portfolio-service.yaml",
         "50-analytics-service.yaml", "60-web-ui.yaml"

foreach ($file in $files) {
    Write-Host "Applying $file"
    (Get-Content (Join-Path $PSScriptRoot $file) -Raw) -replace "DOCKERHUB_USER", $hub |
        kubectl apply -f -
    if ($LASTEXITCODE -ne 0) { throw "kubectl apply failed for $file" }
}

kubectl get pods -n finassist
