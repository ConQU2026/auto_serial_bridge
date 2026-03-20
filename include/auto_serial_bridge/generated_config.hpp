#pragma once
#include <cstdint>
#include <cstddef>

namespace auto_serial_bridge {
namespace config {

    constexpr uint32_t DEFAULT_BAUDRATE = 115200;
    constexpr size_t BUFFER_SIZE = 256;
    constexpr uint8_t CFG_FRAME_HEADER1 = 90;
    constexpr uint8_t CFG_FRAME_HEADER2 = 165;

    enum class ChecksumAlgo { NONE, SUM8, XOR8, CRC8 };
    constexpr ChecksumAlgo CHECKSUM_ALGO = ChecksumAlgo::CRC8;

    constexpr bool REQUIRE_HANDSHAKE = true;
    constexpr size_t QOS_DEPTH = 10;
    constexpr int HEARTBEAT_TIMEOUT_MS = 3000;

}
}
