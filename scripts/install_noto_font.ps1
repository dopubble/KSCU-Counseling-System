# Noto Sans KR Regular OTF — 상담일지 PDF용
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$fontDir = Join-Path $root "static\fonts"
$fontPath = Join-Path $fontDir "NotoSansKR-Regular.ttf"
$uri = "https://github.com/googlefonts/noto-cjk/raw/main/Sans/TTF/Korean/NotoSansKR-Regular.ttf"

New-Item -ItemType Directory -Force -Path $fontDir | Out-Null
if (Test-Path $fontPath) {
    Write-Host "Already exists: $fontPath"
    exit 0
}
Write-Host "Downloading NotoSansKR-Regular.ttf ..."
Invoke-WebRequest -Uri $uri -OutFile $fontPath -UseBasicParsing
Write-Host "Saved: $fontPath"
