cmake_minimum_required(VERSION 3.8)
project(auto_serial_bridge)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(rclcpp_components REQUIRED)
find_package(io_context REQUIRED)
find_package(std_msgs REQUIRED)
find_package(Python3 REQUIRED COMPONENTS Interpreter)

set(PROTOCOL_YAML "${CMAKE_CURRENT_SOURCE_DIR}/config/protocol.yaml")
set(CODEGEN_SCRIPT "${CMAKE_CURRENT_SOURCE_DIR}/scripts/codegen.py")
set(GENERATED_ROOT "${CMAKE_CURRENT_BINARY_DIR}/generated")

set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
  "${PROTOCOL_YAML}"
  "${CODEGEN_SCRIPT}"
  "${CMAKE_CURRENT_SOURCE_DIR}/package.xml"
)

execute_process(
  COMMAND ${Python3_EXECUTABLE} ${CODEGEN_SCRIPT} --print-ros-packages ${PROTOCOL_YAML}
  RESULT_VARIABLE ROS_PACKAGE_RESULT
  OUTPUT_VARIABLE ROS_PACKAGE_OUTPUT
  ERROR_VARIABLE ROS_PACKAGE_ERROR
  OUTPUT_STRIP_TRAILING_WHITESPACE
)
if(NOT ROS_PACKAGE_RESULT EQUAL 0)
  message(FATAL_ERROR "Invalid protocol.yaml:\n${ROS_PACKAGE_OUTPUT}${ROS_PACKAGE_ERROR}")
endif()
string(REPLACE "\n" ";" PROTOCOL_ROS_PACKAGES "${ROS_PACKAGE_OUTPUT}")
list(FILTER PROTOCOL_ROS_PACKAGES EXCLUDE REGEX "^$")
list(REMOVE_DUPLICATES PROTOCOL_ROS_PACKAGES)
foreach(dep IN LISTS PROTOCOL_ROS_PACKAGES)
  find_package(${dep} REQUIRED)
endforeach()

set(GENERATED_PROTOCOL_H "${GENERATED_ROOT}/protocol.h")
set(GENERATED_PROTOCOL_C "${GENERATED_ROOT}/protocol.c")
set(GENERATED_CONFIG_HPP "${GENERATED_ROOT}/generated_config.hpp")
set(GENERATED_BINDINGS_HPP "${GENERATED_ROOT}/generated_bindings.hpp")
set(GENERATED_PROTOCOL_DOC "${GENERATED_ROOT}/PROTOCOL_DOC.md")

add_custom_command(
  OUTPUT
    ${GENERATED_PROTOCOL_H}
    ${GENERATED_PROTOCOL_C}
    ${GENERATED_CONFIG_HPP}
    ${GENERATED_BINDINGS_HPP}
    ${GENERATED_PROTOCOL_DOC}
  COMMAND ${Python3_EXECUTABLE} ${CODEGEN_SCRIPT} ${PROTOCOL_YAML} ${CMAKE_CURRENT_BINARY_DIR}
  DEPENDS ${PROTOCOL_YAML} ${CODEGEN_SCRIPT} ${CMAKE_CURRENT_SOURCE_DIR}/package.xml
  COMMENT "Generating protocol sources from config/protocol.yaml"
  VERBATIM
)
add_custom_target(generate_protocol_code DEPENDS
  ${GENERATED_PROTOCOL_H}
  ${GENERATED_PROTOCOL_C}
  ${GENERATED_CONFIG_HPP}
  ${GENERATED_BINDINGS_HPP}
  ${GENERATED_PROTOCOL_DOC}
)

set(PROJECT_DEPENDENCIES
  rclcpp
  rclcpp_components
  io_context
  std_msgs
  ${PROTOCOL_ROS_PACKAGES}
)
list(REMOVE_DUPLICATES PROJECT_DEPENDENCIES)

add_library(${PROJECT_NAME}_lib SHARED
  src/serial_controller.cpp
  src/serial_controller_bindings.cpp
)
add_dependencies(${PROJECT_NAME}_lib generate_protocol_code)
target_compile_features(${PROJECT_NAME}_lib PUBLIC cxx_std_17)
target_include_directories(${PROJECT_NAME}_lib PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<BUILD_INTERFACE:${CMAKE_CURRENT_BINARY_DIR}/..>
  $<INSTALL_INTERFACE:include>
)
ament_target_dependencies(${PROJECT_NAME}_lib ${PROJECT_DEPENDENCIES})

rclcpp_components_register_nodes(${PROJECT_NAME}_lib
  "auto_serial_bridge::SerialController"
)

add_executable(serial_node src/serial_node.cpp)
add_dependencies(serial_node generate_protocol_code)
target_compile_features(serial_node PUBLIC cxx_std_17)
target_include_directories(serial_node PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<BUILD_INTERFACE:${CMAKE_CURRENT_BINARY_DIR}/..>
  $<INSTALL_INTERFACE:include>
)
ament_target_dependencies(serial_node ${PROJECT_DEPENDENCIES})
target_link_libraries(serial_node ${PROJECT_NAME}_lib)

install(TARGETS ${PROJECT_NAME}_lib
  EXPORT export_${PROJECT_NAME}
  ARCHIVE DESTINATION lib
  LIBRARY DESTINATION lib
  RUNTIME DESTINATION bin
)
install(TARGETS serial_node DESTINATION lib/${PROJECT_NAME})
install(DIRECTORY include/ DESTINATION include
  PATTERN "generated_config.hpp" EXCLUDE
  PATTERN "generated_bindings.hpp" EXCLUDE
  PATTERN "serial_ready_node.hpp" EXCLUDE
)
install(FILES
  ${GENERATED_PROTOCOL_H}
  ${GENERATED_CONFIG_HPP}
  ${GENERATED_BINDINGS_HPP}
  DESTINATION include/${PROJECT_NAME}/generated
)
install(FILES
  ${GENERATED_PROTOCOL_H}
  ${GENERATED_PROTOCOL_C}
  ${GENERATED_PROTOCOL_DOC}
  DESTINATION share/${PROJECT_NAME}/mcu_output
)
install(DIRECTORY launch config web DESTINATION share/${PROJECT_NAME})
install(PROGRAMS scripts/auto_udev.sh DESTINATION lib/${PROJECT_NAME})
install(FILES README.md CHANGELOG.md LICENSE THIRD_PARTY_NOTICES.md
  DESTINATION share/${PROJECT_NAME})

if(BUILD_TESTING)
  find_package(ament_cmake_gtest REQUIRED)
  find_package(ament_cmake_pytest REQUIRED)

  file(GLOB CPP_TEST_FILES "${CMAKE_CURRENT_SOURCE_DIR}/test/test_*.cpp")
  foreach(test_file IN LISTS CPP_TEST_FILES)
    get_filename_component(test_name ${test_file} NAME_WE)
    ament_add_gtest(${test_name} ${test_file})
    if(TARGET ${test_name})
      add_dependencies(${test_name} generate_protocol_code)
      target_include_directories(${test_name} PRIVATE
        ${CMAKE_CURRENT_BINARY_DIR}/..
        ${GENERATED_ROOT}
      )
      target_link_libraries(${test_name} ${PROJECT_NAME}_lib)
      ament_target_dependencies(${test_name} ${PROJECT_DEPENDENCIES})
    endif()
  endforeach()

  ament_add_pytest_test(my_python_tests test/
    ENV "PYTEST_ADDOPTS=--ignore=${CMAKE_CURRENT_SOURCE_DIR}/test/test_checksum_build_matrix.py")
  ament_add_pytest_test(checksum_build_matrix test/test_checksum_build_matrix.py TIMEOUT 600)
  set_tests_properties(checksum_build_matrix PROPERTIES RUN_SERIAL TRUE)
endif()

ament_export_targets(export_${PROJECT_NAME} HAS_LIBRARY_TARGET)
ament_export_include_directories(include)
ament_export_dependencies(${PROJECT_DEPENDENCIES})
ament_package()
