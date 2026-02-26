FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY main.py .

EXPOSE 8403

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8403/healthz')"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8403", "--log-level", "info"]
