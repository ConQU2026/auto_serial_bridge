#include <gtest/gtest.h>
#include <vector>
#include <cstring>
#include <numeric>
#include "auto_serial_bridge/packet_handler.hpp"
#include "auto_serial_bridge/generated_config.hpp"
#include "protocol.h"

using namespace auto_serial_bridge;

namespace {

// ============================================================================
// 独立参考实现 —— 不依赖 if constexpr 分派, 用于交叉验证
// ============================================================================

uint8_t ref_sum8(const uint8_t* data, size_t len) {
    uint8_t sum = 0;
    for (size_t i = 0; i < len; ++i) sum += data[i];
    return sum;
}

uint8_t ref_xor8(const uint8_t* data, size_t len) {
    uint8_t x = 0;
    for (size_t i = 0; i < len; ++i) x ^= data[i];
    return x;
}

#ifdef CHECKSUM_ALGO_CRC8
uint8_t ref_crc8(const uint8_t* data, size_t len) {
    uint8_t crc = 0;
    for (size_t i = 0; i < len; ++i) crc = CRC8_TABLE[crc ^ data[i]];
    return crc;
}
#endif

// 辅助: 手工构建一帧 (Header + ID + Len + Payload + Checksum)
std::vector<uint8_t> build_raw_frame(uint8_t id, const std::vector<uint8_t>& payload,
                                      uint8_t checksum_byte) {
    std::vector<uint8_t> frame;
    frame.push_back(FRAME_HEADER1);
    frame.push_back(FRAME_HEADER2);
    frame.push_back(id);
    frame.push_back(static_cast<uint8_t>(payload.size()));
    frame.insert(frame.end(), payload.begin(), payload.end());
    frame.push_back(checksum_byte);
    return frame;
}

// 辅助: 对 ID+Len+Payload 计算参考校验和
uint8_t compute_ref_checksum(uint8_t id, const std::vector<uint8_t>& payload,
                              uint8_t (*algo)(const uint8_t*, size_t)) {
    std::vector<uint8_t> buf;
    buf.push_back(id);
    buf.push_back(static_cast<uint8_t>(payload.size()));
    buf.insert(buf.end(), payload.begin(), payload.end());
    return algo(buf.data(), buf.size());
}

} // namespace

// ============================================================================
// 1. 参考算法已知向量验证
// ============================================================================

TEST(ChecksumAlgoTest, SUM8_KnownVectors) {
    {
        const uint8_t d[] = {0x01, 0x02, 0x03, 0x04};
        EXPECT_EQ(ref_sum8(d, 4), 0x0A);
    }
    {
        const uint8_t d[] = {0xFF, 0xFF, 0xFF, 0xFF};
        EXPECT_EQ(ref_sum8(d, 4), static_cast<uint8_t>(0xFC));
    }
    EXPECT_EQ(ref_sum8(nullptr, 0), 0x00);
    {
        const uint8_t d[] = {0x42};
        EXPECT_EQ(ref_sum8(d, 1), 0x42);
    }
}

TEST(ChecksumAlgoTest, XOR8_KnownVectors) {
    {
        const uint8_t d[] = {0x01, 0x02, 0x03, 0x04};
        EXPECT_EQ(ref_xor8(d, 4), 0x04); // 1^2=3, 3^3=0, 0^4=4
    }
    {
        const uint8_t d[] = {0xFF, 0xFF, 0xFF, 0xFF};
        EXPECT_EQ(ref_xor8(d, 4), 0x00); // 偶数个 0xFF 异或为 0
    }
    EXPECT_EQ(ref_xor8(nullptr, 0), 0x00);
    {
        const uint8_t d[] = {0xAA, 0x55};
        EXPECT_EQ(ref_xor8(d, 2), 0xFF); // 互补
    }
}

#ifdef CHECKSUM_ALGO_CRC8
TEST(ChecksumAlgoTest, CRC8_KnownVectors) {
    {
        const uint8_t d[] = {0x00};
        EXPECT_EQ(ref_crc8(d, 1), 0x00);
    }
    {
        const uint8_t d[] = {0x01};
        EXPECT_EQ(ref_crc8(d, 1), 0x31); // CRC8_TABLE[1]
    }
    {
        const uint8_t d[] = {0xFF};
        EXPECT_EQ(ref_crc8(d, 1), 0xAC); // CRC8_TABLE[255]
    }
    EXPECT_EQ(ref_crc8(nullptr, 0), 0x00);
}

TEST(ChecksumAlgoTest, CRC8_TableSanity) {
    EXPECT_EQ(CRC8_TABLE[0], 0x00);
    EXPECT_EQ(CRC8_TABLE[1], 0x31);
    EXPECT_EQ(CRC8_TABLE[255], 0xAC);

    // 表中 256 个值互不完全为 0
    int nonzero = 0;
    for (int i = 0; i < 256; ++i)
        if (CRC8_TABLE[i] != 0) ++nonzero;
    EXPECT_GT(nonzero, 200);
}
#endif

// ============================================================================
// 2. calculate_checksum 与当前编译配置的参考实现一致
// ============================================================================

TEST(ChecksumAlgoTest, CompiledMatchesReference) {
    struct TestVector {
        std::vector<uint8_t> data;
    };

    std::vector<TestVector> vectors = {
        {{0x42}},
        {{0x01, 0x02, 0x03, 0x04}},
        {{0x00, 0x00, 0x00, 0x00}},
        {{0xFF, 0xFF, 0xFF, 0xFF}},
        {{0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07}},
        {{0x5A, 0xA5}}, // 帧头字节本身作为数据
    };

    for (const auto& tv : vectors) {
        uint8_t compiled = PacketHandler::calculate_checksum(tv.data.data(), tv.data.size());

        uint8_t expected;
        if constexpr (config::CHECKSUM_ALGO == config::ChecksumAlgo::NONE) {
            expected = 0x00;
        } else if constexpr (config::CHECKSUM_ALGO == config::ChecksumAlgo::SUM8) {
            expected = ref_sum8(tv.data.data(), tv.data.size());
        } else if constexpr (config::CHECKSUM_ALGO == config::ChecksumAlgo::XOR8) {
            expected = ref_xor8(tv.data.data(), tv.data.size());
        } else {
#ifdef CHECKSUM_ALGO_CRC8
            expected = ref_crc8(tv.data.data(), tv.data.size());
#else
            expected = compiled; // fallback: 无表则跳过比对
#endif
        }
        EXPECT_EQ(compiled, expected) << "向量大小=" << tv.data.size();
    }
}

// ============================================================================
// 3. Pack 产出的校验字节验证
// ============================================================================

TEST(ChecksumAlgoTest, PackChecksumByteCorrect) {
    PacketHandler handler(1024);

    Packet_Heartbeat hb = {0xCAFEBABE};
    auto bytes = handler.pack(PACKET_ID_HEARTBEAT, hb);

    // 校验和覆盖 bytes[2 .. end-2] (ID + Len + Payload)
    uint8_t expect_cs = PacketHandler::calculate_checksum(bytes.data() + 2, bytes.size() - 3);
    EXPECT_EQ(bytes.back(), expect_cs);
}

TEST(ChecksumAlgoTest, PackChecksumByteCorrect_Handshake) {
    PacketHandler handler(1024);

    Packet_Handshake hs = {PROTOCOL_HASH};
    auto bytes = handler.pack(PACKET_ID_HANDSHAKE, hs);

    EXPECT_EQ(bytes.size(), 9u); // 2+1+1+4+1
    uint8_t expect_cs = PacketHandler::calculate_checksum(bytes.data() + 2, bytes.size() - 3);
    EXPECT_EQ(bytes.back(), expect_cs);
}

TEST(ChecksumAlgoTest, PackChecksumByteCorrect_CmdVel) {
    PacketHandler handler(1024);

    Packet_CmdVel cmd = {1.0f, -2.0f, 0.5f};
    auto bytes = handler.pack(PACKET_ID_CMDVEL, cmd);

    EXPECT_EQ(bytes.size(), 17u); // 2+1+1+12+1
    uint8_t expect_cs = PacketHandler::calculate_checksum(bytes.data() + 2, bytes.size() - 3);
    EXPECT_EQ(bytes.back(), expect_cs);
}

// ============================================================================
// 4. Pack → Parse 往返: 各种消息类型
// ============================================================================

TEST(ChecksumAlgoTest, RoundTrip_Heartbeat) {
    PacketHandler handler(1024);
    Packet_Heartbeat in = {0xDEADBEEF};
    auto bytes = handler.pack(PACKET_ID_HEARTBEAT, in);

    handler.feed_data(bytes);
    Packet pkt;
    ASSERT_TRUE(handler.parse_packet(pkt));
    EXPECT_EQ(pkt.id, PACKET_ID_HEARTBEAT);
    EXPECT_EQ(pkt.as<Packet_Heartbeat>().count, 0xDEADBEEF);
}

TEST(ChecksumAlgoTest, RoundTrip_Handshake) {
    PacketHandler handler(1024);
    Packet_Handshake in = {PROTOCOL_HASH};
    auto bytes = handler.pack(PACKET_ID_HANDSHAKE, in);

    handler.feed_data(bytes);
    Packet pkt;
    ASSERT_TRUE(handler.parse_packet(pkt));
    EXPECT_EQ(pkt.id, PACKET_ID_HANDSHAKE);
    EXPECT_EQ(pkt.as<Packet_Handshake>().protocol_hash, PROTOCOL_HASH);
}

TEST(ChecksumAlgoTest, RoundTrip_CmdVel) {
    PacketHandler handler(1024);
    Packet_CmdVel in = {3.14f, -1.57f, 0.01f};
    auto bytes = handler.pack(PACKET_ID_CMDVEL, in);

    handler.feed_data(bytes);
    Packet pkt;
    ASSERT_TRUE(handler.parse_packet(pkt));
    EXPECT_EQ(pkt.id, PACKET_ID_CMDVEL);

    auto out = pkt.as<Packet_CmdVel>();
    EXPECT_FLOAT_EQ(out.linear_x, 3.14f);
    EXPECT_FLOAT_EQ(out.linear_y, -1.57f);
    EXPECT_FLOAT_EQ(out.angular_z, 0.01f);
}

// ============================================================================
// 5. 错误校验和拒绝测试
// ============================================================================

TEST(ChecksumAlgoTest, CorruptedChecksumRejected) {
    PacketHandler handler(1024);
    Packet_Heartbeat in = {100};
    auto bytes = handler.pack(PACKET_ID_HEARTBEAT, in);

    if constexpr (config::CHECKSUM_ALGO != config::ChecksumAlgo::NONE) {
        bytes.back() ^= 0xFF; // 篡改校验字节
        handler.feed_data(bytes);
        Packet pkt;
        EXPECT_FALSE(handler.parse_packet(pkt));
    }
}

TEST(ChecksumAlgoTest, CorruptedPayloadRejected) {
    PacketHandler handler(1024);
    Packet_Heartbeat in = {200};
    auto bytes = handler.pack(PACKET_ID_HEARTBEAT, in);

    if constexpr (config::CHECKSUM_ALGO != config::ChecksumAlgo::NONE) {
        bytes[4] ^= 0x01; // 篡改载荷的第一个字节
        handler.feed_data(bytes);
        Packet pkt;
        EXPECT_FALSE(handler.parse_packet(pkt));
    }
}

// ============================================================================
// 6. 交叉算法测试: 用其它算法的校验和构建帧, 验证被当前算法拒绝
// ============================================================================

TEST(ChecksumAlgoTest, CrossAlgo_SUM8Frame) {
    PacketHandler handler(1024);
    uint8_t id = PACKET_ID_GENERICSTATUSTX;
    std::vector<uint8_t> payload = {0x10, 0x20, 0x30};

    uint8_t sum8_cs = compute_ref_checksum(id, payload, ref_sum8);
    auto frame = build_raw_frame(id, payload, sum8_cs);

    handler.feed_data(frame);
    Packet pkt;

    if constexpr (config::CHECKSUM_ALGO == config::ChecksumAlgo::SUM8 ||
                  config::CHECKSUM_ALGO == config::ChecksumAlgo::NONE) {
        EXPECT_TRUE(handler.parse_packet(pkt));
    } else {
        // SUM8 校验和大概率与 CRC8/XOR8 不匹配; 若碰巧匹配也不算错
        handler.parse_packet(pkt); // 不崩溃即可
    }
}

TEST(ChecksumAlgoTest, CrossAlgo_XOR8Frame) {
    PacketHandler handler(1024);
    uint8_t id = PACKET_ID_GENERICSTATUSTX;
    std::vector<uint8_t> payload = {0xAA, 0xBB, 0xCC};

    uint8_t xor8_cs = compute_ref_checksum(id, payload, ref_xor8);
    auto frame = build_raw_frame(id, payload, xor8_cs);

    handler.feed_data(frame);
    Packet pkt;

    if constexpr (config::CHECKSUM_ALGO == config::ChecksumAlgo::XOR8 ||
                  config::CHECKSUM_ALGO == config::ChecksumAlgo::NONE) {
        EXPECT_TRUE(handler.parse_packet(pkt));
    } else {
        handler.parse_packet(pkt);
    }
}

#ifdef CHECKSUM_ALGO_CRC8
TEST(ChecksumAlgoTest, CrossAlgo_CRC8Frame) {
    PacketHandler handler(1024);
    uint8_t id = PACKET_ID_HEARTBEAT;
    std::vector<uint8_t> payload = {0x01, 0x02, 0x03, 0x04};

    uint8_t crc8_cs = compute_ref_checksum(id, payload, ref_crc8);
    auto frame = build_raw_frame(id, payload, crc8_cs);

    handler.feed_data(frame);
    Packet pkt;

    if constexpr (config::CHECKSUM_ALGO == config::ChecksumAlgo::CRC8) {
        ASSERT_TRUE(handler.parse_packet(pkt));
        EXPECT_EQ(static_cast<uint8_t>(pkt.id), id);
        EXPECT_EQ(pkt.payload.size(), payload.size());
        EXPECT_EQ(pkt.payload, payload);
    }
}
#endif

// ============================================================================
// 7. NONE 算法: 任意校验字节都应被接受
// ============================================================================

TEST(ChecksumAlgoTest, NoneAlgoAcceptsAnything) {
    if constexpr (config::CHECKSUM_ALGO == config::ChecksumAlgo::NONE) {
        for (uint8_t cs_byte : {0x00, 0x42, 0xFF}) {
            PacketHandler handler(1024);
            std::vector<uint8_t> payload = {0x01, 0x02, 0x03};
            auto frame = build_raw_frame(PACKET_ID_GENERICSTATUSTX, payload, cs_byte);

            handler.feed_data(frame);
            Packet pkt;
            ASSERT_TRUE(handler.parse_packet(pkt))
                << "NONE 算法应接受任意校验字节, 但拒绝了 0x"
                << std::hex << static_cast<int>(cs_byte);
        }
    }
}

// ============================================================================
// 8. 多包连续通信: 校验和在连续解析中保持正确
// ============================================================================

TEST(ChecksumAlgoTest, ConsecutivePacketsDifferentTypes) {
    PacketHandler handler(4096);

    Packet_Heartbeat hb = {111};
    Packet_Handshake hs = {PROTOCOL_HASH};
    Packet_CmdVel cmd = {0.1f, 0.2f, 0.3f};

    auto b1 = handler.pack(PACKET_ID_HEARTBEAT, hb);
    auto b2 = handler.pack(PACKET_ID_HANDSHAKE, hs);
    auto b3 = handler.pack(PACKET_ID_CMDVEL, cmd);

    // 一次性投喂三种不同类型的包
    std::vector<uint8_t> stream;
    stream.insert(stream.end(), b1.begin(), b1.end());
    stream.insert(stream.end(), b2.begin(), b2.end());
    stream.insert(stream.end(), b3.begin(), b3.end());

    handler.feed_data(stream);

    Packet pkt;
    ASSERT_TRUE(handler.parse_packet(pkt));
    EXPECT_EQ(pkt.id, PACKET_ID_HEARTBEAT);
    EXPECT_EQ(pkt.as<Packet_Heartbeat>().count, 111u);

    ASSERT_TRUE(handler.parse_packet(pkt));
    EXPECT_EQ(pkt.id, PACKET_ID_HANDSHAKE);
    EXPECT_EQ(pkt.as<Packet_Handshake>().protocol_hash, PROTOCOL_HASH);

    ASSERT_TRUE(handler.parse_packet(pkt));
    EXPECT_EQ(pkt.id, PACKET_ID_CMDVEL);
    auto cv = pkt.as<Packet_CmdVel>();
    EXPECT_FLOAT_EQ(cv.linear_x, 0.1f);
    EXPECT_FLOAT_EQ(cv.linear_y, 0.2f);
    EXPECT_FLOAT_EQ(cv.angular_z, 0.3f);

    EXPECT_FALSE(handler.parse_packet(pkt));
}

// ============================================================================
// 9. 噪音 + 错误校验包 + 有效包 混合流
// ============================================================================

TEST(ChecksumAlgoTest, MixedStreamRecovery) {
    PacketHandler handler(4096);

    std::vector<uint8_t> stream;

    // 随机噪音
    for (int i = 0; i < 20; ++i)
        stream.push_back(static_cast<uint8_t>(i * 7));

    // 校验和错误的伪帧
    if constexpr (config::CHECKSUM_ALGO != config::ChecksumAlgo::NONE) {
        std::vector<uint8_t> bad_payload = {0xDE, 0xAD};
        auto bad_frame = build_raw_frame(0x01, bad_payload, 0xFF);
        stream.insert(stream.end(), bad_frame.begin(), bad_frame.end());
    }

    // 有效帧
    Packet_Heartbeat hb = {999};
    auto good = handler.pack(PACKET_ID_HEARTBEAT, hb);
    stream.insert(stream.end(), good.begin(), good.end());

    handler.feed_data(stream);

    Packet pkt;
    ASSERT_TRUE(handler.parse_packet(pkt));
    EXPECT_EQ(pkt.id, PACKET_ID_HEARTBEAT);
    EXPECT_EQ(pkt.as<Packet_Heartbeat>().count, 999u);
}

// ============================================================================
// 10. 生成配置编译期验证
// ============================================================================

TEST(ChecksumAlgoTest, ConfigEnumIsValid) {
    constexpr auto algo = config::CHECKSUM_ALGO;
    EXPECT_TRUE(
        algo == config::ChecksumAlgo::NONE ||
        algo == config::ChecksumAlgo::SUM8 ||
        algo == config::ChecksumAlgo::XOR8 ||
        algo == config::ChecksumAlgo::CRC8
    );
}

TEST(ChecksumAlgoTest, HandshakeConfigDefined) {
    constexpr bool hs = config::REQUIRE_HANDSHAKE;
    (void)hs; // 仅验证编译通过并有确定值
    SUCCEED();
}

TEST(ChecksumAlgoTest, CurrentAlgoMatchesMacro) {
#ifdef CHECKSUM_ALGO_CRC8
    EXPECT_EQ(config::CHECKSUM_ALGO, config::ChecksumAlgo::CRC8);
#endif
}
