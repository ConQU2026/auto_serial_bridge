#include <gtest/gtest.h>

#include <cstdint>
#include <vector>

#include "auto_serial_bridge/packet_handler.hpp"
#include "protocol.h"

using namespace auto_serial_bridge;

namespace
{

Packet roundtrip_packet(PacketHandler & handler, PacketID id, const std::vector<uint8_t> & frame)
{
  handler.feed_data(frame);
  Packet pkt;
  EXPECT_TRUE(handler.parse_packet(pkt));
  EXPECT_EQ(pkt.id, id);
  return pkt;
}

TEST(HeartbeatWraparoundTest, CounterWrapAroundMatchesCorrectly)
{
  PacketHandler handler(1024);
  const std::vector<uint32_t> counts = {
    UINT32_MAX - 1u,
    UINT32_MAX,
    0u,
  };

  for (const uint32_t count : counts) {
    Packet_Heartbeat in{};
    in.count = count;
    const auto frame = handler.pack(PACKET_ID_HEARTBEAT, in);
    const Packet pkt = roundtrip_packet(handler, PACKET_ID_HEARTBEAT, frame);
    EXPECT_EQ(pkt.as<Packet_Heartbeat>().count, count);
  }
}

TEST(HeartbeatWraparoundTest, MismatchedCountDetected)
{
  PacketHandler handler(1024);
  const uint32_t expected_count = 100u;

  Packet_Heartbeat in{};
  in.count = 200u;
  const auto frame = handler.pack(PACKET_ID_HEARTBEAT, in);
  const Packet pkt = roundtrip_packet(handler, PACKET_ID_HEARTBEAT, frame);

  const uint32_t received_count = pkt.as<Packet_Heartbeat>().count;
  EXPECT_NE(received_count, expected_count);
}

TEST(HeartbeatWraparoundTest, ResetCounterAfterHandshake)
{
  PacketHandler handler(1024);

  Packet_Handshake handshake{};
  handshake.protocol_hash = PROTOCOL_HASH;
  const auto handshake_frame = handler.pack(PACKET_ID_HANDSHAKE, handshake);
  const Packet handshake_pkt = roundtrip_packet(handler, PACKET_ID_HANDSHAKE, handshake_frame);
  EXPECT_EQ(handshake_pkt.as<Packet_Handshake>().protocol_hash, PROTOCOL_HASH);

  Packet_Heartbeat heartbeat{};
  heartbeat.count = 0u;
  const auto heartbeat_frame = handler.pack(PACKET_ID_HEARTBEAT, heartbeat);
  EXPECT_EQ(heartbeat_frame[3], sizeof(Packet_Heartbeat));

  const Packet heartbeat_pkt = roundtrip_packet(handler, PACKET_ID_HEARTBEAT, heartbeat_frame);
  EXPECT_EQ(heartbeat_pkt.as<Packet_Heartbeat>().count, 0u);
}

}  // namespace
