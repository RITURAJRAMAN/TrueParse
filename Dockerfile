# ==========================================
# TrueParse - Production Image
# ==========================================
FROM python:3.11-slim

# Build with --build-arg INSTALL_OCR=false for a slimmer image without OCR.
ARG INSTALL_OCR=true

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000 \
    TRUEPARSE_OUTPUT_ROOT=/app/data/output

WORKDIR /app

# libgl/libglib are needed by opencv, which the OCR backend depends on.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN pip install --upgrade pip && \
    if [ "$INSTALL_OCR" = "true" ]; then pip install ".[ocr]"; else pip install .; fi

RUN mkdir -p data/output data/intake

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "trueparse.api.routes:app", "--host", "0.0.0.0", "--port", "8000"]
