@echo off
REM Restaura um backup SEM abrir o sistema (use quando o banco estiver com defeito).
REM Arraste o arquivo .cxbk para cima deste arquivo, ou rode e informe o caminho.
cd /d "%~dp0.."
chcp 65001 >nul
set ARQUIVO=%~1
if "%ARQUIVO%"=="" set /p ARQUIVO=Caminho do arquivo de backup (.cxbk):
".venv\Scripts\python.exe" scripts\restaurar_backup.py "%ARQUIVO%"
pause
