FROM python:3.12-slim

RUN groupadd -r ccos && useradd -r -g ccos -d /app ccos

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

RUN mkdir -p /data && chown -R ccos:ccos /app /data

USER ccos

EXPOSE 8404

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8404/healthz')"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8404", "--log-level", "info"]
