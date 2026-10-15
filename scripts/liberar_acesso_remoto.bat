@echo off
REM Libera o acesso DE FORA DA LOJA pela Tailscale (rede privada criptografada).
REM A regra aceita SOMENTE a faixa da Tailscale (100.64.0.0/10): a internet continua bloqueada.
REM Pre-requisito: Tailscale instalada e conectada neste computador e no celular (mesma conta).
setlocal
chcp 65001 >nul
if "%CAIXA_PORTA%"=="" set CAIXA_PORTA=8765

net session >nul 2>&1
if errorlevel 1 (
    echo Pedindo permissao de administrador do Windows...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b 0
)

netsh advfirewall firewall delete rule name="Caixa da Papelaria (Tailscale)" >nul 2>&1
netsh advfirewall firewall add rule name="Caixa da Papelaria (Tailscale)" dir=in action=allow protocol=TCP localport=%CAIXA_PORTA% profile=any remoteip=100.64.0.0/10
if errorlevel 1 (
    echo [ERRO] Nao foi possivel criar a regra do firewall.
    pause
    exit /b 1
)
echo.
echo Pronto: aparelhos da sua conta Tailscale ja podem acessar (porta %CAIXA_PORTA%).
echo O endereco e o QR Code ficam em Configuracoes ^> Acessar pelo celular ^> De fora da loja.
echo Para desfazer: netsh advfirewall firewall delete rule name="Caixa da Papelaria (Tailscale)"
pause
