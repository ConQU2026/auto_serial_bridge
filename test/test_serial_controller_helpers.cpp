#include <gtest/gtest.h>

#include "auto_serial_bridge/serial_controller.hpp"

using auto_serial_bridge::detail::HandshakeValidationResult;
using auto_serial_bridge::detail::ReceiveFollowUpAction;
using auto_serial_bridge::detail::classify_handshake_validation;
using auto_serial_bridge::detail::classify_receive_result;
using auto_serial_bridge::detail::format_hash_pair;
using auto_serial_bridge::detail::format_hex_payload;
using auto_serial_bridge::detail::heartbeat_mode_name;
using auto_serial_bridge::detail::handshake_mode_name;

TEST(SerialControllerHelperTest, EmptyReadKeepsReceiveLoopAliveWhilePortIsOpen)
{
  EXPECT_EQ(
    classify_receive_result(0, true),
    ReceiveFollowUpAction::ContinueReading);
}

TEST(SerialControllerHelperTest, EmptyReadResetsConnectionWhenPortIsClosed)
{
  EXPECT_EQ(
    classify_receive_result(0, false),
    ReceiveFollowUpAction::ResetConnection);
}

TEST(SerialControllerHelperTest, PositiveReadContinuesReceiveLoop)
{
  EXPECT_EQ(
    classify_receive_result(8, true),
    ReceiveFollowUpAction::ContinueReading);
  EXPECT_EQ(
    classify_receive_result(8, false),
    ReceiveFollowUpAction::ContinueReading);
}

TEST(SerialControllerHelperTest, MatchingHandshakeIsAccepted)
{
  EXPECT_EQ(
    classify_handshake_validation(0x11223344U, 0x11223344U, false),
    HandshakeValidationResult::Matched);
  EXPECT_EQ(
    classify_handshake_validation(0x11223344U, 0x11223344U, true),
    HandshakeValidationResult::Matched);
}

TEST(SerialControllerHelperTest, MismatchCanBeAcceptedWhenIgnoreEnabled)
{
  EXPECT_EQ(
    classify_handshake_validation(0x11223344U, 0x55667788U, true),
    HandshakeValidationResult::IgnoredMismatch);
}

TEST(SerialControllerHelperTest, MismatchIsRejectedWhenIgnoreDisabled)
{
  EXPECT_EQ(
    classify_handshake_validation(0x11223344U, 0x55667788U, false),
    HandshakeValidationResult::RejectedMismatch);
}

TEST(SerialControllerHelperTest, FormatHashPairShowsLocalAndRemote)
{
  EXPECT_EQ(
    format_hash_pair(0x11223344U, 0x55667788U),
    "local=0x11223344, remote=0x55667788");
}

TEST(SerialControllerHelperTest, FormatHexPayloadHandlesEmptyAndTruncation)
{
  EXPECT_EQ(format_hex_payload(nullptr, 0), "(empty)");

  const uint8_t bytes[] = {0x5A, 0xA5, 0xFD, 0x04};
  EXPECT_EQ(format_hex_payload(bytes, sizeof(bytes), 2), "5A A5 ...(4 bytes)");
  EXPECT_EQ(format_hex_payload(bytes, sizeof(bytes), 8), "5A A5 FD 04");
}

TEST(SerialControllerHelperTest, HandshakeModeNameSummarizesSelection)
{
  EXPECT_STREQ(handshake_mode_name(false, false), "disabled");
  EXPECT_STREQ(handshake_mode_name(true, false), "strict");
  EXPECT_STREQ(handshake_mode_name(true, true), "ignore_mismatch");
}

TEST(SerialControllerHelperTest, HeartbeatModeNameSummarizesSelection)
{
  EXPECT_STREQ(heartbeat_mode_name(false, true), "disabled");
  EXPECT_STREQ(heartbeat_mode_name(true, true), "strict");
  EXPECT_STREQ(heartbeat_mode_name(true, false), "warn_only");
}
