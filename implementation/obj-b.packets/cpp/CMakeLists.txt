cmake_minimum_required(VERSION 3.16)
project(packet_server LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

# The routing code needs no gRPC, so its self-test builds with a compiler alone.
add_library(packet_routing STATIC src/routing.cpp)
target_include_directories(packet_routing PUBLIC src)

add_executable(routing_selftest src/routing_selftest.cpp)
target_link_libraries(routing_selftest PRIVATE packet_routing)

find_package(Threads REQUIRED)
find_package(Protobuf)
find_package(gRPC CONFIG)
find_program(GRPC_CPP_PLUGIN grpc_cpp_plugin)

if(NOT (Protobuf_FOUND AND gRPC_FOUND AND GRPC_CPP_PLUGIN))
  message(WARNING
    "protobuf or gRPC C++ was not found, so packet_server was skipped. "
    "Set CMAKE_PREFIX_PATH to their install, then configure again.")
  return()
endif()

set(PROTO_DIR ${CMAKE_CURRENT_SOURCE_DIR}/../../obj-a/proto)
set(GENERATED_SOURCES)
foreach(NAME job queue_to_server server_to_queue)
  set(OUTPUTS ${CMAKE_CURRENT_BINARY_DIR}/${NAME}.pb.cc ${CMAKE_CURRENT_BINARY_DIR}/${NAME}.pb.h)
  set(PROTOC_ARGS --cpp_out=${CMAKE_CURRENT_BINARY_DIR})
  if(NOT NAME STREQUAL "job")
    list(APPEND OUTPUTS
      ${CMAKE_CURRENT_BINARY_DIR}/${NAME}.grpc.pb.cc
      ${CMAKE_CURRENT_BINARY_DIR}/${NAME}.grpc.pb.h)
    list(APPEND PROTOC_ARGS
      --grpc_out=${CMAKE_CURRENT_BINARY_DIR}
      --plugin=protoc-gen-grpc=${GRPC_CPP_PLUGIN})
  endif()
  add_custom_command(
    OUTPUT ${OUTPUTS}
    COMMAND ${Protobuf_PROTOC_EXECUTABLE} -I${PROTO_DIR} ${PROTOC_ARGS} ${PROTO_DIR}/${NAME}.proto
    DEPENDS ${PROTO_DIR}/${NAME}.proto
  )
  list(APPEND GENERATED_SOURCES ${OUTPUTS})
endforeach()

add_executable(packet_server src/main.cpp ${GENERATED_SOURCES})
target_include_directories(packet_server PRIVATE ${CMAKE_CURRENT_BINARY_DIR})
target_link_libraries(packet_server PRIVATE
  packet_routing gRPC::grpc++ protobuf::libprotobuf Threads::Threads)
