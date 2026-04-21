#include <gtest/gtest.h>

#include <asio/io_context_strand.hpp>
#include <asio/io_service.hpp>

#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <memory>
#include <mutex>
#include <thread>
#include <vector>

#include "auto_serial_bridge/packet_handler.hpp"
#include "auto_serial_bridge/reliable_sender.hpp"
#include "protocol.h"

using namespace std::chrono_literals;

namespace
{

class ReliableRoundtripTest : public ::testing::Test
{
protected:
  void SetUp() override
  {
    work_ = std::make_unique<asio::io_service::work>(io_);
    io_thread_ = std::thread([this]() { io_.run(); });
  }

  void TearDown() override
  {
    if (sender_) {
      sender_->clear_all();
    }
    work_.reset();
    io_.stop();
    if (io_thread_.joinable()) {
      io_thread_.join();
    }
  }

  void create_sender(std::chrono::milliseconds retry_interval = 5s, int max_retries = 0)
  {
    sender_ = std::make_shared<auto_serial_bridge::ReliableSender>(
      io_,
      strand_,
      [this](const std::vector<uint8_t> & frame) {
        {
          std::lock_guard<std::mutex> lock(mutex_);
          sent_frames_.push_back(frame);
        }
        cv_.notify_all();
        return true;
      },
      [](PacketID, int) {},
      retry_interval,
      max_retries);
  }

  std::vector<uint8_t> pack_heartbeat(uint32_t count)
  {
    Packet_Heartbeat pkt{};
    pkt.count = count;
    return packet_handler_.pack(PACKET_ID_HEARTBEAT, pkt);
  }

  std::vector<uint8_t> pack_handshake(uint32_t protocol_hash)
  {
    Packet_Handshake pkt{};
    pkt.protocol_hash = protocol_hash;
    return packet_handler_.pack(PACKET_ID_HANDSHAKE, pkt);
  }

  std::vector<uint8_t> send_and_capture(PacketID id, const std::vector<uint8_t> & packed_frame)
  {
    create_sender();
    sender_->send(id, packed_frame);

    std::unique_lock<std::mutex> lock(mutex_);
    const bool got_frame = cv_.wait_for(
      lock, 500ms, [this]() { return !sent_frames_.empty(); });
    EXPECT_TRUE(got_frame);
    return got_frame ? sent_frames_.front() : std::vector<uint8_t>{};
  }

  static uint8_t trailing_seq(const std::vector<uint8_t> & frame)
  {
    return frame.at(frame.size() - 2);
  }

  asio::io_service io_;
  asio::io_service::strand strand_{io_};
  std::unique_ptr<asio::io_service::work> work_;
  std::thread io_thread_;
  std::shared_ptr<auto_serial_bridge::ReliableSender> sender_;

  auto_serial_bridge::PacketHandler packet_handler_{1024};
  std::mutex mutex_;
  std::condition_variable cv_;
  std::vector<std::vector<uint8_t>> sent_frames_;
};

TEST_F(ReliableRoundtripTest, DISABLED_InjectSeqAndParseBack)
{
  const auto original = pack_heartbeat(42);
  const auto injected = send_and_capture(PACKET_ID_HEARTBEAT, original);

  ASSERT_FALSE(injected.empty());
  EXPECT_EQ(injected[3], original[3] + 1);

  auto_serial_bridge::PacketHandler parser(1024);
  parser.feed_data(injected);

  auto_serial_bridge::Packet pkt;
  ASSERT_TRUE(parser.parse_packet(pkt));
  ASSERT_EQ(pkt.id, PACKET_ID_HEARTBEAT);
  ASSERT_EQ(pkt.payload.size(), sizeof(Packet_Heartbeat) + 1);
  EXPECT_EQ(pkt.payload.back(), trailing_seq(injected));
}

TEST_F(ReliableRoundtripTest, InjectSeqUpdatesLenAndChecksum)
{
  const auto original = pack_heartbeat(0x11223344u);
  const auto injected = send_and_capture(PACKET_ID_HEARTBEAT, original);

  ASSERT_FALSE(injected.empty());
  ASSERT_EQ(original.size() + 1, injected.size());
  EXPECT_EQ(injected[0], FRAME_HEADER1);
  EXPECT_EQ(injected[1], FRAME_HEADER2);
  EXPECT_EQ(injected[2], static_cast<uint8_t>(PACKET_ID_HEARTBEAT));
  EXPECT_EQ(injected[3], original[3] + 1);
  EXPECT_EQ(trailing_seq(injected), 0u);

  const uint8_t expected_checksum =
    auto_serial_bridge::PacketHandler::calculate_checksum(
    injected.data() + 2, injected.size() - 3);
  EXPECT_EQ(injected.back(), expected_checksum);
}

TEST_F(ReliableRoundtripTest, InjectSeqUsesLargestSystemPayload)
{
  const auto original = pack_handshake(PROTOCOL_HASH);
  const auto injected = send_and_capture(PACKET_ID_HANDSHAKE, original);

  ASSERT_FALSE(injected.empty());
  ASSERT_EQ(sizeof(Packet_Handshake), sizeof(Packet_Heartbeat));
  EXPECT_EQ(original[3], sizeof(Packet_Handshake));
  EXPECT_EQ(injected[3], sizeof(Packet_Handshake) + 1);
  EXPECT_EQ(injected.size(), original.size() + 1);

  const uint8_t expected_checksum =
    auto_serial_bridge::PacketHandler::calculate_checksum(
    injected.data() + 2, injected.size() - 3);
  EXPECT_EQ(injected.back(), expected_checksum);
}

}  // namespace
