@echo off
rem Runs Citrello without building an .exe (needs Python + python-docx).
cd /d "%~dp0"
start "" pythonw app.py
