@echo off
REM Zera o CAIXA depois de um teste (pede o endereco do banco, mostra o que sai e faz backup antes).
cd /d "%~dp0.."
chcp 65001 >nul
".venv\Scripts\python.exe" scripts\zerar_caixa.py
pause
