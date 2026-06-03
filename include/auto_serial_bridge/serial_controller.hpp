#pragma once

#include <memory>
#include <string>
#include <vector>
#include <functional>
#include <mutex>
#include <atomic>
#include <chrono>
#include <unordered_map>
#include <cstddef>
#include <cstdint>
#include <algorithm>
#include <cstdio>

#include "rcutils/logging.h"
#include "rclcpp/rclcpp.hpp"
#include "serial_driver/serial_driver.hpp"
#include "io_context/io_context.hpp"

#include "auto_serial_bridge/packet_handler.hpp"
#include "auto_serial_bridge/protocol.hpp"
#include "auto_serial_bridge/reliable_sender.hpp"

namespace auto_serial_bridge
{

  namespace generated
  {
    struct ProtocolPublishers;
  }

  namespace detail
  {

    enum class ReceiveFollowUpAction
    {
      ContinueReading,
      ResetConnection
    };

    enum class HandshakeValidationResult
    {
      Matched,
      IgnoredMismatch,
      RejectedMismatch
    };

    inline ReceiveFollowUpAction classify_receive_result(size_t bytes_read, bool port_is_open)
    {
      if (bytes_read > 0)
      {
        return ReceiveFollowUpAction::ContinueReading;
      }
      return port_is_open ? ReceiveFollowUpAction::ContinueReading
                          : ReceiveFollowUpAction::ResetConnection;
    }

    inline HandshakeValidationResult classify_handshake_validation(
        uint32_t local_hash,
        uint32_t remote_hash,
        bool ignore_version_mismatch)
    {
      if (local_hash == remote_hash)
      {
        return HandshakeValidationResult::Matched;
      }
      if (ignore_version_mismatch)
      {
        return HandshakeValidationResult::IgnoredMismatch;
      }
      return HandshakeValidationResult::RejectedMismatch;
    }

    inline std::string format_hash_pair(uint32_t local_hash, uint32_t remote_hash)
    {
      char buf[64];
      std::snprintf(
          buf,
          sizeof(buf),
          "local=0x%08X, remote=0x%08X",
          static_cast<unsigned int>(local_hash),
          static_cast<unsigned int>(remote_hash));
      return std::string(buf);
    }

    inline std::string format_hex_payload(const uint8_t *data, size_t len, size_t max_bytes = 16)
    {
      if (data == nullptr || len == 0)
      {
        return "(empty)";
      }

      const size_t visible = std::min(len, max_bytes);
      std::string out;
      out.reserve(visible * 3 + 24);

      char byte_buf[3];
      for (size_t i = 0; i < visible; ++i)
      {
        if (i > 0)
        {
          out.push_back(' ');
        }
        std::snprintf(byte_buf, sizeof(byte_buf), "%02X", data[i]);
        out.append(byte_buf);
      }

      if (len > visible)
      {
        out += " ...("
               + std::to_string(static_cast<unsigned long long>(len))
               + " bytes)";
      }
      return out;
    }

    inline const char *handshake_mode_name(
        bool require_handshake,
        bool ignore_version_mismatch)
    {
      if (!require_handshake)
      {
        return "disabled";
      }
      return ignore_version_mismatch ? "ignore_mismatch" : "strict";
    }

    inline const char *heartbeat_mode_name(
        bool enable_heartbeat,
        bool strict_heartbeat)
    {
      if (!enable_heartbeat)
      {
        return "disabled";
      }
      return strict_heartbeat ? "strict" : "warn_only";
    }

    inline bool should_log_raw_tx_frame(bool debug_raw_frame, bool debug_log_enabled)
    {
      return debug_raw_frame && debug_log_enabled;
    }

  } // namespace detail

  /**
   * @brief 串口控制节点
   */
  class SerialController : public rclcpp::Node
  {
  public:
    explicit SerialController(const rclcpp::NodeOptions &options);
    ~SerialController() override;

    template <typename T>
    void send_packet(PacketID id, const T &data)
    {
      auto bytes = packet_handler_.pack(id, data);
      async_send(bytes);
    }

    template <typename T>
    void reliable_send(PacketID id, const T &data)
    {
      auto bytes = packet_handler_.pack(id, data);
      post_serial([this, id, bytes = std::move(bytes)]() mutable
                  {
         if (!reliable_sender_) {
           if (async_send_impl(bytes)) {
             tx_packet_count_++;
           }
           return;
         }
         reliable_sender_->send(id, std::move(bytes)); });
    }

    void add_subscription(std::shared_ptr<rclcpp::SubscriptionBase> sub)
    {
      subscriptions_.push_back(sub);
    }

    void register_loopback_publisher(
        PacketID id,
        const std::shared_ptr<rclcpp::PublisherBase> &publisher);

    bool should_skip_loopback(PacketID id, const rclcpp::MessageInfo &info) const;

  private:
    void get_parameters();
    void start_receive();
    void async_send(const std::vector<uint8_t> &packet_bytes);
    bool async_send_impl(const std::vector<uint8_t> &packet_bytes);
    void log_mcu_tx(const std::vector<uint8_t> &packet_bytes) const;
    bool should_log_mcu_tx_raw(PacketID id) const;
    void check_connection();
    void check_connection_impl();
    void reset_serial();
    bool try_open_serial();
    bool try_open_serial_impl();
    void handle_heartbeat_timer();
    void handle_receive(
        const std::shared_ptr<drivers::serial_driver::SerialPort> &port,
        const std::vector<uint8_t> &buffer,
        size_t bytes_read);
    void handle_packet(const Packet &pkt);
    size_t ingest_received_bytes(const uint8_t *data, size_t len);
    void post_serial(std::function<void()> task);
    const char *state_name() const;

    enum class State
    {
      WAITING_HANDSHAKE,
      RUNNING
    };
    State state_;
    void process_handshake(const Packet &pkt);

    // IoContext 和 驱动
    std::shared_ptr<drivers::common::IoContext> ctx_;
    std::unique_ptr<asio::io_service::strand> serial_strand_;
    std::unique_ptr<drivers::serial_driver::SerialDriver> driver_;
    std::unique_ptr<drivers::serial_driver::SerialPortConfig> device_config_;

    PacketHandler packet_handler_;
    std::shared_ptr<ReliableSender> reliable_sender_;

    std::vector<std::shared_ptr<rclcpp::SubscriptionBase>> subscriptions_;
    std::unordered_map<uint8_t, std::weak_ptr<rclcpp::PublisherBase>> loopback_publishers_;
    mutable std::mutex loopback_publishers_mutex_;

    // 定时器和状态
    rclcpp::TimerBase::SharedPtr timer_;
    rclcpp::TimerBase::SharedPtr heartbeat_timer_;
    std::atomic<bool> is_connected_{false};

    // 心跳跟踪
    uint32_t heartbeat_count_ = 0;
    uint32_t last_heartbeat_tx_count_ = 0;
    std::chrono::steady_clock::time_point heartbeat_ack_wait_started_at_;
    std::chrono::steady_clock::time_point last_heartbeat_ack_time_;
    bool awaiting_heartbeat_ack_ = false;
    bool heartbeat_ack_received_ = false;
    bool enable_heartbeat_ = true;
    bool strict_heartbeat_ = true;
    int heartbeat_timeout_ms_ = 3000;

    // 运行时计数器
    std::atomic<uint32_t> tx_packet_count_{0};

    // 参数
    std::string port_;
    uint32_t baudrate_;
    double timeout_;
    bool debug_raw_frame_ = false;

    std::shared_ptr<generated::ProtocolPublishers> protocol_impl_;
  };
} // namespace auto_serial_bridge
