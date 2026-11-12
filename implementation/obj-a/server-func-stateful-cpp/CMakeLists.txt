cmake_minimum_required(VERSION 3.20)
project(stateful_kv LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

find_package(Protobuf CONFIG QUIET)
if(NOT Protobuf_FOUND)
  find_package(Protobuf REQUIRED)
endif()
find_package(gRPC CONFIG REQUIRED)
find_package(Threads REQUIRED)

set(PROTO_DIR ${CMAKE_CURRENT_SOURCE_DIR}/../proto)
set(GENERATED ${CMAKE_CURRENT_BINARY_DIR}/generated)
file(MAKE_DIRECTORY ${GENERATED})
foreach(NAME job queue_to_server server_to_queue)
  set(OUTPUTS ${GENERATED}/${NAME}.pb.cc ${GENERATED}/${NAME}.pb.h
              ${GENERATED}/${NAME}.grpc.pb.cc ${GENERATED}/${NAME}.grpc.pb.h)
  add_custom_command(
    OUTPUT ${OUTPUTS}
    COMMAND protobuf::protoc
    ARGS --proto_path=${PROTO_DIR} --cpp_out=${GENERATED}
         --grpc_out=${GENERATED}
         --plugin=protoc-gen-grpc=$<TARGET_FILE:gRPC::grpc_cpp_plugin>
         ${PROTO_DIR}/${NAME}.proto
    DEPENDS ${PROTO_DIR}/${NAME}.proto ${PROTO_DIR}/job.proto
    VERBATIM
  )
  list(APPEND PROTO_SOURCES ${OUTPUTS})
endforeach()

add_executable(stateful_server src/server.cpp ${PROTO_SOURCES}
  ../cpp/job.cpp ../cpp/events.cpp)
target_include_directories(stateful_server PRIVATE ${GENERATED} ../cpp src)
target_link_libraries(stateful_server PRIVATE protobuf::libprotobuf gRPC::grpc++ Threads::Threads)
