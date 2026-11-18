
FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir grpcio protobuf

COPY implementation/obj-a /app/obj-a
COPY implementation/obj-b.ticketing /app/obj-b.ticketing

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/obj-a:/app/obj-b.ticketing

CMD ["python", "--version"]
