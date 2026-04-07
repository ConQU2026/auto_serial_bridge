#pragma once
#include <cstdint>
#include <cstddef>

#include "protocol.h"

namespace auto_serial_bridge {
namespace config {

    constexpr uint32_t DEFAULT_BAUDRATE = 115200;
    constexpr size_t BUFFER_SIZE = 256;
    constexpr uint8_t CFG_FRAME_HEADER1 = 90;
    constexpr uint8_t CFG_FRAME_HEADER2 = 165;

    enum class ChecksumAlgo { NONE, SUM8, XOR8, CRC8 };
    constexpr ChecksumAlgo CHECKSUM_ALGO = ChecksumAlgo::CRC8;

    constexpr bool REQUIRE_HANDSHAKE = true;
    constexpr bool ENABLE_HEARTBEAT = true;
    constexpr size_t QOS_DEPTH = 10;
    constexpr int HEARTBEAT_TIMEOUT_MS = 10000;
    constexpr size_t MAX_PACKET_PAYLOAD_SIZE = 16;

    inline constexpr size_t expected_payload_size(PacketID id) {
        switch (id) {
            case PACKET_ID_HEARTBEAT: return sizeof(Packet_Heartbeat);
            case PACKET_ID_HANDSHAKE: return sizeof(Packet_Handshake);
            case PACKET_ID_CMDVEL: return sizeof(Packet_CmdVel);
            case PACKET_ID_GRIPPERCONTROLGOAL: return sizeof(Packet_GripperControlGoal);
            case PACKET_ID_WEAPONDOCKGOAL: return sizeof(Packet_WeaponDockGoal);
            case PACKET_ID_MLCONTROLTX: return sizeof(Packet_MlControlTx);
            case PACKET_ID_MERLINPICKGOAL: return sizeof(Packet_MerlinPickGoal);
            case PACKET_ID_GRIDPLACEGOAL: return sizeof(Packet_GridPlaceGoal);
            case PACKET_ID_GRIDATTACKGOAL: return sizeof(Packet_GridAttackGoal);
            case PACKET_ID_GENERICSTATUSTX: return sizeof(Packet_GenericStatusTx);
            case PACKET_ID_GENERICSTATUSRX: return sizeof(Packet_GenericStatusRx);
            default: return 0;
        }
    }

}
}
