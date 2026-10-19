cmake_minimum_required(VERSION 3.20)
project(llm_admission_control VERSION 0.1.0 LANGUAGES CXX)

# Directory scaffold only. Add implementation targets after engine integration design.
add_library(llm_control INTERFACE)
target_compile_features(llm_control INTERFACE cxx_std_17)
target_include_directories(llm_control INTERFACE "${CMAKE_CURRENT_SOURCE_DIR}/include")
