@echo off
cd /d "\\192.168.0.158\GED\LOGICIELS\Objectifs+"
git pull origin main
echo Sync le %date% a %time% >> "\\192.168.0.158\GED\LOGICIELS\Objectifs+\sync.log"