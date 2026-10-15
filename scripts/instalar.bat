@echo off
REM ============================================================
REM  Instalacao do Caixa da Papelaria
REM  - cria o ambiente Python do sistema (.venv)
REM  - instala as bibliotecas nas versoes testadas (constraints.txt)
REM  - prepara o banco de dados
REM  - cria o atalho na area de trabalho
REM  Pode ser rodado de novo para ATUALIZAR: os dados nao sao apagados.
REM ============================================================
setlocal
cd /d "%~dp0.."
chcp 65001 >nul
echo.
echo === Instalando o Caixa da Papelaria ===
echo.

REM ---- 1. Procura o Python 3.12 ou mais novo ----
set "PY="
for %%V in (3.14 3.13 3.12) do (
    if not defined PY (
        py -%%V -c "pass" >nul 2>&1 && set "PY=py -%%V"
    )
)
if not defined PY (
    python -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo [ERRO] Python 3.12 ou mais novo nao encontrado.
    echo.
    echo Instale pelo site https://www.python.org/downloads/
    echo  - marque a opcao "Add python.exe to PATH" na primeira tela
    echo ou rode no terminal:  winget install -e --id Python.Python.3.13
    echo Depois rode este instalador de novo.
    pause
    exit /b 1
)
echo [1/4] Python encontrado: %PY%

REM ---- 2. Ambiente do sistema e bibliotecas ----
if not exist ".venv\Scripts\python.exe" (
    %PY% -m venv .venv || goto :erro
)
".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip || goto :erro
".venv\Scripts\python.exe" -m pip install --quiet -c constraints.txt -e . || goto :erro
echo [2/4] Bibliotecas instaladas.

REM ---- 3. Banco de dados ----
".venv\Scripts\python.exe" scripts\migrar.py || goto :erro
echo [3/4] Banco de dados pronto.

REM ---- 4. Atalho ----
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\atalhos.ps1 -Acao area-de-trabalho
echo [4/4] Atalho "Caixa da Papelaria" criado na area de trabalho.

echo.
set /p INICIO=Abrir o sistema automaticamente quando o Windows ligar? (S/N):
if /i "%INICIO%"=="S" powershell -NoProfile -ExecutionPolicy Bypass -File scripts\atalhos.ps1 -Acao iniciar-com-windows

echo.
echo Para usar no CELULAR, rode tambem: scripts\liberar_celular.bat
echo.
echo === Instalacao concluida! Abra pelo atalho "Caixa da Papelaria". ===
pause
exit /b 0

:erro
echo.
echo [ERRO] A instalacao parou. Tire uma foto desta tela e envie ao responsavel tecnico.
pause
exit /b 1
