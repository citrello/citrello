@echo off
rem Builds Citrello.exe (the app) and CitrelloMCP.exe (the Claude connector).
rem Keep both .exe files in the same folder. No Python is needed where they run.
cd /d "%~dp0"
echo.
echo === Building Citrello ===
echo Please close Claude Desktop first (it keeps the connector running).
echo Closing Citrello if it is running...
taskkill /IM Citrello.exe /F >nul 2>&1
taskkill /IM CitrelloMCP.exe /F >nul 2>&1
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build
echo.
echo Optional: your Mendeley app ID and secret are built in, so users can import from / send to
echo Mendeley without entering them. Press Enter to skip.
echo.
set /p CID=Mendeley client ID: 
set /p CSEC=Mendeley client secret: 
python -c "import json,sys; json.dump({'client_id':sys.argv[1].strip(),'client_secret':sys.argv[2].strip()},open('app_config.json','w'))" "%CID%" "%CSEC%"
python -m pip install --upgrade python-docx citeproc-py pyinstaller
python -m PyInstaller --noconfirm --clean --onefile --noconsole --name Citrello ^
  --add-data "ui.html;." --add-data "clip.html;." --add-data "app_config.json;." --add-data "styles;styles" ^
  --collect-data docx --collect-data citeproc app.py
python -m PyInstaller --noconfirm --onefile --console --name CitrelloMCP ^
  --add-data "styles;styles" --collect-data docx --collect-data citeproc mcp_server.py
python make_mcpb.py dist\CitrelloMCP.exe dist\Citrello.mcpb
del app_config.json
echo.
if exist dist\Citrello.exe if exist dist\Citrello.mcpb (
  echo Done: %~dp0dist\Citrello.exe, CitrelloMCP.exe and Citrello.mcpb
  echo Start Citrello.exe, then Settings - Install in Claude Desktop, and click Install in Claude.
) else (echo Build failed - see the messages above.)
pause
