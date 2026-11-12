cmake_minimum_required(VERSION 3.20)
project(queue_server LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

set(OBJA_CPP ${CMAKE_CURRENT_SOURCE_DIR}/../cpp)

add_library(queue_core STATIC
  ${OBJA_CPP}/job.cpp
  ${OBJA_CPP}/events.cpp
  src/buffer.cpp
)
target_include_directories(queue_core PUBLIC
  ${CMAKE_CURRENT_SOURCE_DIR}/src
  ${OBJA_CPP}
)

add_executable(queue_file_selftest src/file_selftest.cpp)
target_link_libraries(queue_file_selftest PRIVATE queue_core)

# Same soft-skip pattern as matchmaking/cpp: file self-test always; gRPC binary
# only when protobuf + gRPC C++ packages are present.
find_package(Protobuf CONFIG QUIET)
find_package(gRPC CONFIG QUIET)
if(Protobuf_FOUND AND gRPC_FOUND AND TARGET gRPC::grpc_cpp_plugin AND COMMAND protobuf_generate)
  set(PROTO_DIR ${CMAKE_CURRENT_SOURCE_DIR}/../proto)
  set(PROTO_FILES
    ${PROTO_DIR}/job.proto
    ${PROTO_DIR}/queue_to_server.proto
    ${PROTO_DIR}/server_to_queue.proto
    ${PROTO_DIR}/queue_to_queue.proto
  )
  set(SERVICE_PROTOS
    ${PROTO_DIR}/queue_to_server.proto
    ${PROTO_DIR}/server_to_queue.proto
    ${PROTO_DIR}/queue_to_queue.proto
  )

  add_library(queue_proto STATIC src/grpc_edge.cpp)
  target_link_libraries(queue_proto PUBLIC queue_core protobuf::libprotobuf gRPC::grpc++)
  target_include_directories(queue_proto PUBLIC
    ${CMAKE_CURRENT_BINARY_DIR}
    ${CMAKE_CURRENT_SOURCE_DIR}/src
    ${OBJA_CPP}
  )

  protobuf_generate(
    TARGET queue_proto
    LANGUAGE cpp
    PROTOS ${PROTO_FILES}
    IMPORT_DIRS ${PROTO_DIR}
    PROTOC_OUT_DIR ${CMAKE_CURRENT_BINARY_DIR}
  )
  protobuf_generate(
    TARGET queue_proto
    LANGUAGE grpc
    GENERATE_EXTENSIONS .grpc.pb.h .grpc.pb.cc
    PLUGIN "protoc-gen-grpc=$<TARGET_FILE:gRPC::grpc_cpp_plugin>"
    PROTOS ${SERVICE_PROTOS}
    IMPORT_DIRS ${PROTO_DIR}
    PROTOC_OUT_DIR ${CMAKE_CURRENT_BINARY_DIR}
  )

  add_executable(queue_server src/main.cpp)
  target_link_libraries(queue_server PRIVATE queue_proto)
else()
  message(WARNING
    "gRPC C++ was not found, so the queue_server target was skipped. "
    "queue_file_selftest still builds. Point CMAKE_PREFIX_PATH at a protobuf "
    "and gRPC install, then reconfigure."
  )
endif()
