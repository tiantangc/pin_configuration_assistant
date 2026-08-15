@echo off
chcp 65001 >nul
cd /d F:\pin_configuration_assistant
echo === Pin Configuration Assistant - Git Push ===
echo Repo: https://github.com/tiantangc/pin_configuration_assistant
echo.
git init
git add .
set /p MSG=Enter commit message (e.g. update solver): 
if "%MSG%"=="" set MSG=update
git commit -m "%MSG%"
git branch -M main
git remote remove origin 2>nul
git remote add origin https://github.com/tiantangc/pin_configuration_assistant
git push -u origin main
echo.
echo If you see an error above, check your GitHub login, then run this file again.
pause
