FROM python:3.12-slim

WORKDIR /app

# Install system dependencies (Node.js for JS execution, common build tools)
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates gcc g++ && \
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Install Python dependencies (server)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-install commonly requested Python packages for agent autonomy
# These are packages the agent frequently needs for tasks like
# presentations, data analysis, charting, document generation, etc.
RUN pip install --no-cache-dir \
    python-pptx==1.0.2 \
    python-docx==1.1.2 \
    openpyxl==3.1.5 \
    matplotlib==3.10.0 \
    pandas==2.2.3 \
    numpy==2.2.1 \
    Pillow==11.1.0 \
    requests==2.32.3 \
    jinja2==3.1.5 \
    pyyaml==6.0.2 \
    tabulate==0.9.0 \
    qrcode==8.0 \
    reportlab==4.2.5 \
    markdown==3.7

# Create sandbox directories
RUN mkdir -p /sandbox /persist

# Copy application
COPY main.py .

# Allow longer timeout for pip install + code execution
ENV MAX_TIMEOUT=120
ENV MAX_OUTPUT=204800
ENV MAX_CODE_SIZE=102400

EXPOSE 8406

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8406/healthz')"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8406", "--log-level", "info"]
