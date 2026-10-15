@echo off
REM Liga ou desliga a abertura automatica do sistema quando o Windows inicia.
cd /d "%~dp0.."
chcp 65001 >nul
echo 1 - Abrir o sistema junto com o Windows
echo 2 - NAO abrir junto com o Windows
set /p OPCAO=Escolha (1 ou 2):
if "%OPCAO%"=="1" powershell -NoProfile -ExecutionPolicy Bypass -File scripts\atalhos.ps1 -Acao iniciar-com-windows
if "%OPCAO%"=="2" powershell -NoProfile -ExecutionPolicy Bypass -File scripts\atalhos.ps1 -Acao remover-inicio
pause
