#include <rmw/rmw.h>

#include "auto_serial_bridge/loopback_utils.hpp"

#include <gtest/gtest.h>

namespace auto_serial_bridge {
namespace {

rmw_gid_t make_gid(uint8_t seed)
{
  rmw_gid_t gid{};
  gid.implementation_identifier = "rmw_test";
  gid.data[0] = seed;
  gid.data[1] = static_cast<uint8_t>(seed + 1);
  gid.data[2] = static_cast<uint8_t>(seed + 2);
  return gid;
}

TEST(LoopbackUtilsTest, MatchesPublisherGidSkipsDelivery)
{
  const rmw_gid_t local_gid = make_gid(10);
  const rmw_gid_t incoming_gid = make_gid(10);

  EXPECT_TRUE(should_skip_loopback_delivery(local_gid, incoming_gid, false));
}

TEST(LoopbackUtilsTest, DifferentInterProcessPublisherDoesNotSkipDelivery)
{
  const rmw_gid_t local_gid = make_gid(10);
  const rmw_gid_t incoming_gid = make_gid(20);

  EXPECT_FALSE(should_skip_loopback_delivery(local_gid, incoming_gid, false));
}

TEST(LoopbackUtilsTest, IntraProcessDeliverySkipsWhenPublisherGidIsUnavailable)
{
  const rmw_gid_t local_gid = make_gid(10);
  const rmw_gid_t incoming_gid = make_gid(20);

  EXPECT_TRUE(should_skip_loopback_delivery(local_gid, incoming_gid, true));
}

}  // namespace
}  // namespace auto_serial_bridge
