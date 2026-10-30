@echo off
REM Abre a interface grafica no navegador (Windows). Na primeira vez instala as dependencias.
cd /d "%~dp0"
if not exist .venv ( py -3 -m venv .venv )
call .venv\Scripts\activate.bat
pip install -q -r requirements.txt
streamlit run app_gui.py
