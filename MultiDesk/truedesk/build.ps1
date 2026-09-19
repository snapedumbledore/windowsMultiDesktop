# TrueDesk 一键打包脚本（Windows / PowerShell）
# 产物：dist\TrueDesk.exe（单文件、无控制台）
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path "assets\truedesk.ico")) {
    python make_icon.py
}

python -m pip install -r requirements.txt
python -m PyInstaller --noconfirm --clean build.spec

Write-Host ""
Write-Host "打包完成：$PSScriptRoot\dist\TrueDesk.exe"
Write-Host "分发前请执行冒烟测试（Win10 1903+ / Win11）：list / create / switch / status"
