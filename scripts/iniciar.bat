@echo off
REM Abre o Caixa da Papelaria: liga o servidor (se ainda nao estiver ligado) e abre o navegador.
setlocal
cd /d "%~dp0.."
chcp 65001 >nul
if not exist ".venv\Scripts\python.exe" (
    echo Sistema nao instalado. Rode scripts\instalar.bat primeiro.
    pause
    exit /b 1
)
if "%CAIXA_PORTA%"=="" set CAIXA_PORTA=8765
set "TESTE=import socket,sys; s=socket.socket(); s.settimeout(0.5); sys.exit(0 if s.connect_ex(('127.0.0.1',%CAIXA_PORTA%))==0 else 1)"

REM Ja esta ligado? So abre o navegador.
".venv\Scripts\python.exe" -c "%TESTE%" && goto :navegador

REM Liga o servidor numa janela minimizada (NAO FECHE essa janela enquanto usa o sistema).
start "Caixa da Papelaria - NAO FECHE esta janela" /min ".venv\Scripts\python.exe" -m app

REM Espera ate 30 segundos o servidor responder.
for /l %%i in (1,1,30) do (
    ".venv\Scripts\python.exe" -c "%TESTE%" && goto :navegador
    timeout /t 1 /nobreak >nul
)
echo O sistema demorou para abrir. Veja a janela "Caixa da Papelaria" na barra de tarefas.
pause
exit /b 1

:navegador
start "" "http://localhost:%CAIXA_PORTA%/"
exit /b 0
