#pragma once

#include <memory>
#include <string>
#include <vector>
#include <functional>
#include <mutex>
#include <atomic>
#include <chrono>
#include <unordered_map>

#include "rcutils/logging.h"
#include "rclcpp/rclcpp.hpp"
#include "serial_driver/serial_driver.hpp"
#include "io_context/io_context.hpp"

#include "auto_serial_bridge/packet_handler.hpp"
#include "auto_serial_bridge/protocol.hpp"

namespace auto_serial_bridge
{

namespace generated { struct ProtocolPublishers; }

  /**
   * @brief 串口控制节点
   */
  class SerialController : public rclcpp::Node
  {
  public:
    explicit SerialController(const rclcpp::NodeOptions &options);
    ~SerialController() override;

    template <typename T>
    void send_packet(PacketID id, const T& data) {
         auto bytes = packet_handler_.pack(id, data);
         async_send(bytes);
         tx_packet_count_++;
    }

    void add_subscription(std::shared_ptr<rclcpp::SubscriptionBase> sub) {
        subscriptions_.push_back(sub);
    }

    void register_loopback_publisher(
        PacketID id,
        const std::shared_ptr<rclcpp::PublisherBase>& publisher);

    bool should_skip_loopback(PacketID id, const rclcpp::MessageInfo& info) const;

  private:
    void get_parameters();
    void start_receive();
    void async_send(const std::vector<uint8_t> &packet_bytes);
    void check_connection();
    void reset_serial();
    bool try_open_serial();
    
    enum class State {
        WAITING_HANDSHAKE,
        RUNNING
    };
    State state_;
    void process_handshake(const Packet& pkt);
    
    // IoContext 和 驱动
    std::shared_ptr<drivers::common::IoContext> ctx_;
    std::unique_ptr<drivers::serial_driver::SerialDriver> driver_;
    std::unique_ptr<drivers::serial_driver::SerialPortConfig> device_config_;

    /**
     * rx_mutex_ 保护 packet_handler_ 的 feed_data() 和 parse_packet()。
     * async_receive 回调在 IoContext 线程中串行执行，但为了防止
     * 与其他线程（如定时器）的潜在竞争，统一加锁。
     */
    std::mutex rx_mutex_;

    PacketHandler packet_handler_;
    
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
    int heartbeat_timeout_ms_ = 3000;

    // 运行时计数器
    std::atomic<uint32_t> tx_packet_count_{0};

    // 参数
    std::string port_;
    uint32_t baudrate_;
    double timeout_;

    std::shared_ptr<generated::ProtocolPublishers> protocol_impl_;
  };
} // namespace auto_serial_bridge
