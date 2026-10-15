@echo off
REM O ADMINISTRADOR esqueceu a senha? Gera uma senha provisoria (troca obrigatoria no proximo acesso).
REM Funcionarios que esqueceram a senha: o administrador redefine pela tela Usuarios.
cd /d "%~dp0.."
chcp 65001 >nul
".venv\Scripts\python.exe" scripts\recuperar_admin.py
pause
