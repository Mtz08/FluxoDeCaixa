@echo off
REM Faz um backup agora (nas pastas configuradas no sistema), sem abrir o navegador.
cd /d "%~dp0.."
chcp 65001 >nul
".venv\Scripts\python.exe" scripts\backup_manual.py
pause
