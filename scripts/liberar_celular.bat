@echo off
REM Libera o acesso pelo celular NA REDE DA LOJA (Firewall do Windows).
REM A regra vale so para redes "Privadas" e so para aparelhos da mesma rede local.
REM NUNCA abra esta porta no roteador: o sistema nao deve ficar exposto na internet.
setlocal
chcp 65001 >nul
if "%CAIXA_PORTA%"=="" set CAIXA_PORTA=8765

REM Precisa de permissao de administrador do Windows: pede elevacao se necessario.
net session >nul 2>&1
if errorlevel 1 (
    echo Pedindo permissao de administrador do Windows...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b 0
)

netsh advfirewall firewall delete rule name="Caixa da Papelaria" >nul 2>&1
netsh advfirewall firewall add rule name="Caixa da Papelaria" dir=in action=allow protocol=TCP localport=%CAIXA_PORTA% profile=private remoteip=localsubnet
if errorlevel 1 (
    echo [ERRO] Nao foi possivel criar a regra do firewall.
    pause
    exit /b 1
)
echo.
echo Pronto: celulares no mesmo Wi-Fi ja podem acessar (porta %CAIXA_PORTA%).
echo IMPORTANTE: a rede Wi-Fi da loja precisa estar marcada como "Privada" no Windows
echo   (Configuracoes ^> Rede e Internet ^> Wi-Fi ^> propriedades da rede ^> Rede privada).
echo O endereco e o QR Code ficam em Configuracoes ^> Acessar pelo celular.
pause
