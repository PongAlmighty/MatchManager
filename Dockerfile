FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py story.py ./
COPY static ./static
COPY templates ./templates

# Cloud Run sets $PORT; default to 8000 for docker-run.sh.
# One worker with threads so every request shares the same Challonge cache.
ENV PORT=8000
CMD exec gunicorn --bind 0.0.0.0:$PORT --workers 1 --threads 8 --timeout 60 main:app
