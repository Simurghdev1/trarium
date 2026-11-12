# Trarium — Railway-ready image
# Streamlit + PyMuPDF only. Heavy OCR engines (paddleocr/easyocr/surya) are NOT
# installed on purpose: they need several GB of RAM, which a small Railway
# service does not have. Use OpenRouter Vision OCR or a machine with more RAM.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    STREAMLIT_SERVER_ENABLE_CORS=true \
    STREAMLIT_SERVER_ENABLE_XSRF_PROTECTION=true \
    STREAMLIT_SERVER_MAX_UPLOAD_SIZE=100

WORKDIR /app

# libgl/libglib are only needed if an optional imaging stack is added later;
# they are small and keep the image usable for OCR extras.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY . .

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import os,urllib.request; port=os.environ.get('PORT','8080'); urllib.request.urlopen(f'http://127.0.0.1:{port}/_stcore/health', timeout=4)"

# Railway injects $PORT; Streamlit must bind to 0.0.0.0 and that exact port.
CMD ["sh", "-c", "streamlit run streamlit_app.py --server.address=0.0.0.0 --server.port=${PORT:-8080} --server.headless=true"]
