FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 TZ=America/Montevideo
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt gunicorn
COPY backend ./backend
COPY frontend ./frontend
# La base SQLite y la clave de sesión viven en /app/data (volumen persistente)
VOLUME /app/data
EXPOSE 5000
CMD python backend/seed.py && exec gunicorn --chdir backend -w 2 --threads 4 -t 300 -b 0.0.0.0:5000 "app:create_app()"
