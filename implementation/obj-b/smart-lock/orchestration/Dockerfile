FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir grpcio
COPY *.py /app/
COPY orchestration/runtime.py /app/orchestration/runtime.py
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app
ENTRYPOINT ["python3", "-u", "/app/orchestration/runtime.py"]
