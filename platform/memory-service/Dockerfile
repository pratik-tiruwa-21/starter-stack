FROM python:3.12-slim

WORKDIR /app

# Install build deps for numpy/sentence-transformers
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download the embedding model at build time (avoids runtime download)
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

COPY main.py .

EXPOSE 8405

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8405", "--log-level", "info"]
