#include <gtest/gtest.h>
#include <vector>
#include "auto_serial_bridge/packet_handler.hpp"
#include "protocol.h"

using namespace auto_serial_bridge;

namespace {

std::vector<uint8_t> build_raw_frame(uint8_t id, const std::vector<uint8_t>& payload) {
    std::vector<uint8_t> frame = {
        FRAME_HEADER1,
        FRAME_HEADER2,
        id,
        static_cast<uint8_t>(payload.size()),
    };
    frame.insert(frame.end(), payload.begin(), payload.end());

    std::vector<uint8_t> checksum_payload = {id, static_cast<uint8_t>(payload.size())};
    checksum_payload.insert(checksum_payload.end(), payload.begin(), payload.end());
    frame.push_back(PacketHandler::calculate_checksum(checksum_payload.data(), checksum_payload.size()));
    return frame;
}

}  // namespace

class EdgeCaseTest : public ::testing::Test {
protected:
    PacketHandler handler{4096};
};

// 1. 未知 ID 应被拒绝，并且不会阻塞后续重同步
TEST_F(EdgeCaseTest, UnknownIDIsRejectedAndResynced) {
    uint8_t unknown_id = 0x7F;
    std::vector<uint8_t> data = {0x01, 0x02};
    std::vector<uint8_t> pkt = build_raw_frame(unknown_id, data);
    std::vector<uint8_t> valid_pkt = handler.pack(PACKET_ID_HEARTBEAT, Packet_Heartbeat{100});
    
    handler.feed_data(pkt);
    handler.feed_data(valid_pkt);
    Packet out;
    
    ASSERT_TRUE(handler.parse_packet(out));
    EXPECT_EQ(out.id, PACKET_ID_HEARTBEAT);
    EXPECT_EQ(out.as<Packet_Heartbeat>().count, 100u);
}

// 2. 零长度载荷应被拒绝，并且不会阻塞后续重同步
TEST_F(EdgeCaseTest, ZeroLengthPayloadIsRejectedAndResynced) {
    uint8_t test_id = 0xEE;
    std::vector<uint8_t> pkt = build_raw_frame(test_id, {});
    std::vector<uint8_t> valid_pkt = handler.pack(PACKET_ID_HEARTBEAT, Packet_Heartbeat{101});
    
    handler.feed_data(pkt);
    handler.feed_data(valid_pkt);
    Packet out;
    ASSERT_TRUE(handler.parse_packet(out));
    EXPECT_EQ(out.id, PACKET_ID_HEARTBEAT);
    EXPECT_EQ(out.as<Packet_Heartbeat>().count, 101u);
}

// 3. 超过协议最大长度的载荷应被拒绝，并且不会阻塞后续重同步
TEST_F(EdgeCaseTest, PayloadLongerThanProtocolMaximumIsRejectedAndResynced) {
    uint8_t test_id = 0xEE;
    std::vector<uint8_t> data(255, 0xAB);
    std::vector<uint8_t> pkt = build_raw_frame(test_id, data);
    std::vector<uint8_t> valid_pkt = handler.pack(PACKET_ID_HEARTBEAT, Packet_Heartbeat{102});
    
    handler.feed_data(pkt);
    handler.feed_data(valid_pkt);
    Packet out;
    ASSERT_TRUE(handler.parse_packet(out));
    EXPECT_EQ(out.id, PACKET_ID_HEARTBEAT);
    EXPECT_EQ(out.as<Packet_Heartbeat>().count, 102u);
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
    
    if constexpr (config::CHECKSUM_ALGO == config::ChecksumAlgo::NONE) {
        GTEST_SKIP() << "NONE 算法不提供坏包检测，半包恢复语义不适用";
    } else {
        // 同步逻辑:
        // 处理器将首先尝试解析部分数据包，CRC 失败，然后滑动窗口直到找到有效数据包。
        ASSERT_TRUE(handler.parse_packet(out));
        EXPECT_EQ(out.id, PACKET_ID_HEARTBEAT);
    }
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
    const uint8_t noise_id = 0x01;
    const std::vector<uint8_t> noise_payload = {0xFF};
    std::vector<uint8_t> noise = {
        FRAME_HEADER1,
        FRAME_HEADER2,
        noise_id,
        static_cast<uint8_t>(noise_payload.size()),
        noise_payload.front(),
    };

    std::vector<uint8_t> checksum_payload = {
        noise_id,
        static_cast<uint8_t>(noise_payload.size()),
        noise_payload.front(),
    };
    uint8_t wrong_checksum = 0xFF;
    if constexpr (config::CHECKSUM_ALGO != config::ChecksumAlgo::NONE) {
        wrong_checksum = PacketHandler::calculate_checksum(
            checksum_payload.data(),
            checksum_payload.size()
        ) ^ 0xFF;
    }
    noise.push_back(wrong_checksum);
    
    // 2. 真实数据包
    auto real_pkt = handler.pack(PACKET_ID_HEARTBEAT, Packet_Heartbeat{99});
    
    // 组合
    std::vector<uint8_t> stream = noise;
    stream.insert(stream.end(), real_pkt.begin(), real_pkt.end());
    
    handler.feed_data(stream);
    
    Packet out;
    if constexpr (config::CHECKSUM_ALGO == config::ChecksumAlgo::NONE) {
        ASSERT_TRUE(handler.parse_packet(out));
        ASSERT_TRUE(handler.parse_packet(out));
    } else {
        // 解析器应该拒绝噪音 (CRC 不匹配) 并丢弃第一个 '0x5A',
        // 然后重新扫描，最终找到真实数据包。
        ASSERT_TRUE(handler.parse_packet(out));
    }

    EXPECT_EQ(out.id, PACKET_ID_HEARTBEAT);
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
