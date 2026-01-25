FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY scripts ./scripts

# create expected folders (volumes will override in compose)
RUN mkdir -p assets data/raw data/processed data/geo outputs

ENTRYPOINT ["python", "-u"]
CMD ["scripts/10_build_map.py"]
