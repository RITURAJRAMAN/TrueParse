FROM python:3.12-slim

ARG INSTALL_OCR=true
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    XDG_CACHE_HOME=/tmp/trueparse-cache

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv==0.13.5
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src/ ./src/
RUN if [ "$INSTALL_OCR" = "true" ]; then \
      uv sync --frozen --no-dev --no-editable --no-cache --extra api --extra ocr \
      && /app/.venv/bin/python -c "from rapidocr import RapidOCR; RapidOCR()"; \
    else uv sync --frozen --no-dev --no-editable --no-cache --extra api; fi \
    && useradd --uid 10001 --create-home trueparse

USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)" || exit 1
CMD ["python", "-m", "uvicorn", "trueparse.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
