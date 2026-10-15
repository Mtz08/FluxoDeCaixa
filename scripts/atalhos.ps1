# Cria/remove atalhos do Caixa da Papelaria (área de trabalho e iniciar com o Windows).
# Uso: powershell -ExecutionPolicy Bypass -File atalhos.ps1 -Acao area-de-trabalho|iniciar-com-windows|remover-inicio
param([Parameter(Mandatory = $true)][string]$Acao)

$raiz = Split-Path -Parent $PSScriptRoot
$alvo = Join-Path $raiz "scripts\iniciar.bat"
$nome = "Caixa da Papelaria.lnk"

function Criar-Atalho([string]$pasta) {
    $shell = New-Object -ComObject WScript.Shell
    $atalho = $shell.CreateShortcut((Join-Path $pasta $nome))
    $atalho.TargetPath = $alvo
    $atalho.WorkingDirectory = $raiz
    $atalho.WindowStyle = 7          # minimizado
    $atalho.IconLocation = "shell32.dll,43"
    $atalho.Description = "Abrir o Caixa da Papelaria"
    $atalho.Save()
}

switch ($Acao) {
    "area-de-trabalho"    { Criar-Atalho ([Environment]::GetFolderPath("Desktop")); Write-Host "Atalho criado na area de trabalho." }
    "iniciar-com-windows" { Criar-Atalho ([Environment]::GetFolderPath("Startup")); Write-Host "O sistema vai abrir junto com o Windows." }
    "remover-inicio" {
        $arquivo = Join-Path ([Environment]::GetFolderPath("Startup")) $nome
        if (Test-Path $arquivo) { Remove-Item $arquivo }
        Write-Host "O sistema NAO vai mais abrir junto com o Windows."
    }
    default { Write-Error "Acao desconhecida: $Acao"; exit 1 }
}
