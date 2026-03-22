#include <gtest/gtest.h>
#include "protocol.h"
#include "auto_serial_bridge/generated_config.hpp"

// 验证生成的 C 头文件是否有效并符合预期

TEST(ProtocolStructureTest, StructPacking) {
    // Handshake: u32 (4 字节)
    EXPECT_EQ(sizeof(Packet_Handshake), 4);
    
    // Heartbeat: u32 (4 字节)
    EXPECT_EQ(sizeof(Packet_Heartbeat), 4);
}

TEST(ProtocolStructureTest, Constants) {
    // 帧头 (验证 C 宏定义与 C++ 配置常量一致)
    EXPECT_EQ(FRAME_HEADER1, auto_serial_bridge::config::CFG_FRAME_HEADER1);
    EXPECT_EQ(FRAME_HEADER2, auto_serial_bridge::config::CFG_FRAME_HEADER2);
    
    // 哈希应该已定义
    EXPECT_NE(PROTOCOL_HASH, 0);
}

#ifdef CHECKSUM_ALGO_CRC8
TEST(ProtocolStructureTest, CRCTableCheck) {
    // 抽查标准 CRC8-MAXIM 多项式 (0x31) 的几个值
    // 0 -> 0x00
    EXPECT_EQ(CRC8_TABLE[0], 0x00);
    // 1 -> 0x31
    EXPECT_EQ(CRC8_TABLE[1], 0x31);
    // 255 -> 0xAC
    EXPECT_EQ(CRC8_TABLE[255], 0xAC); 
}
#endif
