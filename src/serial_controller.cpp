#include <chrono>
#include <future>

#include "auto_serial_bridge/generated_bindings.hpp"
#include "auto_serial_bridge/generated_config.hpp"
#include "auto_serial_bridge/loopback_utils.hpp"
#include "auto_serial_bridge/serial_controller.hpp"
#include "rclcpp_components/register_node_macro.hpp"

namespace auto_serial_bridge
{

  SerialController::SerialController(const rclcpp::NodeOptions &options)
      : Node("serial_controller", options),
        state_(config::REQUIRE_HANDSHAKE ? State::WAITING_HANDSHAKE : State::RUNNING),
        ctx_(std::make_shared<drivers::common::IoContext>(2)),
        packet_handler_(auto_serial_bridge::config::BUFFER_SIZE),
        enable_heartbeat_(config::ENABLE_HEARTBEAT),
        strict_heartbeat_(config::STRICT_HEARTBEAT),
        heartbeat_timeout_ms_(static_cast<int>(config::HEARTBEAT_TIMEOUT_MS))
  {
    RCLCPP_INFO(this->get_logger(), "Initializing SerialController...");

    serial_strand_ = std::make_unique<asio::io_service::strand>(ctx_->ios());
    reliable_sender_ = std::make_shared<ReliableSender>(
        ctx_->ios(),
        *serial_strand_,
        [this](const std::vector<uint8_t> &packet_bytes)
        {
          const bool sent = async_send_impl(packet_bytes);
          if (sent)
          {
            tx_packet_count_++;
          }
          return sent;
        },
        [this](PacketID id, int max_retries)
        {
          RCLCPP_ERROR(
              this->get_logger(),
              "Reliable send failed for packet id=0x%02X after %d retries",
              static_cast<unsigned int>(id),
              max_retries);
        },
        std::chrono::milliseconds(config::RELIABLE_RETRY_INTERVAL_MS),
        config::RELIABLE_MAX_RETRIES);

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
        [this]()
        {
          post_serial([this]()
                      { handle_heartbeat_timer(); });
        });
  }

  SerialController::~SerialController()
  {
    heartbeat_timer_.reset();
    timer_.reset();

    if (serial_strand_ && !serial_strand_->running_in_this_thread())
    {
      std::promise<void> done;
      auto future = done.get_future();
      serial_strand_->post([this, &done]() mutable
                           {
      reset_serial();
      done.set_value(); });
      future.wait();
      return;
    }

    reset_serial();
  }

  void SerialController::register_loopback_publisher(
      PacketID id,
      const std::shared_ptr<rclcpp::PublisherBase> &publisher)
  {
    std::lock_guard<std::mutex> lock(loopback_publishers_mutex_);
    loopback_publishers_[static_cast<uint8_t>(id)] = publisher;
  }

  bool SerialController::should_skip_loopback(
      PacketID id,
      const rclcpp::MessageInfo &info) const
  {
    std::shared_ptr<rclcpp::PublisherBase> publisher;
    {
      std::lock_guard<std::mutex> lock(loopback_publishers_mutex_);
      const auto it = loopback_publishers_.find(static_cast<uint8_t>(id));
      if (it == loopback_publishers_.end())
      {
        return false;
      }
      publisher = it->second.lock();
    }

    if (!publisher)
    {
      return false;
    }

    const auto &rmw_info = info.get_rmw_message_info();
    return should_skip_loopback_delivery(
        publisher->get_gid(), rmw_info.publisher_gid, rmw_info.from_intra_process);
  }

  void SerialController::post_serial(std::function<void()> task)
  {
    if (!serial_strand_)
    {
      task();
      return;
    }
    serial_strand_->post(std::move(task));
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
        "Port: %s, Baudrate: %u, EnableHeartbeat: %s, StrictHeartbeat: %s, HeartbeatTimeout: %dms",
        port_.c_str(),
        baudrate_,
        enable_heartbeat_ ? "true" : "false",
        strict_heartbeat_ ? "true" : "false",
        heartbeat_timeout_ms_);
    RCLCPP_DEBUG(
        this->get_logger(),
        "Mode selection: handshake=%s, heartbeat=%s (require_handshake=%s, ignore_version_mismatch=%s, enable_heartbeat=%s, strict_heartbeat=%s)",
        detail::handshake_mode_name(
            config::REQUIRE_HANDSHAKE,
            config::IGNORE_VERSION_MISMATCH),
        detail::heartbeat_mode_name(enable_heartbeat_, strict_heartbeat_),
        config::REQUIRE_HANDSHAKE ? "true" : "false",
        config::IGNORE_VERSION_MISMATCH ? "true" : "false",
        enable_heartbeat_ ? "true" : "false",
        strict_heartbeat_ ? "true" : "false");
  }

  bool SerialController::try_open_serial()
  {
    return try_open_serial_impl();
  }

  bool SerialController::try_open_serial_impl()
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
      RCLCPP_ERROR_THROTTLE(
          this->get_logger(), *this->get_clock(), 2000,
          "Failed to open serial port '%s': %s", port_.c_str(), e.what());
      return false;
    }
  }

  void SerialController::reset_serial()
  {
    is_connected_ = false;

    if (reliable_sender_)
    {
      reliable_sender_->clear_all();
    }

    if (driver_)
    {
      const auto port = driver_->port();
      if (port && port->is_open())
      {
        port->close();
      }
      driver_.reset();
    }

    packet_handler_.reset();
    state_ = config::REQUIRE_HANDSHAKE ? State::WAITING_HANDSHAKE : State::RUNNING;
    heartbeat_count_ = 0;
    last_heartbeat_tx_count_ = 0;
    awaiting_heartbeat_ack_ = false;
    heartbeat_ack_received_ = false;
  }

  void SerialController::check_connection()
  {
    post_serial([this]()
                { check_connection_impl(); });
  }

  void SerialController::check_connection_impl()
  {
    if (is_connected_)
    {
      return;
    }

    if (!try_open_serial_impl())
    {
      return;
    }

    is_connected_ = true;
    if constexpr (config::REQUIRE_HANDSHAKE)
    {
      state_ = State::WAITING_HANDSHAKE;
      RCLCPP_INFO(this->get_logger(), "Serial connected. Waiting for handshake...");
    }
    else
    {
      state_ = State::RUNNING;
      RCLCPP_INFO(this->get_logger(), "Serial connected. Handshake disabled, entering RUNNING.");
    }
    start_receive();
  }

  void SerialController::process_handshake(const Packet &pkt)
  {
    if (pkt.payload.size() != sizeof(Packet_Handshake))
    {
      const std::string payload_hex = detail::format_hex_payload(
          pkt.payload.data(), pkt.payload.size());
      RCLCPP_WARN_THROTTLE(
          this->get_logger(), *this->get_clock(), 2000,
          "Received malformed Handshake payload: expected=%zu, got=%zu, local_hash=0x%08X, payload=[%s]",
          sizeof(Packet_Handshake), pkt.payload.size(),
          static_cast<unsigned int>(PROTOCOL_HASH), payload_hex.c_str());
      return;
    }

    const auto *data = reinterpret_cast<const Packet_Handshake *>(pkt.payload.data());
    const std::string hash_pair = detail::format_hash_pair(
        PROTOCOL_HASH,
        data->protocol_hash);
    const auto validation = detail::classify_handshake_validation(
        PROTOCOL_HASH,
        data->protocol_hash,
        config::IGNORE_VERSION_MISMATCH);

    const auto enter_running_state = [this]()
    {
      state_ = State::RUNNING;
      heartbeat_count_ = 0;
      last_heartbeat_tx_count_ = 0;
      awaiting_heartbeat_ack_ = false;
      heartbeat_ack_received_ = false;
      last_heartbeat_ack_time_ = std::chrono::steady_clock::now();
    };

    switch (validation)
    {
    case detail::HandshakeValidationResult::Matched:
      enter_running_state();
      RCLCPP_INFO(
          this->get_logger(),
          "Handshake SUCCESS. %s. Entering RUNNING state.",
          hash_pair.c_str());
      break;
    case detail::HandshakeValidationResult::IgnoredMismatch:
      enter_running_state();
      RCLCPP_WARN(
          this->get_logger(),
          "Handshake hash mismatch ignored by config. %s. Entering RUNNING state.",
          hash_pair.c_str());
      break;
    case detail::HandshakeValidationResult::RejectedMismatch:
      RCLCPP_WARN(
          this->get_logger(),
          "Handshake rejected due to hash mismatch. %s. Keep waiting handshake.",
          hash_pair.c_str());
      break;
    }
  }

  const char *SerialController::state_name() const
  {
    switch (state_)
    {
    case State::WAITING_HANDSHAKE:
      return "WAITING_HANDSHAKE";
    case State::RUNNING:
      return "RUNNING";
    }
    return "UNKNOWN";
  }

  void SerialController::start_receive()
  {
    if (!is_connected_ || !driver_)
    {
      return;
    }

    const auto port = driver_->port();
    if (!port || !port->is_open())
    {
      return;
    }

    port->async_receive(serial_strand_->wrap(
        [this, port](const std::vector<uint8_t> &buffer, const size_t bytes_read)
        {
          handle_receive(port, buffer, bytes_read);
        }));
  }

  size_t SerialController::ingest_received_bytes(const uint8_t *data, size_t len)
  {
    return feed_data_with_recovery(
        packet_handler_, data, len,
        [this](const Packet &pkt)
        {
          handle_packet(pkt);
        });
  }

  void SerialController::handle_receive(
      const std::shared_ptr<drivers::serial_driver::SerialPort> &port,
      const std::vector<uint8_t> &buffer,
      size_t bytes_read)
  {
    if (!driver_ || driver_->port() != port)
    {
      return;
    }

    const auto follow_up = detail::classify_receive_result(bytes_read, port->is_open());
    if (follow_up == detail::ReceiveFollowUpAction::ResetConnection)
    {
      RCLCPP_ERROR(this->get_logger(), "Read error/close.");
      reset_serial();
      return;
    }

    if (bytes_read == 0)
    {
      RCLCPP_DEBUG(this->get_logger(), "Received 0 bytes from serial port, keeping receive loop alive.");
      start_receive();
      return;
    }

    const size_t dropped = ingest_received_bytes(buffer.data(), bytes_read);
    if (dropped > 0)
    {
      RCLCPP_WARN_THROTTLE(
          this->get_logger(), *this->get_clock(), 2000,
          "环形缓冲区溢出，丢弃 %zu 字节 (累计溢出 %u 次)",
          dropped, packet_handler_.overflow_count());
    }

    start_receive();
  }

  void SerialController::handle_packet(const Packet &pkt)
  {
    auto *pubs = protocol_impl_.get();
    if (!pubs)
    {
      return;
    }

    if (pkt.id == PACKET_ID_HEARTBEAT && state_ == State::RUNNING && enable_heartbeat_)
    {
      if (pkt.payload.size() == sizeof(Packet_Heartbeat))
      {
        const auto *data = reinterpret_cast<const Packet_Heartbeat *>(pkt.payload.data());
        if (awaiting_heartbeat_ack_ && data->count == last_heartbeat_tx_count_)
        {
          awaiting_heartbeat_ack_ = false;
          heartbeat_ack_received_ = true;
          last_heartbeat_ack_time_ = std::chrono::steady_clock::now();
          RCLCPP_DEBUG(
              this->get_logger(),
              "Heartbeat ACK matched: count=%u, state=%s",
              data->count,
              state_name());
        }
        else
        {
          const std::string payload_hex = detail::format_hex_payload(
              pkt.payload.data(), pkt.payload.size());
          RCLCPP_WARN_THROTTLE(
              this->get_logger(), *this->get_clock(), 2000,
              "忽略不匹配的心跳确认: expected=%u, got=%u, awaiting=%s, state=%s, payload=[%s]",
              last_heartbeat_tx_count_,
              data->count,
              awaiting_heartbeat_ack_ ? "true" : "false",
              state_name(),
              payload_hex.c_str());
        }
      }
      else
      {
        const std::string payload_hex = detail::format_hex_payload(
            pkt.payload.data(), pkt.payload.size());
        RCLCPP_WARN_THROTTLE(
            this->get_logger(), *this->get_clock(), 2000,
            "Received malformed Heartbeat payload: expected=%zu, got=%zu, state=%s, payload=[%s]",
            sizeof(Packet_Heartbeat),
            pkt.payload.size(),
            state_name(),
            payload_hex.c_str());
      }
    }

    if (pkt.id == PACKET_ID_ACK && reliable_sender_)
    {
      if (pkt.payload.size() == sizeof(Packet_Ack))
      {
        const auto *data = reinterpret_cast<const Packet_Ack *>(pkt.payload.data());
        reliable_sender_->on_ack_received(data->acked_id, data->ack_seq);
      }
      else
      {
        RCLCPP_WARN_THROTTLE(
            this->get_logger(), *this->get_clock(), 2000,
            "Received malformed Ack payload: expected=%zu, got=%zu",
            sizeof(Packet_Ack), pkt.payload.size());
      }
    }

    if constexpr (config::REQUIRE_HANDSHAKE)
    {
      if (pkt.id == PACKET_ID_HANDSHAKE)
      {
        process_handshake(pkt);
        auto_serial_bridge::generated::dispatch_packet(
            *pubs, static_cast<uint8_t>(pkt.id), pkt.payload, this->get_logger());
      }
      else if (state_ == State::RUNNING)
      {
        auto_serial_bridge::generated::dispatch_packet(
            *pubs, static_cast<uint8_t>(pkt.id), pkt.payload, this->get_logger());
      }
      else
      {
        RCLCPP_DEBUG_THROTTLE(
            this->get_logger(), *this->get_clock(), 2000,
            "Dropping packet while waiting handshake: packet_id=0x%02X, payload_len=%zu, state=%s",
            static_cast<unsigned int>(pkt.id),
            pkt.payload.size(),
            state_name());
      }
    }
    else
    {
      if (pkt.id == PACKET_ID_HANDSHAKE)
      {
        process_handshake(pkt);
      }
      auto_serial_bridge::generated::dispatch_packet(
          *pubs, static_cast<uint8_t>(pkt.id), pkt.payload, this->get_logger());
    }
  }

  void SerialController::handle_heartbeat_timer()
  {
    if (!is_connected_)
    {
      return;
    }

    if constexpr (config::REQUIRE_HANDSHAKE)
    {
      if (state_ == State::WAITING_HANDSHAKE)
      {
        Packet_Handshake pkt;
        pkt.protocol_hash = PROTOCOL_HASH;
        send_packet(PACKET_ID_HANDSHAKE, pkt);
        RCLCPP_INFO_THROTTLE(
            this->get_logger(), *this->get_clock(), 2000,
            "等待下位机握手响应，已发送握手探测。上位机协议 hash=0x%08X",
            static_cast<unsigned int>(PROTOCOL_HASH));
        return;
      }
    }

    if (state_ != State::RUNNING || !enable_heartbeat_)
    {
      return;
    }

    const auto now = std::chrono::steady_clock::now();
    if (heartbeat_timeout_ms_ > 0 && awaiting_heartbeat_ack_)
    {
      const auto elapsed = now - heartbeat_ack_wait_started_at_;
      const auto elapsed_ms = std::chrono::duration_cast<std::chrono::milliseconds>(elapsed).count();
      if (elapsed_ms > heartbeat_timeout_ms_)
      {
        if (strict_heartbeat_)
        {
          RCLCPP_WARN(
              this->get_logger(),
              "心跳确认超时 (%ld ms > %d ms)，MCU 可能已断连",
              static_cast<long>(elapsed_ms), heartbeat_timeout_ms_);
          reset_serial();
          return;
        }
        else
        {
          RCLCPP_WARN_THROTTLE(
              this->get_logger(), *this->get_clock(), 5000,
              "心跳确认超时 (%ld ms > %d ms)，非严格模式，继续运行",
              static_cast<long>(elapsed_ms), heartbeat_timeout_ms_);
          // 非严格模式: 重置等待状态，允许发送下一次心跳
          awaiting_heartbeat_ack_ = false;
        }
      }

      // Keep a single outstanding heartbeat so delayed ACKs remain matchable.
      return;
    }

    Packet_Heartbeat hb_pkt;
    hb_pkt.count = heartbeat_count_++;
    last_heartbeat_tx_count_ = hb_pkt.count;
    awaiting_heartbeat_ack_ = true;
    heartbeat_ack_wait_started_at_ = now;
    send_packet(PACKET_ID_HEARTBEAT, hb_pkt);
  }

  void SerialController::async_send(const std::vector<uint8_t> &packet_bytes)
  {
    post_serial([this, packet_bytes]()
                { async_send_impl(packet_bytes); });
  }

  bool SerialController::async_send_impl(const std::vector<uint8_t> &packet_bytes)
  {
    if (!is_connected_ || !driver_)
    {
      return false;
    }

    const auto port = driver_->port();
    if (!port || !port->is_open())
    {
      return false;
    }

    if constexpr (config::REQUIRE_HANDSHAKE)
    {
      if (state_ == State::WAITING_HANDSHAKE && packet_bytes.size() > 2)
      {
        const uint8_t id_byte = packet_bytes[2];
        if (static_cast<PacketID>(id_byte) != PACKET_ID_HANDSHAKE)
        {
          RCLCPP_DEBUG_THROTTLE(
              this->get_logger(), *this->get_clock(), 2000,
              "Blocked TX before handshake: packet_id=0x%02X, state=%s",
              static_cast<unsigned int>(id_byte),
              state_name());
          return false;
        }
      }
    }

    try
    {
      port->async_send(packet_bytes);
    }
    catch (const std::exception &e)
    {
      RCLCPP_ERROR(this->get_logger(), "Send error: %s", e.what());
      reset_serial();
      return false;
    }

    return true;
  }

} // namespace auto_serial_bridge

RCLCPP_COMPONENTS_REGISTER_NODE(auto_serial_bridge::SerialController)
