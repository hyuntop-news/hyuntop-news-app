@echo off
cd /d "%~dp0"
echo HYUNTOP NEWS dashboard is starting...
echo.
echo If the browser does not open, go to:
echo http://localhost:8501
echo.
start "" http://localhost:8501
python -m streamlit run ui.py --server.port 8501
pause
