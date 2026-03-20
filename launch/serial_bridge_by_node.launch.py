import os
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch_ros.actions import Node

def generate_launch_description():

    package_name = 'auto_serial_bridge'
    
    my_pkg_share = get_package_share_directory(package_name)
    
    common_config = os.path.join(my_pkg_share, 'config', 'protocol.yaml')
        
    serial_node = Node(
        package=package_name,
        executable='serial_node',
        name='serial_controller',
        output='screen',
        emulate_tty=True,
        parameters=[common_config]
    )
    
    ld = LaunchDescription()
    ld.add_action(serial_node)
    
    return ld
