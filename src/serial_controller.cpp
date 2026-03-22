#include <chrono>
#include <sstream>
#include <iomanip>

#include "auto_serial_bridge/serial_controller.hpp"
#include "auto_serial_bridge/loopback_utils.hpp"
#include "auto_serial_bridge/generated_bindings.hpp"
#include "auto_serial_bridge/generated_config.hpp"
#include "rclcpp_components/register_node_macro.hpp"

namespace auto_serial_bridge
{
  SerialController::SerialController(const rclcpp::NodeOptions &options)
      : Node("serial_controller", options),
        state_(config::REQUIRE_HANDSHAKE ? State::WAITING_HANDSHAKE : State::RUNNING),
        ctx_(std::make_shared<drivers::common::IoContext>(2)),
        packet_handler_(auto_serial_bridge::config::BUFFER_SIZE),
        enable_heartbeat_(config::ENABLE_HEARTBEAT),
        heartbeat_timeout_ms_(static_cast<int>(config::HEARTBEAT_TIMEOUT_MS))
  {
    RCLCPP_INFO(this->get_logger(), "Initializing SerialController...");

    get_parameters();
    
    auto pubs = std::make_shared<auto_serial_bridge::generated::ProtocolPublishers>();
    pubs->init(this);
    protocol_impl_ = pubs;

    auto_serial_bridge::generated::register_all(this);

    device_config_ = std::make_unique<drivers::serial_driver::SerialPortConfig>(
        baudrate_,
        drivers::serial_driver::FlowControl::NONE,
        drivers::serial_driver::Parity::NONE,
        drivers::serial_driver::StopBits::ONE);

    timer_ = this->create_wall_timer(
        std::chrono::milliseconds(1000),
        std::bind(&SerialController::check_connection, this));
        
    heartbeat_timer_ = this->create_wall_timer(
        std::chrono::milliseconds(1000),
        [this]() {
            if (!is_connected_) return;
            
            if constexpr (config::REQUIRE_HANDSHAKE) {
                if (state_ == State::WAITING_HANDSHAKE) {
                    Packet_Handshake pkt;
                    pkt.protocol_hash = PROTOCOL_HASH;
                    send_packet(PACKET_ID_HANDSHAKE, pkt);
                    RCLCPP_INFO_THROTTLE(this->get_logger(), *this->get_clock(), 2000,
                        "协议握手失败或者暂未收到下位机握手 (Hash: 0x%08X)...", PROTOCOL_HASH);
                    return;
                }
            }

            if (state_ == State::RUNNING) {
                if (!enable_heartbeat_) {
                    return;
                }

                auto now = std::chrono::steady_clock::now();

                if (heartbeat_timeout_ms_ > 0 && awaiting_heartbeat_ack_) {
                    auto elapsed = now - heartbeat_ack_wait_started_at_;
                    auto elapsed_ms = std::chrono::duration_cast<std::chrono::milliseconds>(elapsed).count();
                    if (elapsed_ms > heartbeat_timeout_ms_) {
                        RCLCPP_WARN(this->get_logger(),
                            "心跳确认超时 (%ld ms > %d ms)，MCU 可能已断连",
                            static_cast<long>(elapsed_ms), heartbeat_timeout_ms_);
                        is_connected_ = false;
                        reset_serial();
                        return;
                    }
                }

                Packet_Heartbeat hb_pkt;
                hb_pkt.count = heartbeat_count_++;
                last_heartbeat_tx_count_ = hb_pkt.count;
                if (!awaiting_heartbeat_ack_) {
                    awaiting_heartbeat_ack_ = true;
                    heartbeat_ack_wait_started_at_ = now;
                }
                send_packet(PACKET_ID_HEARTBEAT, hb_pkt);
            }
        });
  }

  SerialController::~SerialController()
  {
    reset_serial();
  }

  void SerialController::register_loopback_publisher(
      PacketID id,
      const std::shared_ptr<rclcpp::PublisherBase>& publisher)
  {
    std::lock_guard<std::mutex> lock(loopback_publishers_mutex_);
    loopback_publishers_[static_cast<uint8_t>(id)] = publisher;
  }

  bool SerialController::should_skip_loopback(
      PacketID id,
      const rclcpp::MessageInfo& info) const
  {
    std::shared_ptr<rclcpp::PublisherBase> publisher;
    {
      std::lock_guard<std::mutex> lock(loopback_publishers_mutex_);
      const auto it = loopback_publishers_.find(static_cast<uint8_t>(id));
      if (it == loopback_publishers_.end()) {
        return false;
      }
      publisher = it->second.lock();
    }

    if (!publisher) {
      return false;
    }

    const auto & rmw_info = info.get_rmw_message_info();
    return should_skip_loopback_delivery(
      publisher->get_gid(), rmw_info.publisher_gid, rmw_info.from_intra_process);
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

    RCLCPP_INFO(
        this->get_logger(),
        "Port: %s, Baudrate: %u, EnableHeartbeat: %s, HeartbeatTimeout: %dms",
        port_.c_str(),
        baudrate_,
        enable_heartbeat_ ? "true" : "false",
        heartbeat_timeout_ms_);
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
    {
      std::lock_guard<std::mutex> lock(rx_mutex_);
      packet_handler_.reset();
    }
    state_ = config::REQUIRE_HANDSHAKE ? State::WAITING_HANDSHAKE : State::RUNNING;
    heartbeat_count_ = 0;
    last_heartbeat_tx_count_ = 0;
    awaiting_heartbeat_ack_ = false;
    heartbeat_ack_received_ = false;
  }

  void SerialController::check_connection()
  {
    if (is_connected_) return;

    if (try_open_serial())
    {
      is_connected_ = true;
      if constexpr (config::REQUIRE_HANDSHAKE) {
        state_ = State::WAITING_HANDSHAKE;
        RCLCPP_INFO(this->get_logger(), "Serial connected. Waiting for handshake...");
      } else {
        state_ = State::RUNNING;
        RCLCPP_INFO(this->get_logger(), "Serial connected. Handshake disabled, entering RUNNING.");
      }
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
          heartbeat_count_ = 0;
          last_heartbeat_tx_count_ = 0;
          awaiting_heartbeat_ack_ = false;
          heartbeat_ack_received_ = false;
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
             std::stringstream ss;
             for (size_t i = 0; i < bytes_read; ++i) {
                 ss << std::uppercase << std::hex << std::setw(2) << std::setfill('0') << static_cast<int>(buffer[i]) << " ";
             }
             RCLCPP_DEBUG(this->get_logger(), "RECV HEX: %s", ss.str().c_str());

             std::lock_guard<std::mutex> lock(rx_mutex_);

             size_t dropped = packet_handler_.feed_data(buffer.data(), bytes_read);
             if (dropped > 0) {
                 RCLCPP_WARN_THROTTLE(this->get_logger(), *this->get_clock(), 2000,
                     "环形缓冲区溢出，丢弃 %zu 字节 (累计溢出 %u 次)",
                     dropped, packet_handler_.overflow_count());
             }
             
             Packet pkt;
             while (packet_handler_.parse_packet(pkt)) {
                 auto* pubs = protocol_impl_.get();

                 if (pkt.id == PACKET_ID_HEARTBEAT && state_ == State::RUNNING) {
                     if (!enable_heartbeat_) {
                         continue;
                     }
                     if (pkt.payload.size() == sizeof(Packet_Heartbeat)) {
                         const auto* data = reinterpret_cast<const Packet_Heartbeat*>(pkt.payload.data());
                         if (awaiting_heartbeat_ack_ && data->count == last_heartbeat_tx_count_) {
                             awaiting_heartbeat_ack_ = false;
                             heartbeat_ack_received_ = true;
                             last_heartbeat_ack_time_ = std::chrono::steady_clock::now();
                         } else {
                             RCLCPP_WARN_THROTTLE(
                                 this->get_logger(), *this->get_clock(), 2000,
                                 "忽略不匹配的心跳确认: expected=%u, got=%u",
                                 last_heartbeat_tx_count_, data->count);
                         }
                     }
                 }

                 if constexpr (config::REQUIRE_HANDSHAKE) {
                     if (pkt.id == PACKET_ID_HANDSHAKE) {
                         process_handshake(pkt);
                         auto_serial_bridge::generated::dispatch_packet(*pubs, static_cast<uint8_t>(pkt.id), pkt.payload);
                     } else if (state_ == State::RUNNING) {
                         auto_serial_bridge::generated::dispatch_packet(*pubs, static_cast<uint8_t>(pkt.id), pkt.payload);
                     }
                 } else {
                     if (pkt.id == PACKET_ID_HANDSHAKE) {
                         process_handshake(pkt);
                     }
                     auto_serial_bridge::generated::dispatch_packet(*pubs, static_cast<uint8_t>(pkt.id), pkt.payload);
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
    
    if constexpr (config::REQUIRE_HANDSHAKE) {
        if (state_ == State::WAITING_HANDSHAKE) {
            if (packet_bytes.size() > 2) {
                 uint8_t id_byte = packet_bytes[2];
                 if (static_cast<PacketID>(id_byte) != PACKET_ID_HANDSHAKE) {
                     return;
                 }
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
