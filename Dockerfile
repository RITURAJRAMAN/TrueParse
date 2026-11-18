FROM python:3.12-slim AS builder

ARG INSTALL_OCR=true
ENV PATH="/app/.venv/bin:$PATH" \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app
RUN pip install --no-cache-dir uv==0.13.5
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN if [ "$INSTALL_OCR" = "true" ]; then \
      uv sync --frozen --no-dev --no-install-project --extra api --extra ocr; \
    else uv sync --frozen --no-dev --no-install-project --extra api; fi
COPY src/ ./src/
RUN if [ "$INSTALL_OCR" = "true" ]; then \
      uv sync --frozen --no-dev --no-editable --extra api --extra ocr \
      && /app/.venv/bin/python -c "from rapidocr import RapidOCR; RapidOCR()"; \
    else uv sync --frozen --no-dev --no-editable --extra api; fi

FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    XDG_CACHE_HOME=/tmp/trueparse-cache

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home trueparse \
    && mkdir /app/job-data \
    && chown 10001:10001 /app/job-data
COPY --from=builder --chown=10001:10001 /app/.venv /app/.venv

USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)" || exit 1
CMD ["trueparse", "serve", "--host", "0.0.0.0", "--port", "8000"]
