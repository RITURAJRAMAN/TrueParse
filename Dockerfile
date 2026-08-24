# ==========================================
# TrueParse - Production Image
# ==========================================
FROM python:3.11-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

# Set working directory
WORKDIR /app

# Install system dependencies (including optional local Tesseract for scanned PDF OCR)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    libtesseract-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy build configuration and source code
COPY pyproject.toml README.md ./
COPY src/ ./src/

# Install python package and production dependencies
RUN pip install --upgrade pip && \
    pip install .

# Create directory structure for data outputs and temporary intakes
RUN mkdir -p data/output data/intake

# Expose FastAPI application port
EXPOSE 8000

# Health check probe
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Start FastAPI server
CMD ["uvicorn", "trueparse.api.routes:app", "--host", "0.0.0.0", "--port", "8000"]
