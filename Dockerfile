FROM python:3.12-slim

# Install Chromium + its driver (Debian bundles a matching pair, avoiding
# version-mismatch headaches with a separately downloaded chromedriver).
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    chromium-driver \
    && rm -rf /var/lib/apt/lists/*

ENV CHROME_BIN=/usr/bin/chromium
ENV CHROMEDRIVER_PATH=/usr/bin/chromedriver
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY cita_checker.py .

CMD ["python", "cita_checker.py"]
