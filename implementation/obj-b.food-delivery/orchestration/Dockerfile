FROM python:3.13-slim-bookworm
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    g++ make cmake libgrpc++-dev libprotobuf-dev protobuf-compiler protobuf-compiler-grpc \
    && rm -rf /var/lib/apt/lists/*
COPY implementation/obj-a/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY implementation/obj-a implementation/obj-a
COPY implementation/obj-b.food-delivery/fd implementation/obj-b.food-delivery/fd
COPY implementation/obj-b.food-delivery/run_demo.py implementation/obj-b.food-delivery/
COPY implementation/obj-b.food-delivery/cpp/*.cpp implementation/obj-b.food-delivery/cpp/*.hpp implementation/obj-b.food-delivery/cpp/CMakeLists.txt implementation/obj-b.food-delivery/cpp/
COPY implementation/obj-b.food-delivery/orchestration/runtime.py implementation/obj-b.food-delivery/orchestration/
RUN cmake -S implementation/obj-b.food-delivery/cpp \
          -B implementation/obj-b.food-delivery/cpp/build -DCMAKE_BUILD_TYPE=Release \
    && cmake --build implementation/obj-b.food-delivery/cpp/build --target food_server -j 2
ENV PYTHONUNBUFFERED=1 LAB_LOG_COMPACT=1
ENTRYPOINT ["python", "/app/implementation/obj-b.food-delivery/orchestration/runtime.py"]
