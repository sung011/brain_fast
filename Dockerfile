FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# ultralytics가 GUI용 opencv-python을 같이 깐다. slim 이미지에는 libxcb가 없어 서버가 바로 죽는다.
RUN pip uninstall -y opencv-python \
    && pip install --no-cache-dir --force-reinstall --no-deps "opencv-python-headless>=4.10.0"

COPY . .

COPY docker/app-entrypoint.sh /usr/local/bin/brain_fast_entrypoint.sh
RUN chmod +x /usr/local/bin/brain_fast_entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/brain_fast_entrypoint.sh"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]