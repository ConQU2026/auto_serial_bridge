#pragma once
#include <cstdint>
#include <cstddef>

#include "protocol.h"

namespace auto_serial_bridge {
namespace config {

    constexpr const char * DEFAULT_PORT = "/dev/stm32";
    constexpr uint32_t DEFAULT_BAUDRATE = 115200;
    constexpr size_t BUFFER_SIZE = 1024;
    constexpr uint8_t CFG_FRAME_HEADER1 = 90;
    constexpr uint8_t CFG_FRAME_HEADER2 = 165;

    enum class ChecksumAlgo { NONE, SUM8, XOR8, CRC8 };
    constexpr ChecksumAlgo CHECKSUM_ALGO = ChecksumAlgo::CRC8;

    constexpr bool REQUIRE_HANDSHAKE = false;
    constexpr bool IGNORE_VERSION_MISMATCH = true;
    constexpr bool ENABLE_HEARTBEAT = true;
    constexpr bool STRICT_HEARTBEAT = true;
    constexpr size_t QOS_DEPTH = 10;
    constexpr int HEARTBEAT_TIMEOUT_MS = 10000;
    constexpr int RELIABLE_RETRY_INTERVAL_MS = 100;
    constexpr int RELIABLE_MAX_RETRIES = 3;
    constexpr size_t MAX_PACKET_PAYLOAD_SIZE = 17;

    inline constexpr size_t expected_payload_size(PacketID id) {
        switch (id) {
            case PACKET_ID_ACK: return sizeof(Packet_Ack);
            case PACKET_ID_HEARTBEAT: return sizeof(Packet_Heartbeat);
            case PACKET_ID_HANDSHAKE: return sizeof(Packet_Handshake);
            case PACKET_ID_CMDVEL: return sizeof(Packet_CmdVel);
            case PACKET_ID_WEAPONDOCKFINEYVELOCITY: return sizeof(Packet_WeaponDockFineYVelocity) + 1;
            case PACKET_ID_WEAPONDOCKERROR: return sizeof(Packet_WeaponDockError) + 1;
            case PACKET_ID_MERLINPICKGOAL: return sizeof(Packet_MerlinPickGoal) + 1;
            case PACKET_ID_GRIDPLACEGOAL: return sizeof(Packet_GridPlaceGoal) + 1;
            case PACKET_ID_GRIDATTACKGOAL: return sizeof(Packet_GridAttackGoal) + 1;
            case PACKET_ID_GENERICSTATUSTX: return sizeof(Packet_GenericStatusTx) + 1;
            case PACKET_ID_GENERICSTATUSRX: return sizeof(Packet_GenericStatusRx);
            default: return 0;
        }
    }

    inline constexpr bool is_reliable_packet(PacketID id) {
        switch (id) {
            case PACKET_ID_ACK: return false;
            case PACKET_ID_HEARTBEAT: return false;
            case PACKET_ID_HANDSHAKE: return false;
            case PACKET_ID_CMDVEL: return false;
            case PACKET_ID_WEAPONDOCKFINEYVELOCITY: return true;
            case PACKET_ID_WEAPONDOCKERROR: return true;
            case PACKET_ID_MERLINPICKGOAL: return true;
            case PACKET_ID_GRIDPLACEGOAL: return true;
            case PACKET_ID_GRIDATTACKGOAL: return true;
            case PACKET_ID_GENERICSTATUSTX: return true;
            case PACKET_ID_GENERICSTATUSRX: return false;
            default: return false;
        }
    }

    inline constexpr bool is_debug_log_enabled(PacketID id) {
        switch (id) {
            case PACKET_ID_ACK: return false;
            case PACKET_ID_HEARTBEAT: return false;
            case PACKET_ID_HANDSHAKE: return true;
            case PACKET_ID_CMDVEL: return false;
            case PACKET_ID_WEAPONDOCKFINEYVELOCITY: return true;
            case PACKET_ID_WEAPONDOCKERROR: return true;
            case PACKET_ID_MERLINPICKGOAL: return true;
            case PACKET_ID_GRIDPLACEGOAL: return true;
            case PACKET_ID_GRIDATTACKGOAL: return true;
            case PACKET_ID_GENERICSTATUSTX: return true;
            case PACKET_ID_GENERICSTATUSRX: return true;
            default: return true;
        }
    }

}
}
