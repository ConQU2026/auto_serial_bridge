#include <gtest/gtest.h>
#include <vector>
#include "auto_serial_bridge/packet_handler.hpp"
#include "protocol.h"

using namespace auto_serial_bridge;

class EdgeCaseTest : public ::testing::Test {
protected:
    PacketHandler handler{4096};
};

// 1. 未知 ID 测试
TEST_F(EdgeCaseTest, UnknownID) {
    // 手动构造具有 ID 0xFF 的数据包 
    uint8_t unknown_id = 0xFF;
    std::vector<uint8_t> data = {0x01, 0x02};
    uint8_t len = data.size();
    
    // Header
    std::vector<uint8_t> pkt = {FRAME_HEADER1, FRAME_HEADER2, unknown_id, len};
    // 数据
    pkt.insert(pkt.end(), data.begin(), data.end());
    // 计算 CRC (CRC 覆盖 ID, Len, Data)
    std::vector<uint8_t> crc_payload = {unknown_id, len};
    crc_payload.insert(crc_payload.end(), data.begin(), data.end());
    
    uint8_t crc = 0;
    for(auto b : crc_payload) crc = CRC8_TABLE[crc ^ b];
    pkt.push_back(crc);
    
    handler.feed_data(pkt);
    Packet out;
    
    // 应当作为有效帧解析成功。
    // 应用逻辑 (分发器) 决定如何处理未知 ID。
    // PacketHandler 仅确保帧有效性。
    ASSERT_TRUE(handler.parse_packet(out));
    EXPECT_EQ(out.id, unknown_id);
}

// 2. 零长度载荷测试
TEST_F(EdgeCaseTest, ZeroLengthPayload) {
    // 构造 len = 0 的数据包
    // uint8_t id = PACKET_ID_HEARTBEAT; // Assume heartbeat can be empty for test (Removed unused var)
    uint8_t len = 0;
    
    // 握手包通常是 4 字节，心跳包是 1 字节。
    // 让我们为 ID 0xEE 强制构造一个 0 长度的数据包
    uint8_t test_id = 0xEE;
    
    std::vector<uint8_t> pkt = {FRAME_HEADER1, FRAME_HEADER2, test_id, len};
    // 无数据
    
    // CRC
    uint8_t crc = 0;
    crc = CRC8_TABLE[crc ^ test_id];
    crc = CRC8_TABLE[crc ^ len]; // 0
    pkt.push_back(crc);
    
    handler.feed_data(pkt);
    Packet out;
    ASSERT_TRUE(handler.parse_packet(out));
    EXPECT_EQ(out.id, test_id);
    EXPECT_EQ(out.payload.size(), 0);
}

// 3. 最大长度载荷 (255)
TEST_F(EdgeCaseTest, MaxLengthPayload) {
    uint8_t test_id = 0xEE;
    uint8_t len = 255;
    std::vector<uint8_t> data(255, 0xAB);
    
    std::vector<uint8_t> pkt;
    pkt.reserve(5 + data.size());
    pkt.push_back(FRAME_HEADER1);
    pkt.push_back(FRAME_HEADER2);
    pkt.push_back(test_id);
    pkt.push_back(len);
    pkt.insert(pkt.end(), data.begin(), data.end());
    
    uint8_t crc = 0;
    crc = CRC8_TABLE[crc ^ test_id];
    crc = CRC8_TABLE[crc ^ len]; 
    for(auto b : data) crc = CRC8_TABLE[crc ^ b];
    pkt.push_back(crc);
    
    handler.feed_data(pkt);
    Packet out;
    ASSERT_TRUE(handler.parse_packet(out));
    EXPECT_EQ(out.payload.size(), 255);
    EXPECT_EQ(out.payload[0], 0xAB);
    EXPECT_EQ(out.payload[254], 0xAB);
}

// 4. 损坏/部分数据包恢复
TEST_F(EdgeCaseTest, PartialThenValid) {
    // 场景: 帧头有效，但数据不再到来。随后一个新的有效包到达。
    // 处理器应当最终重新同步。
    
    uint8_t test_id = 0x01;
    
    // 数据包 1: 部分数据
    // 我们声明 len=5. Request size = 2+1+1+5+1 = 10 bytes.
    // 我们实际发送 6 bytes (part1) + 6 bytes (valid pkt) = 12 bytes.
    // 12 >= 10, 所以解析器会尝试解析, 发现CRC错误, 然后重同步.
    std::vector<uint8_t> part1 = {FRAME_HEADER1, FRAME_HEADER2, test_id, 5}; 
    // 发送 2 字节
    part1.push_back(0x01);
    part1.push_back(0x02);
    
    // 投喂部分数据
    handler.feed_data(part1);
    
    Packet out;
    EXPECT_FALSE(handler.parse_packet(out));
    
    // 数据包 2: 完整有效
    // ID=1 (Heartbeat), len 1
    Packet_Heartbeat data = {100};
    std::vector<uint8_t> valid_pkt = handler.pack(PACKET_ID_HEARTBEAT, data);
    
    handler.feed_data(valid_pkt);
    
    // 同步逻辑:
    // 处理器将首先尝试解析部分数据包，CRC 失败，然后滑动窗口直到找到有效数据包。
    
    ASSERT_TRUE(handler.parse_packet(out));
    EXPECT_EQ(out.id, PACKET_ID_HEARTBEAT);
}

// 5. 环形缓冲区回绕
TEST_F(EdgeCaseTest, RingBufferWrapAround) {
    // 1. 填充缓冲区至接近末尾 (大小为 4096)
    // 留出 2 字节空间。
    size_t fill_size = 4096 - 2; 
    std::vector<uint8_t> dummy(fill_size, 0x00);
    handler.feed_data(dummy);
    
    // 清空 dummy 数据以推进指针
    Packet out;
    // 排空缓冲区。因为数据全为 0x00，parse_packet 检查帧头后滑动。
    // 它将消耗所有数据。
    while(handler.data_available() > 0) {
        if(!handler.parse_packet(out)) {
             // If parse fails but data still there, it means 'not enough for packet' or 'scanning'.
             // We need to force consume if stuck?
             // Actually parse_packet(out) consumes 1 byte if header check fails. 
             // So calling it N times is enough.
             // But simpler: just trust parse_packet loop.
             // 如果 data_available < 5, parse_packet 立即返回 false。
             // 所以当剩余小于 5 字节时停止。
             break;
        }
    }
    
    // 2. 投喂一个长于 2 字节的有效数据包。
    // 它将分割: 一部分在数组末尾，一部分在数组开头。
    // Handshake: 4 bytes payload, total 4(Head+ID+Len)+4(Data)+1(CRC) = 9 bytes
    Packet_Handshake data = {0x12345678};
    std::vector<uint8_t> split_pkt = handler.pack(PACKET_ID_HANDSHAKE, data);
    
    handler.feed_data(split_pkt);
    
    // 3. 解析。逻辑必须处理内存不连续性。
    ASSERT_TRUE(handler.parse_packet(out));
    EXPECT_EQ(out.id, PACKET_ID_HANDSHAKE);
    
    // 验证载荷数据完整性
    Packet_Handshake payload = out.as<Packet_Handshake>();
    EXPECT_EQ(payload.protocol_hash, 0x12345678);
}

// 粘包测试
TEST_F(EdgeCaseTest, StickyPackets) {
    // 创建 3 个拼接在一起的数据包
    std::vector<uint8_t> stream;
    
    // Packet 1: Heartbeat
    auto pkt1 = handler.pack(PACKET_ID_HEARTBEAT, Packet_Heartbeat{10});
    
    // Packet 2: Heartbeat with different value
    Packet_Heartbeat data = {15};
    auto pkt2 = handler.pack(PACKET_ID_HEARTBEAT, data);
    
    // Packet 3: Heartbeat
    auto pkt3 = handler.pack(PACKET_ID_HEARTBEAT, Packet_Heartbeat{20});
    
    stream.insert(stream.end(), pkt1.begin(), pkt1.end());
    stream.insert(stream.end(), pkt2.begin(), pkt2.end());
    stream.insert(stream.end(), pkt3.begin(), pkt3.end());
    
    // 一次性投喂所有
    handler.feed_data(stream);
    
    Packet out;
    // 应该提取第 1 个
    ASSERT_TRUE(handler.parse_packet(out));
    EXPECT_EQ(out.id, PACKET_ID_HEARTBEAT);
    EXPECT_EQ(out.as<Packet_Heartbeat>().count, 10);
    
    // 应该提取第 2 个
    ASSERT_TRUE(handler.parse_packet(out));
    EXPECT_EQ(out.id, PACKET_ID_HEARTBEAT);
    EXPECT_EQ(out.as<Packet_Heartbeat>().count, 15);
    
    // 应该提取第 3 个
    ASSERT_TRUE(handler.parse_packet(out));
    EXPECT_EQ(out.id, PACKET_ID_HEARTBEAT);
    EXPECT_EQ(out.as<Packet_Heartbeat>().count, 20);
    
    // 缓冲区应为空
    EXPECT_FALSE(handler.parse_packet(out));
}

// 7. 逐字节分片测试
TEST_F(EdgeCaseTest, ByteByByteFeed) {
    Packet_Heartbeat data = {55};
    std::vector<uint8_t> pkt = handler.pack(PACKET_ID_HEARTBEAT, data);
    
    Packet out;
    
    // 逐字节投喂并尝试解析
    for (size_t i = 0; i < pkt.size(); ++i) {
        std::vector<uint8_t> single_byte = {pkt[i]};
        handler.feed_data(single_byte);
        
        if (i < pkt.size() - 1) {
            // 还不应就绪
            EXPECT_FALSE(handler.parse_packet(out));
        } else {
            // 最后一个字节到达，应就绪
            ASSERT_TRUE(handler.parse_packet(out));
        }
    }
    
    Packet_Heartbeat payload = out.as<Packet_Heartbeat>();
    EXPECT_EQ(payload.count, 55);
}

// 8. 伪帧头混淆测试
TEST_F(EdgeCaseTest, FalseHeaderSequence) {
    // 1. 看起来像帧头的噪音: 0x5A, 0xA5, 但后续 CRC 错误
    //    或者帧结构有效但 CRC 校验稍后失败。
    std::vector<uint8_t> noise = {(uint8_t)FRAME_HEADER1, (uint8_t)FRAME_HEADER2, 0x01, 0x01, 0xFF, 0xFF}; // CRC 错误
    
    // 2. 真实数据包
    auto real_pkt = handler.pack(PACKET_ID_HEARTBEAT, Packet_Heartbeat{99});
    
    // 组合
    std::vector<uint8_t> stream = noise;
    stream.insert(stream.end(), real_pkt.begin(), real_pkt.end());
    
    handler.feed_data(stream);
    
    Packet out;
    // 解析器应该拒绝噪音 (CRC 不匹配) 并丢弃第一个 '0x5A',
    // 然后重新扫描，最终找到真实数据包。
    ASSERT_TRUE(handler.parse_packet(out));
    EXPECT_EQ(out.id, PACKET_ID_HEARTBEAT);
    
    // 确保我们消耗了正确的那一个
    EXPECT_EQ(out.as<Packet_Heartbeat>().count, 99);
}

// 9. 缓冲区溢出 / 覆盖
TEST_F(EdgeCaseTest, BufferOverflow) {
    // 假设缓冲区大小为 4096。
    // 投喂 5000 字节的垃圾数据。
    std::vector<uint8_t> heavy_load(5000, 0xFF);
    handler.feed_data(heavy_load);
    
    // 然后投喂一个有效数据包
    auto pkt = handler.pack(PACKET_ID_HEARTBEAT, Packet_Heartbeat{1});
    handler.feed_data(pkt);
    
    Packet out;
    // 取决于具体实现:
    // 情况 A (丢弃新数据): 你将找不到该数据包。
    // 情况 B (覆盖旧数据): 你将找到该数据包 (因为它在末尾)。
    
    // 当前实现在溢出时丢弃新数据。
    EXPECT_FALSE(handler.parse_packet(out));
}
