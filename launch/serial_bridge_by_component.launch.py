import os
import yaml
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import ComposableNodeContainer
from launch_ros.descriptions import ComposableNode


def generate_launch_description():
    package_name = 'auto_serial_bridge'

    pkg_share = get_package_share_directory(package_name)
    protocol_path = os.path.join(pkg_share, 'config', 'protocol.yaml')
    with open(protocol_path, 'r', encoding='utf-8') as f:
        protocol_config = yaml.safe_load(f)
    node_params = protocol_config['serial_controller']['ros__parameters']

    port_arg = DeclareLaunchArgument(
        'port',
        default_value=str(node_params.get('port', '/dev/ttyACM0')),
    )
    baudrate_arg = DeclareLaunchArgument(
        'baudrate',
        default_value=str(node_params.get('baudrate', 115200)),
    )
    log_level_arg = DeclareLaunchArgument('log_level', default_value='info')
    node_params['port'] = LaunchConfiguration('port')
    node_params['baudrate'] = LaunchConfiguration('baudrate')

    container = ComposableNodeContainer(
        name=package_name + '_container',
        namespace='',
        package='rclcpp_components',
        executable='component_container',
        composable_node_descriptions=[
            ComposableNode(
                package=package_name,
                plugin='auto_serial_bridge::SerialController',
                name='serial_controller',
                parameters=[node_params],
            ),
        ],
        output='screen',
        # 只调整本容器内节点的日志级别，不影响 rcl/rmw
        arguments=[
            '--ros-args', '--log-level',
            ['serial_controller:=', LaunchConfiguration('log_level')],
        ],
    )

    ld = LaunchDescription()
    ld.add_action(port_arg)
    ld.add_action(baudrate_arg)
    ld.add_action(log_level_arg)
    ld.add_action(container)
    return ld
