@echo off
cd /d "%~dp0"
if not exist .venv (
  echo Creando entorno virtual...
  python -m venv .venv
  .venv\Scripts\python.exe -m pip install -r requirements.txt
)
echo.
echo  Turnos Tifena en http://localhost:5000   (encargado: http://localhost:5000/admin)
echo  Cerrar esta ventana para detener el servidor.
echo.
.venv\Scripts\python.exe backend\app.py
