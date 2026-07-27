#pragma once

#include <cstring>

#include <rmw/rmw.h>

namespace auto_serial_bridge
{

inline bool publisher_gid_matches(
  const rmw_gid_t & local_publisher_gid,
  const rmw_gid_t & incoming_publisher_gid)
{
  if (
    local_publisher_gid.implementation_identifier == nullptr ||
    incoming_publisher_gid.implementation_identifier == nullptr)
  {
    return false;
  }

  return std::strcmp(
           local_publisher_gid.implementation_identifier,
           incoming_publisher_gid.implementation_identifier) == 0 &&
         std::memcmp(
           local_publisher_gid.data,
           incoming_publisher_gid.data,
           sizeof(local_publisher_gid.data)) == 0;
}

inline bool should_skip_loopback_delivery(
  const rmw_gid_t & local_publisher_gid,
  const rmw_gid_t & incoming_publisher_gid,
  bool from_intra_process)
{
  if (publisher_gid_matches(local_publisher_gid, incoming_publisher_gid)) {
    return true;
  }

  // Humble intra-process callbacks currently zero publisher_gid, so this flag
  // remains the only stable signal for self-published mirrored traffic.
  return from_intra_process;
}

}  // namespace auto_serial_bridge
