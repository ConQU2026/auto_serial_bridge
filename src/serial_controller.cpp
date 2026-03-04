#include <chrono>
#include <sstream>
#include <iomanip>

#include "auto_serial_bridge/serial_controller.hpp"
#include "auto_serial_bridge/generated_bindings.hpp" // 生成的绑定代码
#include "auto_serial_bridge/generated_config.hpp"   // 生成的配置常数
#include "rclcpp_components/register_node_macro.hpp"

namespace auto_serial_bridge
{
  SerialController::SerialController(const rclcpp::NodeOptions &options)
      : Node("serial_controller", options),
        state_(State::WAITING_HANDSHAKE),
        ctx_(std::make_shared<drivers::common::IoContext>(2)),
        packet_handler_(auto_serial_bridge::config::BUFFER_SIZE)
  {
    RCLCPP_INFO(this->get_logger(), "Initializing SerialController...");

    get_parameters();
    
    // 初始化并存储 Publisher
    auto pubs = std::make_shared<auto_serial_bridge::generated::ProtocolPublishers>();
    pubs->init(this);
    protocol_impl_ = pubs;

    // 注册所有 ROS 订阅者
    auto_serial_bridge::generated::register_all(this);

    // 设备配置
    device_config_ = std::make_unique<drivers::serial_driver::SerialPortConfig>(
        baudrate_,
        drivers::serial_driver::FlowControl::NONE,
        drivers::serial_driver::Parity::NONE,
        drivers::serial_driver::StopBits::ONE);

    // 连接检查定时器
    timer_ = this->create_wall_timer(
        std::chrono::milliseconds(1000),
        std::bind(&SerialController::check_connection, this));
        
    // 心跳/握手定时器 (1Hz)
    heartbeat_timer_ = this->create_wall_timer(
        std::chrono::milliseconds(1000),
        [this]() {
            if (!is_connected_) return;
            
            if (state_ == State::WAITING_HANDSHAKE) {
                // 发送握手请求
                Packet_Handshake pkt;
                pkt.protocol_hash = PROTOCOL_HASH;
                send_packet(PACKET_ID_HANDSHAKE, pkt);
                RCLCPP_INFO_THROTTLE(this->get_logger(), *this->get_clock(), 2000, "协议握手失败或者暂未收到下位机握手 (Hash: 0x%08X)...", PROTOCOL_HASH);
            } else {
                // 发送心跳包
                // Packet_Heartbeat pkt;
                // pkt.count++; 
                // send_packet(PACKET_ID_HEARTBEAT, pkt);
            }
        });
  }

  SerialController::~SerialController()
  {
    reset_serial();
  }

  void SerialController::get_parameters()
  {
    this->declare_parameter<std::string>("port", "/dev/ttyUSB0");
    this->declare_parameter<int>("baudrate", auto_serial_bridge::config::DEFAULT_BAUDRATE);
    this->declare_parameter<double>("timeout", 0.1);

    this->get_parameter("port", port_);
    int baudrate_temp = auto_serial_bridge::config::DEFAULT_BAUDRATE;
    this->get_parameter("baudrate", baudrate_temp);
    baudrate_ = static_cast<uint32_t>(baudrate_temp);
    this->get_parameter("timeout", timeout_);

    RCLCPP_INFO(this->get_logger(), "Port: %s, Baudrate: %u", port_.c_str(), baudrate_);
  }

  bool SerialController::try_open_serial()
  {
    try
    {
      reset_serial();
      driver_ = std::make_unique<drivers::serial_driver::SerialDriver>(*ctx_);
      driver_->init_port(port_, *device_config_);
      driver_->port()->open();
      return driver_->port()->is_open();
    }
    catch (const std::exception &e)
    {
      RCLCPP_ERROR_THROTTLE(this->get_logger(), *this->get_clock(), 2000, "Failed to open serial port '%s': %s", port_.c_str(), e.what());
      return false;
    }
  }

  void SerialController::reset_serial()
  {
    if (driver_)
    {
      if (driver_->port()->is_open())
      {
        driver_->port()->close();
      }
      driver_.reset();
    }
    state_ = State::WAITING_HANDSHAKE;
  }

  void SerialController::check_connection()
  {
    if (is_connected_) return;

    if (try_open_serial())
    {
      RCLCPP_INFO(this->get_logger(), "Serial connected. Waiting for handshake...");
      is_connected_ = true;
      state_ = State::WAITING_HANDSHAKE;
      start_receive();
    }
    else
    {
      // Silent retry or log only occasionally
    }
  }
  
  void SerialController::process_handshake(const Packet& pkt) {
      if (pkt.payload.size() != sizeof(Packet_Handshake)) return;
      
      const Packet_Handshake* data = reinterpret_cast<const Packet_Handshake*>(pkt.payload.data());
      if (data->protocol_hash == PROTOCOL_HASH) {
          state_ = State::RUNNING;
          RCLCPP_INFO(this->get_logger(), "Handshake SUCCESS! Protocol Hash Matched. Entering RUNNING state.");
      } else {
          RCLCPP_INFO(this->get_logger(), "Hash mismatch : Local: 0x%08X, Remote: 0x%08X", PROTOCOL_HASH, data->protocol_hash);
      }
  }

  void SerialController::start_receive()
  {
    if (!is_connected_ || !driver_ || !driver_->port()->is_open()) return;

    driver_->port()->async_receive(
        [this](const std::vector<uint8_t> &buffer, const size_t bytes_read)
        {
          if (bytes_read > 0)
          {
             // [DEBUG] 打印接收到的原始数据
             std::stringstream ss;
             for (size_t i = 0; i < bytes_read; ++i) {
                 ss << std::uppercase << std::hex << std::setw(2) << std::setfill('0') << static_cast<int>(buffer[i]) << " ";
             }
             // Raw frame dump is only for debug-level troubleshooting.
             RCLCPP_DEBUG(this->get_logger(), "RECV HEX: %s", ss.str().c_str());

             {
                 std::lock_guard<std::mutex> lock(rx_mutex_);
                 packet_handler_.feed_data(buffer.data(), bytes_read); 
             }
             
             Packet pkt;
             while (packet_handler_.parse_packet(pkt)) {
                 if (pkt.id == PACKET_ID_HANDSHAKE) {
                     process_handshake(pkt);
                     
                     auto pubs = static_cast<auto_serial_bridge::generated::ProtocolPublishers*>(protocol_impl_.get());
                     auto_serial_bridge::generated::dispatch_packet(*pubs, static_cast<uint8_t>(pkt.id), pkt.payload);
                 } else if (state_ == State::RUNNING) {
                     auto pubs = static_cast<auto_serial_bridge::generated::ProtocolPublishers*>(protocol_impl_.get());
                     auto_serial_bridge::generated::dispatch_packet(*pubs, static_cast<uint8_t>(pkt.id), pkt.payload);
                 } else {
                     // 如果未握手成功，丢弃数据包
                 }
             }
             
             this->start_receive();
          }
          else
          {
            RCLCPP_ERROR(this->get_logger(), "Read error/close.");
            is_connected_ = false;
            reset_serial();
          }
        });
  }

  void SerialController::async_send(const std::vector<uint8_t> &packet_bytes)
  {
    if (!is_connected_ || !driver_ || !driver_->port()->is_open()) return;
    
    // 在 WAITING_HANDSHAKE 状态下, 只允许发送握手数据包
    if (state_ == State::WAITING_HANDSHAKE) {
        // 数据包结构: [HEAD1][HEAD2][ID]...
        // ID 位于第3个字节 (索引 2)
        if (packet_bytes.size() > 2) {
             uint8_t id_byte = packet_bytes[2];
             if (static_cast<PacketID>(id_byte) != PACKET_ID_HANDSHAKE) {
                 return; // 过滤掉非握手包
             }
        }
    }
    
    try
    {
        // [DEBUG] 打印发送的原始数据
        std::stringstream ss;
        for (const auto& byte : packet_bytes) {
            ss << std::uppercase << std::hex << std::setw(2) << std::setfill('0') << static_cast<int>(byte) << " ";
        }
        // Raw frame dump is only for debug-level troubleshooting.
        RCLCPP_DEBUG(this->get_logger(), "SEND HEX: %s", ss.str().c_str());

        driver_->port()->async_send(packet_bytes);
    }
    catch (const std::exception &e)
    {
        RCLCPP_ERROR(this->get_logger(), "Send error: %s", e.what());
        is_connected_ = false;
        reset_serial();
    }
  }

} // namespace auto_serial_bridge

RCLCPP_COMPONENTS_REGISTER_NODE(auto_serial_bridge::SerialController)
