#include <gtest/gtest.h>
#include <vector>
#include <cstring>
#include "auto_serial_bridge/packet_handler.hpp"
#include "protocol.h" // For IDs and Hash

using namespace auto_serial_bridge;

namespace {

class PacketHandlerTest : public ::testing::Test {
protected:
    void SetUp() override {
        // ...
    }
};

TEST_F(PacketHandlerTest, FullPackAndParse) {
    PacketHandler handler(1024);
    
    Packet_Heartbeat data_in = {100};
    std::vector<uint8_t> bytes = handler.pack(PACKET_ID_HEARTBEAT, data_in);
    
    // Header(2) + ID(1) + Len(1) + Payload(4) + CRC(1) = 9 字节
    EXPECT_EQ(bytes.size(), 9);
    EXPECT_EQ(bytes[0], FRAME_HEADER1);
    EXPECT_EQ(bytes[1], FRAME_HEADER2);
    
    handler.feed_data(bytes);
    
    Packet pkt;
    ASSERT_TRUE(handler.parse_packet(pkt));
    EXPECT_EQ(pkt.id, PACKET_ID_HEARTBEAT);
    EXPECT_EQ(pkt.payload.size(), sizeof(Packet_Heartbeat));
    
    Packet_Heartbeat data_out = pkt.as<Packet_Heartbeat>();
    EXPECT_EQ(data_out.count, 100);
}

TEST_F(PacketHandlerTest, FragmentedData) {
    PacketHandler handler(1024);
    Packet_Heartbeat data_in = {42};
    std::vector<uint8_t> bytes = handler.pack(PACKET_ID_HEARTBEAT, data_in);
    
    // 投喂前半部分
    size_t split = 3;
    handler.feed_data(bytes.data(), split);
    
    Packet pkt;
    EXPECT_FALSE(handler.parse_packet(pkt));
    
    // 投喂剩余部分
    handler.feed_data(bytes.data() + split, bytes.size() - split);
    
    ASSERT_TRUE(handler.parse_packet(pkt));
    EXPECT_EQ(pkt.id, PACKET_ID_HEARTBEAT);
}

TEST_F(PacketHandlerTest, StickyPackets) {
    PacketHandler handler(1024);
    Packet_Heartbeat data1 = {11};
    Packet_Heartbeat data2 = {22};
    
    std::vector<uint8_t> bytes1 = handler.pack(PACKET_ID_HEARTBEAT, data1);
    std::vector<uint8_t> bytes2 = handler.pack(PACKET_ID_HEARTBEAT, data2);
    
    std::vector<uint8_t> all_bytes = bytes1;
    all_bytes.insert(all_bytes.end(), bytes2.begin(), bytes2.end());
    
    handler.feed_data(all_bytes);
    
    Packet pkt;
    // 第一个包
    ASSERT_TRUE(handler.parse_packet(pkt));
    auto d1 = pkt.as<Packet_Heartbeat>();
    EXPECT_EQ(d1.count, 11);
    
    // 第二个包
    ASSERT_TRUE(handler.parse_packet(pkt));
    auto d2 = pkt.as<Packet_Heartbeat>();
    EXPECT_EQ(d2.count, 22);
    
    // 没有更多了
    EXPECT_FALSE(handler.parse_packet(pkt));
}

TEST_F(PacketHandlerTest, WrapAround) {
    // 小缓冲区以强制回绕
    // 每个 Heartbeat 包 9 字节。缓冲区 18 字节。
    PacketHandler handler(18); 
    
    Packet_Heartbeat data = {123};
    std::vector<uint8_t> bytes = handler.pack(PACKET_ID_HEARTBEAT, data); // 9 bytes
    
    // 1. 填充 9 字节
    handler.feed_data(bytes);
    Packet pkt;
    ASSERT_TRUE(handler.parse_packet(pkt)); // 消耗 9 字节。Tail 在 9。
    
    // 2. 再次投喂。9 字节。
    // 缓冲区容量 19 (18+1)。Head 9。
    // 投喂 9 字节: 9->18 (9 字节)，下次解析应正常工作。
    handler.feed_data(bytes);
    
    ASSERT_TRUE(handler.parse_packet(pkt));
    Packet_Heartbeat out = pkt.as<Packet_Heartbeat>();
    EXPECT_EQ(out.count, 123);
}

TEST_F(PacketHandlerTest, ChecksumError) {
    PacketHandler handler(1024);
    Packet_Heartbeat data = {1};
    std::vector<uint8_t> bytes = handler.pack(PACKET_ID_HEARTBEAT, data);
    
    // 损坏数据 (最后一个字节是 CRC)
    bytes.back() ^= 0xFF;
    
    handler.feed_data(bytes);
    Packet pkt;
    if constexpr (config::CHECKSUM_ALGO == config::ChecksumAlgo::NONE) {
        ASSERT_TRUE(handler.parse_packet(pkt));
        EXPECT_EQ(pkt.id, PACKET_ID_HEARTBEAT);
    } else {
        EXPECT_FALSE(handler.parse_packet(pkt));
    }
}

TEST_F(PacketHandlerTest, NoiseBeforeHeader) {
    PacketHandler handler(1024);
    Packet_Heartbeat data = {99};
    std::vector<uint8_t> bytes = handler.pack(PACKET_ID_HEARTBEAT, data);
    
    std::vector<uint8_t> noise = {0x00, 0x11, 0x5A, 0x00, 0xFF}; // 0x5A 但后面不是 0xA5
    
    handler.feed_data(noise);
    handler.feed_data(bytes);
    
    Packet pkt;
    ASSERT_TRUE(handler.parse_packet(pkt)); // 应跳过噪音并找到有效数据包
    EXPECT_EQ(pkt.id, PACKET_ID_HEARTBEAT);
}

TEST_F(PacketHandlerTest, OversizedFrameLengthDoesNotBlockResync) {
    PacketHandler handler(16);

    std::vector<uint8_t> invalid = {
        FRAME_HEADER1,
        FRAME_HEADER2,
        static_cast<uint8_t>(PACKET_ID_HEARTBEAT),
        0xFF,
        0x00,
    };
    Packet_Heartbeat data = {77};
    std::vector<uint8_t> valid = handler.pack(PACKET_ID_HEARTBEAT, data);

    handler.feed_data(invalid);

    Packet pkt;
    EXPECT_FALSE(handler.parse_packet(pkt));

    handler.feed_data(valid);

    ASSERT_TRUE(handler.parse_packet(pkt));
    EXPECT_EQ(pkt.id, PACKET_ID_HEARTBEAT);
    EXPECT_EQ(pkt.as<Packet_Heartbeat>().count, 77);
}

TEST_F(PacketHandlerTest, PayloadLongerThanProtocolMaximumIsRejectedAndResynced) {
    PacketHandler handler(64);

    std::vector<uint8_t> invalid = {
        FRAME_HEADER1,
        FRAME_HEADER2,
        static_cast<uint8_t>(PACKET_ID_HEARTBEAT),
        20,
    };
    invalid.insert(invalid.end(), 20, 0xAB);
    invalid.push_back(PacketHandler::calculate_checksum(invalid.data() + 2, invalid.size() - 2));

    Packet_Heartbeat data = {33};
    std::vector<uint8_t> valid = handler.pack(PACKET_ID_HEARTBEAT, data);

    handler.feed_data(invalid);
    handler.feed_data(valid);

    Packet pkt;
    ASSERT_TRUE(handler.parse_packet(pkt));
    EXPECT_EQ(pkt.id, PACKET_ID_HEARTBEAT);
    EXPECT_EQ(pkt.as<Packet_Heartbeat>().count, 33);
}

}
