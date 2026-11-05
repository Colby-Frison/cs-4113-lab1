cmake_minimum_required(VERSION 3.20)
project(food_delivery LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
find_package(Protobuf CONFIG REQUIRED)
find_package(gRPC CONFIG REQUIRED)
set(PROTO_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../../obj-a/proto")
set(GENERATED "${CMAKE_CURRENT_BINARY_DIR}/generated")
file(MAKE_DIRECTORY "${GENERATED}")
set(PROTO_SRCS)
foreach(NAME job queue_to_server server_to_queue)
  list(APPEND PROTO_SRCS "${GENERATED}/${NAME}.pb.cc" "${GENERATED}/${NAME}.grpc.pb.cc")
  add_custom_command(
    OUTPUT "${GENERATED}/${NAME}.pb.cc" "${GENERATED}/${NAME}.pb.h"
           "${GENERATED}/${NAME}.grpc.pb.cc" "${GENERATED}/${NAME}.grpc.pb.h"
    COMMAND protobuf::protoc
    ARGS "--proto_path=${PROTO_DIR}" "--cpp_out=${GENERATED}" "--grpc_out=${GENERATED}"
         "--plugin=protoc-gen-grpc=$<TARGET_FILE:gRPC::grpc_cpp_plugin>" "${PROTO_DIR}/${NAME}.proto"
    DEPENDS "${PROTO_DIR}/${NAME}.proto" "${PROTO_DIR}/job.proto"
    VERBATIM)
endforeach()
add_library(food_proto ${PROTO_SRCS})
target_include_directories(food_proto PUBLIC "${GENERATED}")
target_link_libraries(food_proto PUBLIC protobuf::libprotobuf gRPC::grpc++)
add_executable(food_server server.cpp)
target_link_libraries(food_server PRIVATE food_proto)
target_compile_options(food_server PRIVATE -Wall -Wextra -Wpedantic)
