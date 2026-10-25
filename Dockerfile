FROM python:3.13-slim AS builder

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

COPY app.py .
COPY json_tools.py .
COPY users.json .
COPY static ./static


FROM python:3.13-slim

WORKDIR /app

COPY --from=builder /install /usr/local
COPY --from=builder /app /app

CMD ["python", "app.py"]