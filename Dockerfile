FROM python:3.11-slim

# System dependencies: Tesseract OCR (+ Hindi/Odia packs), poppler for PDF
# uploads, and fonts for the PIL-rendered demo register pages (DejaVu for
# Latin + Lohit Devanagari/Odia for the handwritten Hindi page). Without the
# fonts generate_samples() crashes at boot or renders tofu boxes.
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-hin \
    tesseract-ocr-ori \
    poppler-utils \
    libgl1 \
    fonts-dejavu-core \
    fonts-lohit-deva \
    fonts-lohit-orya \
    && rm -rf /var/lib/apt/lists/* \
    && fc-cache -f >/dev/null 2>&1 || true

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Fresh data dirs — the app seeds its own demo dataset on first boot
RUN rm -rf data && mkdir -p data/uploads data/processed data/geojson data/reports data/samples

# Hugging Face Spaces expects the container to listen on 7860.
# Render (and most PaaS) inject $PORT instead - bind that when present.
EXPOSE 7860

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860}"]
