import os
import yaml
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch_ros.actions import ComposableNodeContainer
from launch_ros.descriptions import ComposableNode

def generate_launch_description():
    
    package_name = 'auto_serial_bridge'
    
    my_pkg_share = get_package_share_directory(package_name)
    
    protocol_path = os.path.join(my_pkg_share, 'config', 'protocol.yaml')
    with open(protocol_path, 'r', encoding='utf-8') as f:
        protocol_config = yaml.safe_load(f)
    node_params = protocol_config['serial_controller']['ros__parameters']
        
    container = ComposableNodeContainer(
            name= package_name + '_container',
            namespace= '',
            package='rclcpp_components',
            executable='component_container', 
            composable_node_descriptions=[
                ComposableNode(
                    package= package_name,
                    plugin='auto_serial_bridge::SerialController',
                    name='serial_controller',
                    parameters=[node_params]
                ),
            ],
            output='screen',
        )
    
    ld = LaunchDescription()
    ld.add_action(container)
    
    return ld
