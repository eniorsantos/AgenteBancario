#!/usr/bin/env bash
# Abre a interface grafica no navegador (Linux/macOS). Na primeira vez instala as dependencias.
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate
pip install -q -r requirements.txt
streamlit run app_gui.py
