cmake_minimum_required(VERSION 3.16)
project(smart_lock_server LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(Protobuf REQUIRED)
find_package(gRPC CONFIG REQUIRED)

set(PROTO_FILE ${CMAKE_CURRENT_SOURCE_DIR}/../proto/smart_lock.proto)

add_custom_command(OUTPUT smart_lock.pb.cc smart_lock.pb.h smart_lock.grpc.pb.cc smart_lock.grpc.pb.h
 COMMAND protobuf::protoc --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/../proto
 --cpp_out=${CMAKE_CURRENT_BINARY_DIR} --grpc_out=${CMAKE_CURRENT_BINARY_DIR}
 --plugin=protoc-gen-grpc=$<TARGET_FILE:gRPC::grpc_cpp_plugin> ${PROTO_FILE}
 DEPENDS ${PROTO_FILE})

add_executable(smart-lock-server main.cpp server_function.cpp ${CMAKE_CURRENT_BINARY_DIR}/smart_lock.pb.cc ${CMAKE_CURRENT_BINARY_DIR}/smart_lock.grpc.pb.cc)

target_include_directories(smart-lock-server PRIVATE ${CMAKE_CURRENT_BINARY_DIR})
target_link_libraries(smart-lock-server PRIVATE gRPC::grpc++ protobuf::libprotobuf)
