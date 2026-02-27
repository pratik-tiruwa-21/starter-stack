FROM python:3.12-slim

WORKDIR /app

# Install Node.js for JavaScript execution
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates && \
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create sandbox directories
RUN mkdir -p /sandbox /persist

# Copy application
COPY main.py .

EXPOSE 8406

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8406/healthz')"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8406", "--log-level", "info"]
