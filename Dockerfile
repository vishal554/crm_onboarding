FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# System deps: tesseract for OCR, poppler-utils for pdf2image (used from step 5).
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        poppler-utils \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# spaCy English model for applicant-field extraction from free-form bodies.
RUN python -m spacy download en_core_web_sm

COPY . .

COPY docker/entrypoint.sh /entrypoint.sh
# Normalise line endings (file may be authored on Windows) and make executable.
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["web"]
