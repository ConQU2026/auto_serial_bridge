#include <gtest/gtest.h>

#include "auto_serial_bridge/packet_handler.hpp"
#include "protocol.h"

using namespace auto_serial_bridge;

TEST(PacketHandlerResetTest, ResetClearsBufferedPartialFrame) {
    PacketHandler handler(64);

    Packet_Heartbeat heartbeat{123};
    const auto full_packet = handler.pack(PACKET_ID_HEARTBEAT, heartbeat);

    ASSERT_GT(full_packet.size(), 3u);
    handler.feed_data(full_packet.data(), 3);
    EXPECT_GT(handler.data_available(), 0u);

    handler.reset();

    EXPECT_EQ(handler.data_available(), 0u);

    Packet pkt;
    handler.feed_data(full_packet);
    ASSERT_TRUE(handler.parse_packet(pkt));
    EXPECT_EQ(pkt.id, PACKET_ID_HEARTBEAT);
    EXPECT_EQ(pkt.as<Packet_Heartbeat>().count, 123u);
}
