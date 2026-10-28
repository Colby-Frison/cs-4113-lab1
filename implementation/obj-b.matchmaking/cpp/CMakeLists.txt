cmake_minimum_required(VERSION 3.20)
project(match_server LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_library(match_core STATIC
  src/ticket.cpp
  src/events.cpp
  src/visit.cpp
)
target_include_directories(match_core PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/src)

add_executable(match_file_selftest src/file_selftest.cpp)
target_link_libraries(match_file_selftest PRIVATE match_core)

find_package(Protobuf CONFIG QUIET)
find_package(gRPC CONFIG QUIET)
if(Protobuf_FOUND AND gRPC_FOUND AND TARGET gRPC::grpc_cpp_plugin AND COMMAND protobuf_generate)
  set(PROTO_DIR ${CMAKE_CURRENT_SOURCE_DIR}/../../obj-a/proto)
  set(PROTO_FILES
    ${PROTO_DIR}/job.proto
    ${PROTO_DIR}/queue_to_server.proto
    ${PROTO_DIR}/server_to_queue.proto
  )
  set(SERVICE_PROTOS
    ${PROTO_DIR}/queue_to_server.proto
    ${PROTO_DIR}/server_to_queue.proto
  )

  add_library(match_proto STATIC src/grpc_edge.cpp)
  target_link_libraries(match_proto PUBLIC match_core protobuf::libprotobuf gRPC::grpc++)
  target_include_directories(match_proto PUBLIC
    ${CMAKE_CURRENT_BINARY_DIR}
    ${CMAKE_CURRENT_SOURCE_DIR}/src
  )

  protobuf_generate(
    TARGET match_proto
    LANGUAGE cpp
    PROTOS ${PROTO_FILES}
    IMPORT_DIRS ${PROTO_DIR}
    PROTOC_OUT_DIR ${CMAKE_CURRENT_BINARY_DIR}
  )
  protobuf_generate(
    TARGET match_proto
    LANGUAGE grpc
    GENERATE_EXTENSIONS .grpc.pb.h .grpc.pb.cc
    PLUGIN "protoc-gen-grpc=$<TARGET_FILE:gRPC::grpc_cpp_plugin>"
    PROTOS ${SERVICE_PROTOS}
    IMPORT_DIRS ${PROTO_DIR}
    PROTOC_OUT_DIR ${CMAKE_CURRENT_BINARY_DIR}
  )

  add_executable(match_server src/main.cpp)
  target_link_libraries(match_server PRIVATE match_proto)
else()
  message(WARNING
    "gRPC C++ was not found, so the match_server target was skipped. "
    "match_file_selftest still builds. Point CMAKE_PREFIX_PATH at a protobuf "
    "and gRPC install, then reconfigure."
  )
endif()
