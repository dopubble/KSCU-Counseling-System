# KSCU Counseling System — 로컬 개발 환경 자동 설정 (Windows PowerShell)
# 사용법: 프로젝트 루트에서 .\setup.ps1

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host ""
Write-Host "=== KSCU Counseling System Setup ===" -ForegroundColor Cyan
Write-Host ""

# Python 실행 파일 찾기
$pythonCmd = $null
if (Get-Command python -ErrorAction SilentlyContinue) {
    $pythonCmd = "python"
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $pythonCmd = "py"
} else {
    Write-Host "오류: Python이 설치되어 있지 않습니다." -ForegroundColor Red
    Write-Host "https://www.python.org/downloads/ 에서 Python 3.10 이상을 설치한 뒤 다시 실행해 주세요."
    exit 1
}

# 1) 가상환경(venv) 생성
Write-Host "[1/4] 가상환경(venv) 생성..." -ForegroundColor Yellow
if (Test-Path "venv") {
    Write-Host "       venv 폴더가 이미 있습니다. 건너뜁니다." -ForegroundColor DarkGray
} else {
    & $pythonCmd -m venv venv
    if ($LASTEXITCODE -ne 0) {
        Write-Host "오류: 가상환경 생성에 실패했습니다." -ForegroundColor Red
        exit 1
    }
    Write-Host "       venv 생성 완료" -ForegroundColor Green
}

$venvPython = Join-Path $PSScriptRoot "venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "오류: venv\Scripts\python.exe 를 찾을 수 없습니다." -ForegroundColor Red
    exit 1
}

# 2) 가상환경에서 requirements.txt 패키지 설치
Write-Host "[2/4] 패키지 설치 (requirements.txt)..." -ForegroundColor Yellow
& $venvPython -m pip install --upgrade pip --quiet
& $venvPython -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "오류: 패키지 설치에 실패했습니다." -ForegroundColor Red
    exit 1
}
Write-Host "       패키지 설치 완료" -ForegroundColor Green

# 3) .env 파일 복사
Write-Host "[3/4] .env 파일 복사..." -ForegroundColor Yellow
if (-not (Test-Path ".env.example")) {
    Write-Host "오류: .env.example 파일이 없습니다." -ForegroundColor Red
    exit 1
}
if (Test-Path ".env") {
    Write-Host "       .env 파일이 이미 있습니다. 건너뜁니다." -ForegroundColor DarkGray
} else {
    Copy-Item ".env.example" ".env"
    Write-Host "       .env.example -> .env 복사 완료" -ForegroundColor Green
}

# 4) DB 마이그레이션
Write-Host "[4/4] DB 마이그레이션 (manage.py migrate)..." -ForegroundColor Yellow
$env:DJANGO_SETTINGS_MODULE = "kscu_counseling.settings.development"
& $venvPython manage.py migrate
if ($LASTEXITCODE -ne 0) {
    Write-Host "오류: 마이그레이션에 실패했습니다." -ForegroundColor Red
    exit 1
}
Write-Host "       마이그레이션 완료" -ForegroundColor Green

Write-Host ""
Write-Host "=== 설정 완료 ===" -ForegroundColor Green
Write-Host ""
Write-Host "다음 명령으로 서버를 실행하세요:" -ForegroundColor White
Write-Host "  .\venv\Scripts\Activate.ps1" -ForegroundColor Cyan
Write-Host "  python manage.py runserver" -ForegroundColor Cyan
Write-Host ""
Write-Host "관리자 계정 생성 (선택):" -ForegroundColor White
Write-Host "  python manage.py createsuperuser" -ForegroundColor Cyan
Write-Host ""
Write-Host "상담일지 PDF 한글 폰트 (최초 1회):" -ForegroundColor White
Write-Host "  .\scripts\install_noto_font.ps1" -ForegroundColor Cyan
Write-Host ""
