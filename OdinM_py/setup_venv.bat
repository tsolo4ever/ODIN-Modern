@echo off
echo Creating virtual environment...
python -m venv .venv
echo Installing requirements...
.venv\Scripts\pip install -r requirements.txt
echo Done. Run with: .venv\Scripts\python main.py
pause
