cmake_minimum_required(VERSION 3.8)
project(auto_serial_bridge)

# ==============================================================================
# 1. 编译选项 
# ==============================================================================
if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

# ==============================================================================
# 2. 依赖管理 
# ==============================================================================

set(dependencies
  ament_cmake
  rclcpp
  std_msgs
  rclcpp_components
  serial_driver
  geometry_msgs
)

foreach(dep ${dependencies})
  find_package(${dep} REQUIRED)
endforeach()

# ==============================================================================
# 2.5 代码生成
# ==============================================================================
find_package(Python3 REQUIRED COMPONENTS Interpreter)

# Track protocol config files so CMake re-configures when protocol.yaml appears
# or either file changes, preventing stale fallback selection.
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
  "${CMAKE_CURRENT_SOURCE_DIR}/config/protocol.yaml"
  "${CMAKE_CURRENT_SOURCE_DIR}/config/protocol-sample.yaml"
  "${CMAKE_CURRENT_SOURCE_DIR}/package.xml"
)

set(PROTOCOL_YAML "${CMAKE_CURRENT_SOURCE_DIR}/config/protocol.yaml")
set(PACKAGE_MANIFEST "${CMAKE_CURRENT_SOURCE_DIR}/package.xml")
if(NOT EXISTS "${PROTOCOL_YAML}")
  message(FATAL_ERROR
    "Auto Serial Bridge requires config/protocol.yaml for code generation. "
    "Copy config/protocol-sample.yaml to config/protocol.yaml or let CI create it explicitly before build.")
endif()
set(CODEGEN_SCRIPT "${CMAKE_CURRENT_SOURCE_DIR}/scripts/codegen.py")
set(MCU_HEADER "${CMAKE_CURRENT_SOURCE_DIR}/mcu_output/protocol.h")
set(MCU_SOURCE "${CMAKE_CURRENT_SOURCE_DIR}/mcu_output/protocol.c")
set(ROS_BINDINGS "${CMAKE_CURRENT_SOURCE_DIR}/include/auto_serial_bridge/generated_bindings.hpp")
set(CPP_CONFIG "${CMAKE_CURRENT_SOURCE_DIR}/include/auto_serial_bridge/generated_config.hpp")
set(MCU_DOC "${CMAKE_CURRENT_SOURCE_DIR}/mcu_output/PROTOCOL_DOC.md")

add_custom_command(
  OUTPUT ${MCU_HEADER} ${ROS_BINDINGS} ${MCU_SOURCE} ${CPP_CONFIG} ${MCU_DOC}
  COMMAND ${Python3_EXECUTABLE} ${CODEGEN_SCRIPT} ${PROTOCOL_YAML} ${CMAKE_CURRENT_SOURCE_DIR}
  DEPENDS ${PROTOCOL_YAML} ${CODEGEN_SCRIPT} ${PACKAGE_MANIFEST}
  COMMENT "Generating protocol code and documentation..."
)

add_custom_target(generate_protocol_code DEPENDS ${MCU_HEADER} ${ROS_BINDINGS} ${MCU_SOURCE} ${CPP_CONFIG} ${MCU_DOC})

# ==============================================================================
# 3. 核心库构建
# 编译为库，方便测试和复用
# ==============================================================================
add_library(${PROJECT_NAME}_lib SHARED
  src/serial_controller.cpp
)

add_dependencies(${PROJECT_NAME}_lib generate_protocol_code)

# 配置库的头文件和依赖
target_include_directories(${PROJECT_NAME}_lib PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/mcu_output>
  $<INSTALL_INTERFACE:include>
)
ament_target_dependencies(${PROJECT_NAME}_lib ${dependencies})

# 注册组件
rclcpp_components_register_nodes(${PROJECT_NAME}_lib
  "auto_serial_bridge::SerialController"
)

# ==============================================================================
# 4. 节点构建 
# ==============================================================================
add_executable(serial_node
  src/serial_node.cpp
)


set(targets
  serial_node
)

foreach(target ${targets})
  # 包含头文件
  target_include_directories(${target} PUBLIC
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
    $<INSTALL_INTERFACE:include>)

  # 设置 C++ 标准
  target_compile_features(${target} PUBLIC c_std_99 cxx_std_17)

  # 链接依赖库
  ament_target_dependencies(${target}
    ${dependencies}
  )

  target_link_libraries(${target} ${PROJECT_NAME}_lib)
endforeach()

# ==============================================================================
# 5. 安装规则
# ==============================================================================
install(TARGETS ${PROJECT_NAME}_lib
  ARCHIVE DESTINATION lib
  LIBRARY DESTINATION lib
  RUNTIME DESTINATION bin
)

# 安装可执行文件
install(TARGETS ${targets}
  DESTINATION lib/${PROJECT_NAME}
)

# 安装文件夹
if(IS_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/include)
  install(DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/include/ DESTINATION include/)
endif()

if(IS_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/launch)
  install(DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/launch/ DESTINATION share/${PROJECT_NAME}/launch)
endif()

if(IS_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/config)
  install(DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/config/ DESTINATION share/${PROJECT_NAME}/config)
endif()

if(IS_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/urdf)
  install(DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/urdf/ DESTINATION share/${PROJECT_NAME}/urdf)
endif()


# ==============================================================================
# 6. 测试部分
# ==============================================================================
if(BUILD_TESTING)

  # find_package(ament_lint_auto REQUIRED)
  # set(ament_cmake_copyright_FOUND TRUE)
  # set(ament_cmake_cpplint_FOUND TRUE)
  # ament_lint_auto_find_test_dependencies()

  find_package(ament_cmake_gtest REQUIRED)
  find_package(ament_cmake_pytest REQUIRED)

  # --- B. C++ GTest
  file(GLOB CPP_TEST_FILES "${CMAKE_CURRENT_SOURCE_DIR}/test/test_*.cpp")

  foreach(test_file ${CPP_TEST_FILES})

    get_filename_component(test_name ${test_file} NAME_WE)

    ament_add_gtest(${test_name} ${test_file})

    if(TARGET ${PROJECT_NAME}_lib)
      target_link_libraries(${test_name} ${PROJECT_NAME}_lib)
    endif()

    ament_target_dependencies(${test_name} ${dependencies})

  endforeach()

  # --- C. Python Pytest
  ament_add_pytest_test(
    my_python_tests
    test/
    ENV "PYTEST_ADDOPTS=--ignore=${CMAKE_CURRENT_SOURCE_DIR}/test/test_checksum_build_matrix.py"
  )
  ament_add_pytest_test(
    checksum_build_matrix
    test/test_checksum_build_matrix.py
    TIMEOUT 600
  )
  set_tests_properties(checksum_build_matrix PROPERTIES RUN_SERIAL TRUE)
endif()

# ==============================================================================
# 7. 导出信息 
# ==============================================================================
ament_export_include_directories(include)
ament_export_dependencies(${dependencies})
ament_export_libraries(${PROJECT_NAME}_lib)
ament_package()
