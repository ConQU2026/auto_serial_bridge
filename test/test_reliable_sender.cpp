#include <gtest/gtest.h>

#include <asio/io_context_strand.hpp>
#include <asio/io_service.hpp>

#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <functional>
#include <memory>
#include <mutex>
#include <thread>
#include <utility>
#include <vector>

#include "auto_serial_bridge/packet_handler.hpp"
#include "auto_serial_bridge/reliable_sender.hpp"
#include "protocol.h"

using namespace std::chrono_literals;

namespace
{

  class ReliableSenderTest : public ::testing::Test
  {
  protected:
    struct ExhaustedEvent
    {
      PacketID id;
      int max_retries;
    };

    void SetUp() override
    {
      work_ = std::make_unique<asio::io_service::work>(io_);
      io_thread_ = std::thread([this]()
                               { io_.run(); });
    }

    void TearDown() override
    {
      work_.reset();
      io_.stop();
      if (io_thread_.joinable())
      {
        io_thread_.join();
      }
    }

    std::shared_ptr<auto_serial_bridge::ReliableSender> create_sender(
        std::chrono::milliseconds retry_interval,
        int max_retries)
    {
      return std::make_shared<auto_serial_bridge::ReliableSender>(
          io_,
          strand_,
          [this](const std::vector<uint8_t> &packet_bytes)
          {
            {
              std::lock_guard<std::mutex> lock(mutex_);
              sent_packets_.push_back(packet_bytes);
            }
            cv_.notify_all();
            return true;
          },
          [this](PacketID id, int retries)
          {
            {
              std::lock_guard<std::mutex> lock(mutex_);
              exhausted_events_.push_back({id, retries});
            }
            cv_.notify_all();
          },
          retry_interval,
          max_retries);
    }

    std::vector<uint8_t> pack_heartbeat(uint32_t count)
    {
      Packet_Heartbeat pkt;
      pkt.count = count;
      return packet_handler_.pack(PACKET_ID_HEARTBEAT, pkt);
    }

    /// 从发送帧中提取 ReliableSender 注入的 seq 字节（倒数第二位）。
    static uint8_t extract_seq_from_frame(const std::vector<uint8_t> &frame)
    {
      // 帧格式: [H1][H2][ID][LEN][PAYLOAD...][SEQ][CRC]
      // seq 是校验和之前的最后一个 payload 字节
      return frame[frame.size() - 2];
    }

    bool wait_for(
        const std::function<bool()> &predicate,
        std::chrono::milliseconds timeout)
    {
      std::unique_lock<std::mutex> lock(mutex_);
      return cv_.wait_for(lock, timeout, predicate);
    }

    size_t sent_count()
    {
      std::lock_guard<std::mutex> lock(mutex_);
      return sent_packets_.size();
    }

    size_t exhausted_count()
    {
      std::lock_guard<std::mutex> lock(mutex_);
      return exhausted_events_.size();
    }

    std::vector<uint8_t> latest_sent_packet()
    {
      std::lock_guard<std::mutex> lock(mutex_);
      if (sent_packets_.empty())
      {
        return {};
      }
      return sent_packets_.back();
    }

    std::vector<uint8_t> sent_packet_at(size_t index)
    {
      std::lock_guard<std::mutex> lock(mutex_);
      if (index >= sent_packets_.size())
      {
        return {};
      }
      return sent_packets_[index];
    }

    ExhaustedEvent first_exhausted_event()
    {
      std::lock_guard<std::mutex> lock(mutex_);
      if (exhausted_events_.empty())
      {
        return {PACKET_ID_ACK, 0};
      }
      return exhausted_events_.front();
    }

    asio::io_service io_;
    asio::io_service::strand strand_{io_};
    std::unique_ptr<asio::io_service::work> work_;
    std::thread io_thread_;

    auto_serial_bridge::PacketHandler packet_handler_{1024};

    std::mutex mutex_;
    std::condition_variable cv_;
    std::vector<std::vector<uint8_t>> sent_packets_;
    std::vector<ExhaustedEvent> exhausted_events_;
  };

  TEST_F(ReliableSenderTest, SendAndAck)
  {
    auto sender = create_sender(20ms, 2);
    auto bytes = pack_heartbeat(1);

    sender->send(PACKET_ID_HEARTBEAT, bytes);
    ASSERT_TRUE(wait_for([this]()
                         { return sent_packets_.size() == 1; }, 200ms));

    const uint8_t seq = extract_seq_from_frame(latest_sent_packet());
    sender->on_ack_received(static_cast<uint8_t>(PACKET_ID_HEARTBEAT), seq);
    std::this_thread::sleep_for(80ms);

    EXPECT_EQ(sent_count(), 1u);
    EXPECT_EQ(exhausted_count(), 0u);
  }

  TEST_F(ReliableSenderTest, RetryOnTimeout)
  {
    auto sender = create_sender(20ms, 3);
    auto bytes = pack_heartbeat(1);

    sender->send(PACKET_ID_HEARTBEAT, bytes);

    ASSERT_TRUE(wait_for([this]()
                         { return sent_packets_.size() >= 2; }, 250ms));
    EXPECT_GE(sent_count(), 2u);
  }

  TEST_F(ReliableSenderTest, MaxRetriesExceeded)
  {
    auto sender = create_sender(20ms, 1);
    auto bytes = pack_heartbeat(1);

    sender->send(PACKET_ID_HEARTBEAT, bytes);

    ASSERT_TRUE(wait_for([this]()
                         { return exhausted_events_.size() == 1; }, 300ms));
    EXPECT_EQ(sent_count(), 2u);

    const auto exhausted = first_exhausted_event();
    EXPECT_EQ(exhausted.id, PACKET_ID_HEARTBEAT);
    EXPECT_EQ(exhausted.max_retries, 1);
  }

  TEST_F(ReliableSenderTest, NewSendOverridesPending)
  {
    auto sender = create_sender(30ms, 2);
    auto first = pack_heartbeat(1);
    auto second = pack_heartbeat(2);

    sender->send(PACKET_ID_HEARTBEAT, first);
    ASSERT_TRUE(wait_for([this]()
                         { return sent_packets_.size() == 1; }, 200ms));

    sender->send(PACKET_ID_HEARTBEAT, second);
    ASSERT_TRUE(wait_for([this]()
                         { return sent_packets_.size() == 2; }, 200ms));
    ASSERT_TRUE(wait_for([this]()
                         { return sent_packets_.size() >= 3; }, 250ms));

    // 最后重发的帧 payload 应与 second 一致（seq 之前的部分）
    const auto last_sent = latest_sent_packet();
    const auto second_sent = sent_packet_at(1);
    EXPECT_EQ(last_sent, second_sent);
  }

  TEST_F(ReliableSenderTest, ResetClearsPending)
  {
    auto sender = create_sender(20ms, 2);
    auto bytes = pack_heartbeat(1);

    sender->send(PACKET_ID_HEARTBEAT, bytes);
    ASSERT_TRUE(wait_for([this]()
                         { return sent_packets_.size() == 1; }, 200ms));

    sender->clear_all();
    std::this_thread::sleep_for(80ms);

    EXPECT_EQ(sent_count(), 1u);
    EXPECT_EQ(exhausted_count(), 0u);
  }

  TEST_F(ReliableSenderTest, StaleAckRejected)
  {
    auto sender = create_sender(40ms, 3);
    auto first = pack_heartbeat(10);
    auto second = pack_heartbeat(20);

    // 发送第一版
    sender->send(PACKET_ID_HEARTBEAT, first);
    ASSERT_TRUE(wait_for([this]()
                         { return sent_packets_.size() == 1; }, 200ms));
    const uint8_t seq1 = extract_seq_from_frame(sent_packet_at(0));

    // 发送第二版（覆盖第一版）
    sender->send(PACKET_ID_HEARTBEAT, second);
    ASSERT_TRUE(wait_for([this]()
                         { return sent_packets_.size() == 2; }, 200ms));
    const uint8_t seq2 = extract_seq_from_frame(sent_packet_at(1));

    // 确认 seq 不同
    EXPECT_NE(seq1, seq2);

    // 用旧 seq 发送过期 ACK — 应被拒绝
    sender->on_ack_received(static_cast<uint8_t>(PACKET_ID_HEARTBEAT), seq1);

    // 等待重试，证明 pending 未被清除
    ASSERT_TRUE(wait_for([this]()
                         { return sent_packets_.size() >= 3; }, 300ms));
    EXPECT_GE(sent_count(), 3u);

    // 用正确的 seq2 确认 — 应被接受
    sender->on_ack_received(static_cast<uint8_t>(PACKET_ID_HEARTBEAT), seq2);
    std::this_thread::sleep_for(100ms);

    // 确认后不再重发
    const size_t final_count = sent_count();
    std::this_thread::sleep_for(100ms);
    EXPECT_EQ(sent_count(), final_count);
    EXPECT_EQ(exhausted_count(), 0u);
  }

} // namespace
