#include <chrono>
#include <future>
#include <sstream>

#include "auto_serial_bridge/generated/generated_config.hpp"
#include "auto_serial_bridge/serial_controller.hpp"
#include "rclcpp_components/register_node_macro.hpp"

namespace auto_serial_bridge
{
  namespace
  {
    constexpr auto kConnectionCheckPeriod = std::chrono::seconds(1);
  } // namespace


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

    auto ready_qos = rclcpp::QoS(rclcpp::KeepLast(1));
    ready_qos.reliable().transient_local();
    ready_publisher_ = this->create_publisher<std_msgs::msg::Bool>(
        "auto_serial_bridge/ready", ready_qos);
    publish_ready(false);

    init_protocol_bindings();

    timer_ = this->create_wall_timer(
        kConnectionCheckPeriod,
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
                             if (pending_serial_ops_ == 0)
                             {
                               done.set_value();
                               return;
                             }
                             pending_serial_drain_callback_ = [&done]() mutable
                             {
                               done.set_value();
                             };
                           });
      future.wait();
      return;
    }

    reset_serial();
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
    this->declare_parameter<std::string>("port", auto_serial_bridge::config::DEFAULT_PORT);
    this->declare_parameter<int>("baudrate", auto_serial_bridge::config::DEFAULT_BAUDRATE);
    this->declare_parameter<bool>("debug_raw_frame", false);

    this->get_parameter("port", port_);
    int baudrate_temp = auto_serial_bridge::config::DEFAULT_BAUDRATE;
    this->get_parameter("baudrate", baudrate_temp);
    baudrate_ = static_cast<uint32_t>(baudrate_temp);
    this->get_parameter("debug_raw_frame", debug_raw_frame_);

    RCLCPP_INFO(
        this->get_logger(),
        "Port: %s, Baudrate: %u, DebugRawFrame: %s, EnableHeartbeat: %s, StrictHeartbeat: %s, HeartbeatTimeout: %dms",
        port_.c_str(),
        baudrate_,
        debug_raw_frame_ ? "true" : "false",
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

  void SerialController::publish_ready(bool ready)
  {
    if (ready_published_ && ready_state_ == ready)
    {
      return;
    }
    std_msgs::msg::Bool message;
    message.data = ready;
    ready_publisher_->publish(message);
    ready_state_ = ready;
    ready_published_ = true;
  }

  bool SerialController::try_open_serial_impl()
  {
    try
    {
      reset_serial();

      auto port = std::make_shared<asio::serial_port>(ctx_->ios());
      port->open(port_);
      port->set_option(asio::serial_port_base::baud_rate(baudrate_));
      port->set_option(asio::serial_port_base::flow_control(
          asio::serial_port_base::flow_control::none));
      port->set_option(asio::serial_port_base::parity(
          asio::serial_port_base::parity::none));
      port->set_option(asio::serial_port_base::stop_bits(
          asio::serial_port_base::stop_bits::one));

      serial_port_ = port;
      return serial_port_->is_open();
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
    publish_ready(false);
    is_connected_ = false;
    ++connection_generation_;
    shutdown_requested_ = true;

    if (reliable_sender_)
    {
      reliable_sender_->clear_all();
    }

    tx_queue_.clear();
    tx_write_in_progress_ = false;

    if (serial_port_)
    {
      asio::error_code ignored;
      if (serial_port_->is_open())
      {
        serial_port_->cancel(ignored);
        serial_port_->close(ignored);
      }
      serial_port_.reset();
    }

    packet_handler_.reset();
    state_ = config::REQUIRE_HANDSHAKE ? State::WAITING_HANDSHAKE : State::RUNNING;
    heartbeat_count_ = 0;
    last_heartbeat_tx_count_ = 0;
    awaiting_heartbeat_ack_ = false;

    if (pending_serial_ops_ == 0 && pending_serial_drain_callback_)
    {
      notify_serial_ops_drained();
    }
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
      if (!serial_port_ || !serial_port_->is_open())
      {
        RCLCPP_WARN(this->get_logger(), "串口意外断开，尝试重连...");
        reset_serial();
      }
      return;
    }

    if (!try_open_serial_impl())
    {
      return;
    }

    is_connected_ = true;
    shutdown_requested_ = false;
    if constexpr (config::REQUIRE_HANDSHAKE)
    {
      state_ = State::WAITING_HANDSHAKE;
      RCLCPP_INFO(this->get_logger(), "Serial connected. Waiting for handshake...");
    }
    else
    {
      state_ = State::RUNNING;
      RCLCPP_INFO(this->get_logger(), "Serial connected. Handshake disabled, entering RUNNING.");
      publish_ready(true);
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
      publish_ready(true);
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
    if (!is_connected_ || !serial_port_)
    {
      return;
    }

    const auto port = serial_port_;
    if (!port->is_open())
    {
      return;
    }

    const auto generation = connection_generation_;
    ++pending_serial_ops_;
    try
    {
      port->async_read_some(
          asio::buffer(rx_buffer_),
          serial_strand_->wrap(
              [this, port, generation](
                  const asio::error_code &error,
                  const size_t bytes_read)
              {
                handle_receive(port, generation, error, bytes_read);
              }));
    }
    catch (const std::exception &e)
    {
      complete_serial_op();
      RCLCPP_ERROR(
          this->get_logger(),
          "Failed to start serial read on '%s': %s",
          port_.c_str(),
          e.what());
      reset_serial();
    }
  }

  void SerialController::complete_serial_op()
  {
    if (pending_serial_ops_ > 0)
    {
      --pending_serial_ops_;
    }

    if (shutdown_requested_ && pending_serial_ops_ == 0 && pending_serial_drain_callback_)
    {
      notify_serial_ops_drained();
    }
  }

  void SerialController::notify_serial_ops_drained()
  {
    if (!pending_serial_drain_callback_)
    {
      return;
    }

    auto callback = std::move(pending_serial_drain_callback_);
    pending_serial_drain_callback_ = nullptr;

    if (serial_strand_ && serial_strand_->running_in_this_thread())
    {
      serial_strand_->post(std::move(callback));
      return;
    }

    callback();
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
      const std::shared_ptr<asio::serial_port> &port,
      uint64_t generation,
      const asio::error_code &error,
      size_t bytes_read)
  {
    complete_serial_op();

    if (generation != connection_generation_ || serial_port_ != port)
    {
      return;
    }

    if (error)
    {
      if (error == asio::error::operation_aborted)
      {
        return;
      }

      RCLCPP_ERROR(
          this->get_logger(),
          "Serial read error on '%s': %s",
          port_.c_str(),
          error.message().c_str());
      reset_serial();
      return;
    }

    const auto receive_action = detail::classify_receive_result(
        bytes_read,
        port && port->is_open());
    if (receive_action == detail::ReceiveFollowUpAction::ResetConnection)
    {
      RCLCPP_WARN(this->get_logger(), "串口意外断开，尝试重连...");
      reset_serial();
      return;
    }

    if (bytes_read == 0)
    {
      RCLCPP_DEBUG(this->get_logger(), "Received 0 bytes from serial port.");
      start_receive();
      return;
    }

    const size_t dropped = ingest_received_bytes(rx_buffer_.data(), bytes_read);
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
    if (!protocol_impl_)
    {
      return;
    }

    std::vector<uint8_t> dispatch_payload = pkt.payload;
    if (config::is_reliable_packet(pkt.id) && !dispatch_payload.empty())
    {
      dispatch_payload.pop_back();
    }

    if (pkt.id == PACKET_ID_HEARTBEAT && state_ == State::RUNNING && enable_heartbeat_)
    {
      if (pkt.payload.size() == sizeof(Packet_Heartbeat))
      {
        const auto *data = reinterpret_cast<const Packet_Heartbeat *>(pkt.payload.data());
        if (data->count == last_heartbeat_tx_count_)
        {
          // 正常 ACK 或迟到的 ACK（非严格模式超时重置后才回传），都视为有效
          awaiting_heartbeat_ack_ = false;
          if (should_log_protocol_debug(PACKET_ID_HEARTBEAT))
          {
            RCLCPP_DEBUG(
                this->get_logger(),
                "Heartbeat ACK matched: count=%u, state=%s",
                data->count,
                state_name());
          }
        }
        else
        {
          const std::string payload_hex = detail::format_hex_payload(
              pkt.payload.data(), pkt.payload.size());
          RCLCPP_WARN_THROTTLE(
              this->get_logger(), *this->get_clock(), 2000,
              "心跳计数不匹配: expected=%u, got=%u, state=%s, payload=[%s]",
              last_heartbeat_tx_count_,
              data->count,
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
        if (state_ == State::WAITING_HANDSHAKE)
        {
          process_handshake(pkt);
        }
        dispatch_to_protocol(static_cast<uint8_t>(pkt.id), dispatch_payload);
      }
      else if (state_ == State::RUNNING)
      {
        dispatch_to_protocol(static_cast<uint8_t>(pkt.id), dispatch_payload);
      }
      else
      {
        if (should_log_protocol_debug(pkt.id))
        {
          RCLCPP_DEBUG_THROTTLE(
              this->get_logger(), *this->get_clock(), 2000,
              "Dropping packet while waiting handshake: packet_id=0x%02X, payload_len=%zu, state=%s",
              static_cast<unsigned int>(pkt.id),
              pkt.payload.size(),
              state_name());
        }
      }
    }
    else
    {
      if (pkt.id == PACKET_ID_HANDSHAKE && state_ != State::RUNNING)
      {
        process_handshake(pkt);
      }
      dispatch_to_protocol(static_cast<uint8_t>(pkt.id), dispatch_payload);
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

  void SerialController::async_send(std::vector<uint8_t> packet_bytes)
  {
    post_serial([this, packet_bytes = std::move(packet_bytes)]() mutable
                {
                  if (async_send_impl(std::move(packet_bytes)))
                  {
                    tx_packet_count_++;
                  }
                });
  }

  bool SerialController::should_log_protocol_debug(PacketID id) const
  {
    return config::is_debug_log_enabled(id);
  }

  bool SerialController::should_log_mcu_tx_raw(PacketID id) const
  {
    return detail::should_log_raw_tx_frame(debug_raw_frame_, should_log_protocol_debug(id));
  }

  void SerialController::log_mcu_tx(const std::vector<uint8_t> &packet_bytes) const
  {
    const PacketID id = packet_bytes.size() > 2
                            ? static_cast<PacketID>(packet_bytes[2])
                            : static_cast<PacketID>(0);
    const bool debug_log_enabled = should_log_protocol_debug(id);
    const bool log_raw_frame = should_log_mcu_tx_raw(id);
    if (packet_bytes.size() < 5)
    {
      if (log_raw_frame)
      {
        RCLCPP_DEBUG(
            this->get_logger(),
            "[MCU TX RAW] %s",
            detail::format_hex_payload(
                packet_bytes.data(), packet_bytes.size(), packet_bytes.size())
                .c_str());
      }
      return;
    }

    const uint8_t payload_len = packet_bytes[3];
    const size_t payload_offset = 4;
    const size_t checksum_size = 1;
    const size_t expected_size = payload_offset + payload_len + checksum_size;

    if (log_raw_frame)
    {
      RCLCPP_DEBUG(
          this->get_logger(),
          "[MCU TX RAW] %s",
          detail::format_hex_payload(
              packet_bytes.data(), packet_bytes.size(), packet_bytes.size())
              .c_str());
    }

    if (!debug_log_enabled)
    {
      return;
    }

    if (packet_bytes.size() != expected_size)
    {
      RCLCPP_DEBUG(
          this->get_logger(),
          "[MCU TX DECODED] packet_id=0x%02X, payload_len=%u, frame_size=%zu, expected_size=%zu",
          static_cast<unsigned int>(static_cast<uint8_t>(id)),
          static_cast<unsigned int>(payload_len),
          packet_bytes.size(),
          expected_size);
      return;
    }

    std::vector<uint8_t> payload(
        packet_bytes.begin() + payload_offset,
        packet_bytes.begin() + payload_offset + payload_len);
    RCLCPP_DEBUG(
        this->get_logger(),
        "[MCU TX DECODED] id=0x%02X, len=%u, checksum=0x%02X, %s",
        static_cast<unsigned int>(static_cast<uint8_t>(id)),
        static_cast<unsigned int>(payload_len),
        static_cast<unsigned int>(packet_bytes.back()),
        describe_protocol_packet(id, payload).c_str());
  }

  bool SerialController::async_send_impl(std::vector<uint8_t> packet_bytes)
  {
    if (!is_connected_ || !serial_port_)
    {
      return false;
    }

    if (!serial_port_->is_open())
    {
      return false;
    }

    if constexpr (config::REQUIRE_HANDSHAKE)
    {
      if (state_ == State::WAITING_HANDSHAKE && packet_bytes.size() > 2)
      {
        const uint8_t id_byte = packet_bytes[2];
        const auto id = static_cast<PacketID>(id_byte);
        if (id != PACKET_ID_HANDSHAKE)
        {
          if (should_log_protocol_debug(id))
          {
            RCLCPP_DEBUG_THROTTLE(
                this->get_logger(), *this->get_clock(), 2000,
                "Blocked TX before handshake: packet_id=0x%02X, state=%s",
                static_cast<unsigned int>(id_byte),
                state_name());
          }
          return false;
        }
      }
    }

    try
    {
      log_mcu_tx(packet_bytes);
      tx_queue_.push_back(std::make_shared<std::vector<uint8_t>>(std::move(packet_bytes)));
      start_next_write();
    }
    catch (const std::exception &e)
    {
      RCLCPP_ERROR(this->get_logger(), "Send error: %s", e.what());
      reset_serial();
      return false;
    }

    return true;
  }

  void SerialController::start_next_write()
  {
    if (tx_write_in_progress_ || tx_queue_.empty())
    {
      return;
    }

    if (!is_connected_ || !serial_port_ || !serial_port_->is_open())
    {
      tx_queue_.clear();
      tx_write_in_progress_ = false;
      return;
    }

    const auto port = serial_port_;
    const auto generation = connection_generation_;
    const auto packet_bytes = tx_queue_.front();
    tx_write_in_progress_ = true;
    ++pending_serial_ops_;

    try
    {
      asio::async_write(
          *port,
          asio::buffer(*packet_bytes),
          serial_strand_->wrap(
              [this, port, generation, packet_bytes](
                  const asio::error_code &error,
                  const size_t bytes_transferred)
              {
                handle_write(port, generation, packet_bytes, error, bytes_transferred);
              }));
    }
    catch (const std::exception &e)
    {
      complete_serial_op();
      tx_write_in_progress_ = false;
      tx_queue_.clear();
      RCLCPP_ERROR(
          this->get_logger(),
          "Failed to start serial write on '%s': %s",
          port_.c_str(),
          e.what());
      reset_serial();
    }
  }

  void SerialController::handle_write(
      const std::shared_ptr<asio::serial_port> &port,
      uint64_t generation,
      const std::shared_ptr<std::vector<uint8_t>> &packet_bytes,
      const asio::error_code &error,
      size_t)
  {
    complete_serial_op();

    if (generation != connection_generation_ || serial_port_ != port)
    {
      return;
    }

    tx_write_in_progress_ = false;
    if (!tx_queue_.empty() && tx_queue_.front() == packet_bytes)
    {
      tx_queue_.pop_front();
    }

    if (error)
    {
      if (error == asio::error::operation_aborted)
      {
        return;
      }

      RCLCPP_ERROR(
          this->get_logger(),
          "Serial write error on '%s': %s",
          port_.c_str(),
          error.message().c_str());
      reset_serial();
      return;
    }

    start_next_write();
  }

} // namespace auto_serial_bridge

RCLCPP_COMPONENTS_REGISTER_NODE(auto_serial_bridge::SerialController)
