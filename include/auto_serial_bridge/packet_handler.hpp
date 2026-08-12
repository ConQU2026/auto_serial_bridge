#pragma once

#include "auto_serial_bridge/protocol.hpp"
#include "auto_serial_bridge/generated/generated_config.hpp"
#include <vector>
#include <cstdint>
#include <cstddef>

namespace auto_serial_bridge
{

  /**
   * @brief 数据包处理类
   *
   * 负责数据的校验、打包和解包。使用环形缓冲区解析接收字节流。
   * 容量向上取整为 2 的幂，索引计算使用位掩码。
   *
   * 线程模型: feed_data() / try_push_byte() / parse_packet() 必须在同一
   * 执行序列中顺序调用，或者由调用方显式串行化。在 SerialController 中，
   * 这些操作都通过 IoContext strand 串行执行。
   */
  class PacketHandler
  {
  private:
    std::vector<uint8_t> ring_buffer_;
    size_t head_ = 0;
    size_t tail_ = 0;
    size_t capacity_;
    size_t mask_;

    static constexpr size_t MIN_PACKET_SIZE = 5;

    uint32_t overflow_count_ = 0;
    uint32_t crc_error_count_ = 0;
    uint32_t rx_packet_count_ = 0;

    static constexpr size_t next_power_of_two(size_t value)
    {
      size_t result = 1;
      while (result < value)
      {
        result <<= 1;
      }
      return result;
    }

    /// 校验和单字节更新，算法由编译期配置决定。打包、解包共用这一份实现。
    static constexpr uint8_t checksum_update(uint8_t current, uint8_t byte)
    {
      if constexpr (config::CHECKSUM_ALGO == config::ChecksumAlgo::NONE)
      {
        (void)byte;
        (void)current;
        return 0x00;
      }
      else if constexpr (config::CHECKSUM_ALGO == config::ChecksumAlgo::SUM8)
      {
        return static_cast<uint8_t>(current + byte);
      }
      else if constexpr (config::CHECKSUM_ALGO == config::ChecksumAlgo::XOR8)
      {
        return current ^ byte;
      }
      else // ChecksumAlgo::CRC8
      {
        return config::CRC8_TABLE[current ^ byte];
      }
    }

    uint8_t calculate_checksum_from_ring(size_t start, size_t count) const
    {
      uint8_t checksum = 0;
      for (size_t i = 0; i < count; ++i)
      {
        checksum = checksum_update(checksum, ring_buffer_[(start + i) & mask_]);
      }
      return checksum;
    }

  public:
    explicit PacketHandler(size_t buffer_size)
        : capacity_(next_power_of_two(buffer_size + 1)),
          mask_(capacity_ - 1)
    {
      ring_buffer_.resize(capacity_);
    }

    uint32_t overflow_count() const { return overflow_count_; }
    uint32_t crc_error_count() const { return crc_error_count_; }
    uint32_t rx_packet_count() const { return rx_packet_count_; }

    void reset()
    {
      head_ = 0;
      tail_ = 0;
    }

    bool try_push_byte(uint8_t byte)
    {
      const size_t next_head = (head_ + 1) & mask_;
      if (next_head == tail_)
      {
        return false;
      }
      ring_buffer_[head_] = byte;
      head_ = next_head;
      return true;
    }

    void record_overflow()
    {
      overflow_count_++;
    }

    static uint8_t calculate_checksum(const uint8_t *data, size_t len)
    {
      uint8_t checksum = 0;
      for (size_t i = 0; i < len; ++i)
      {
        checksum = checksum_update(checksum, data[i]);
      }
      return checksum;
    }

    /**
     * @brief 打包数据 (ROS -> MCU)
     */
    template <typename T>
    std::vector<uint8_t> pack(PacketID id, const T &data) const
    {
      static_assert(sizeof(T) <= 255, "数据大小不能超过255字节");
      // 双帧头 + ID + 长度 + 数据 + 校验 = 2 + 1 + 1 + N + 1 = 5 + N
      std::vector<uint8_t> packet;
      packet.reserve(5 + sizeof(T));

      packet.push_back(FRAME_HEADER1);
      packet.push_back(FRAME_HEADER2);
      packet.push_back(static_cast<uint8_t>(id));
      packet.push_back(static_cast<uint8_t>(sizeof(T)));

      const uint8_t *ptr = reinterpret_cast<const uint8_t *>(&data);
      packet.insert(packet.end(), ptr, ptr + sizeof(T));

      // 校验和覆盖范围: ID, 长度, 数据 (packet[2] 起)
      packet.push_back(calculate_checksum(packet.data() + 2, packet.size() - 2));
      return packet;
    }

    /**
     * @brief 接收数据投喂口
     * @return 本次调用丢弃的字节数 (0 = 无溢出)
     */
    size_t feed_data(const uint8_t *data, size_t len)
    {
      size_t dropped = 0;
      for (size_t i = 0; i < len; ++i)
      {
        if (!try_push_byte(data[i]))
        {
          dropped++;
          overflow_count_++;
        }
      }
      return dropped;
    }

    size_t feed_data(const std::vector<uint8_t> &data)
    {
      return feed_data(data.data(), data.size());
    }

    /**
     * @brief 解析数据包
     */
    bool parse_packet(Packet &out_packet)
    {
      while (data_available() >= MIN_PACKET_SIZE)
      {
        // 寻找双帧头
        const uint8_t b1 = ring_buffer_[tail_];
        const uint8_t b2 = ring_buffer_[(tail_ + 1) & mask_];
        if (b1 != FRAME_HEADER1 || b2 != FRAME_HEADER2)
        {
          tail_ = (tail_ + 1) & mask_;
          continue;
        }

        const uint8_t id_byte = ring_buffer_[(tail_ + 2) & mask_];
        const uint8_t len_byte = ring_buffer_[(tail_ + 3) & mask_];
        const size_t total_len = 2 + 1 + 1 + static_cast<size_t>(len_byte) + 1;

        if (total_len > capacity_ - 1)
        {
          // 当前候选帧即使完整到达也无法驻留在环形缓冲区中，丢弃头字节重同步。
          tail_ = (tail_ + 1) & mask_;
          continue;
        }

        if (len_byte > config::MAX_PACKET_PAYLOAD_SIZE)
        {
          tail_ = (tail_ + 1) & mask_;
          continue;
        }

        const size_t expected_len = config::expected_payload_size(static_cast<PacketID>(id_byte));
        if (expected_len == 0 || len_byte != expected_len)
        {
          tail_ = (tail_ + 1) & mask_;
          continue;
        }

        if (data_available() < total_len)
        {
          return false; // 等待完整数据包
        }

        bool checksum_ok;
        if constexpr (config::CHECKSUM_ALGO == config::ChecksumAlgo::NONE)
        {
          checksum_ok = true;
        }
        else
        {
          const uint8_t calc_cs = calculate_checksum_from_ring((tail_ + 2) & mask_, 2 + len_byte);
          const uint8_t recv_cs = ring_buffer_[(tail_ + total_len - 1) & mask_];
          checksum_ok = (calc_cs == recv_cs);
        }

        if (!checksum_ok)
        {
          crc_error_count_++;
          tail_ = (tail_ + 1) & mask_;
          continue;
        }

        out_packet.id = static_cast<PacketID>(id_byte);
        out_packet.payload.resize(len_byte);
        const size_t payload_start = (tail_ + 4) & mask_;
        for (size_t i = 0; i < len_byte; ++i)
        {
          out_packet.payload[i] = ring_buffer_[(payload_start + i) & mask_];
        }

        tail_ = (tail_ + total_len) & mask_;
        rx_packet_count_++;
        return true;
      }
      return false;
    }

    size_t data_available() const
    {
      return (head_ - tail_) & mask_;
    }
  };

  template <typename PacketConsumer>
  size_t feed_data_with_recovery(
      PacketHandler &handler,
      const uint8_t *data,
      size_t len,
      PacketConsumer &&consumer)
  {
    size_t dropped = 0;
    Packet pkt;

    for (size_t i = 0; i < len; ++i)
    {
      if (handler.try_push_byte(data[i]))
      {
        continue;
      }

      bool drained_any = false;
      while (handler.parse_packet(pkt))
      {
        drained_any = true;
        consumer(pkt);
      }

      if (handler.try_push_byte(data[i]))
      {
        continue;
      }

      // 缓冲满且 drain 无效，reset 缓冲区以避免后续字节全部丢弃
      if (!drained_any)
      {
        handler.reset();
      }

      handler.record_overflow();
      dropped++;

      // reset 后重试当前字节
      handler.try_push_byte(data[i]);
    }

    while (handler.parse_packet(pkt))
    {
      consumer(pkt);
    }

    return dropped;
  }

} // namespace auto_serial_bridge
