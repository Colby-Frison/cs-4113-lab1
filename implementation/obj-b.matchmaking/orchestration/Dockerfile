# One image for the queue, the stage servers, and the workload generator.
# Build context is implementation/ so both trees are available:
#   docker build -f obj-b.matchmaking/orchestration/Dockerfile -t matchmaking:local .
FROM python:3.12-slim

WORKDIR /app

COPY obj-a/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY obj-a /app/obj-a
COPY obj-b.matchmaking /app/obj-b.matchmaking

ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python", "/app/obj-b.matchmaking/orchestration/entrypoint.py"]
