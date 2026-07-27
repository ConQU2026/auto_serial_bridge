// 独立编译单元：包含所有 ROS2 消息类型头文件，与 serial_controller.cpp 并行编译
#include "auto_serial_bridge/generated/generated_bindings.hpp"
#include "auto_serial_bridge/serial_controller.hpp"

namespace auto_serial_bridge
{

void SerialController::init_protocol_bindings()
{
  auto pubs = std::make_shared<generated::ProtocolPublishers>();
  pubs->init(this);
  protocol_impl_ = pubs;
  generated::register_all(this);
}

void SerialController::dispatch_to_protocol(uint8_t id, const std::vector<uint8_t> &payload)
{
  auto *pubs = static_cast<generated::ProtocolPublishers *>(protocol_impl_.get());
  if (!pubs) return;
  generated::dispatch_packet(*pubs, id, payload, this->get_logger());
}

std::string SerialController::describe_protocol_packet(
    PacketID id,
    const std::vector<uint8_t> &payload) const
{
  return generated::describe_packet(id, payload);
}

} // namespace auto_serial_bridge
