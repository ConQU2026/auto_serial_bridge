#pragma once

#include "auto_serial_bridge/protocol.hpp"
#include "auto_serial_bridge/generated_config.hpp"
#include <vector>
#include <iostream>
#include <algorithm>
#include <cstdint>

namespace auto_serial_bridge
{

  /**
   * @brief 数据包处理类
   *
   * 负责数据的校验、打包和解包。使用环形缓冲区实现零拷贝和高性能解析。
   *
   * 线程模型: feed_data() 和 parse_packet() 必须在同一线程中顺序调用，
   * 或者由调用方持有同一把锁来保护。在 SerialController 中，两者均在
   * IoContext 的 async_receive 回调中串行执行，由 rx_mutex_ 统一保护。
   */
  class PacketHandler
  {
  private:
    std::vector<uint8_t> ring_buffer_;
    size_t head_ = 0;
    size_t tail_ = 0;
    size_t capacity_;
    
    static constexpr size_t MIN_PACKET_SIZE = 5; 

    uint32_t overflow_count_ = 0;
    uint32_t crc_error_count_ = 0;
    uint32_t rx_packet_count_ = 0;

    static uint8_t calculate_checksum_from_ring(
        const std::vector<uint8_t> & ring_buffer,
        size_t capacity,
        size_t start,
        size_t count)
    {
      uint8_t checksum = 0;
      for (size_t i = 0; i < count; ++i) {
        const uint8_t byte = ring_buffer[(start + i) % capacity];
        if constexpr (config::CHECKSUM_ALGO == config::ChecksumAlgo::NONE) {
          (void)byte;
          checksum = 0x00;
        } else if constexpr (config::CHECKSUM_ALGO == config::ChecksumAlgo::SUM8) {
          checksum += byte;
        } else if constexpr (config::CHECKSUM_ALGO == config::ChecksumAlgo::XOR8) {
          checksum ^= byte;
        } else if constexpr (config::CHECKSUM_ALGO == config::ChecksumAlgo::CRC8) {
#ifdef CHECKSUM_ALGO_CRC8
          checksum = CRC8_TABLE[checksum ^ byte];
#else
          static_assert(config::CHECKSUM_ALGO != config::ChecksumAlgo::CRC8,
                        "CRC8 selected but CRC8_TABLE is not available");
#endif
        }
      }
      return checksum;
    }

  public:
    explicit PacketHandler(size_t buffer_size) : capacity_(buffer_size + 1)
    {
       ring_buffer_.resize(capacity_);
    }

    uint32_t overflow_count()  const { return overflow_count_; }
    uint32_t crc_error_count() const { return crc_error_count_; }
    uint32_t rx_packet_count() const { return rx_packet_count_; }

    void reset()
    {
        head_ = 0;
        tail_ = 0;
    }

    /**
     * @brief 根据编译期配置分派的校验和计算
     */
	    static uint8_t calculate_checksum(const uint8_t* data, size_t len)
	    {
	      if constexpr (config::CHECKSUM_ALGO == config::ChecksumAlgo::NONE) {
	        (void)data; (void)len;
	        return 0x00;
	      } else if constexpr (config::CHECKSUM_ALGO == config::ChecksumAlgo::SUM8) {
	        uint8_t sum = 0;
	        for (size_t i = 0; i < len; ++i) sum += data[i];
	        return sum;
	      } else if constexpr (config::CHECKSUM_ALGO == config::ChecksumAlgo::XOR8) {
	        uint8_t x = 0;
	        for (size_t i = 0; i < len; ++i) x ^= data[i];
	        return x;
	      } else if constexpr (config::CHECKSUM_ALGO == config::ChecksumAlgo::CRC8) {
#ifdef CHECKSUM_ALGO_CRC8
	        uint8_t crc = 0;
	        for (size_t i = 0; i < len; ++i) crc = CRC8_TABLE[crc ^ data[i]];
	        return crc;
#else
	        static_assert(config::CHECKSUM_ALGO != config::ChecksumAlgo::CRC8,
	                      "CRC8 selected but CRC8_TABLE is not available");
	        return 0x00;
#endif
	      } else {
	        static_assert(config::CHECKSUM_ALGO == config::ChecksumAlgo::NONE ||
	                      config::CHECKSUM_ALGO == config::ChecksumAlgo::SUM8 ||
	                      config::CHECKSUM_ALGO == config::ChecksumAlgo::XOR8 ||
	                      config::CHECKSUM_ALGO == config::ChecksumAlgo::CRC8,
	                      "Unsupported checksum algorithm");
	        return 0x00;
	      }
	    }

    /**
     * @brief 打包数据 (ROS -> MCU)
     */
    template <typename T>
    std::vector<uint8_t> pack(PacketID id, const T &data) const
    {
      static_assert(sizeof(T) <= 255, "数据大小不能超过255字节");
      // 双帧头 + ID + 长度 + 数据 + CRC = 2 + 1 + 1 + N + 1 = 5 + N
      const size_t packet_size = 5 + sizeof(T);
      std::vector<uint8_t> packet;
      packet.reserve(packet_size);

      packet.push_back(FRAME_HEADER1);
      packet.push_back(FRAME_HEADER2);
      packet.push_back(static_cast<uint8_t>(id));
      packet.push_back(static_cast<uint8_t>(sizeof(T)));
      
      const uint8_t *ptr = reinterpret_cast<const uint8_t *>(&data);
      packet.insert(packet.end(), ptr, ptr + sizeof(T));
      
      // 校验和覆盖范围: ID, 长度, 数据
      // packet[2] 是 ID.
      uint8_t checksum = calculate_checksum(packet.data() + 2, packet.size() - 2);
      packet.push_back(checksum);

      return packet;
    }

    /**
     * @brief 接收数据投喂口
     * @return 本次调用丢弃的字节数 (0 = 无溢出)
     */
    size_t feed_data(const uint8_t* data, size_t len)
    {
        size_t dropped = 0;
        for (size_t i = 0; i < len; ++i) {
            size_t next_head = (head_ + 1) % capacity_;
            if (next_head != tail_) {
                ring_buffer_[head_] = data[i];
                head_ = next_head;
            } else {
                dropped = len - i;
                overflow_count_++;
                break; 
            }
        }
        return dropped;
    }
    
    size_t feed_data(const std::vector<uint8_t>& data) {
        return feed_data(data.data(), data.size());
    }

    /**
     * @brief 解析数据包
     */
    bool parse_packet(Packet &out_packet)
    {
        while (data_available() >= MIN_PACKET_SIZE) 
        {
            // 1. 快速搜寻帧头: 寻找 0x5A, 0xA5
            // 检查 tail 和 (tail+1)%cap
            uint8_t b1 = ring_buffer_[tail_];
            uint8_t b2 = ring_buffer_[(tail_ + 1) % capacity_];
            
            if (b1 != FRAME_HEADER1 || b2 != FRAME_HEADER2) {
                // 不是帧头，滑动窗口
                tail_ = (tail_ + 1) % capacity_;
                continue;
            }
            
            // 发现帧头在 tail_, tail_+1
            // 需要检查是否有足够的数据获取长度信息
            // Header(2) + ID(1) + Len(1) = 需要4个字节来知道长度
            
            if (data_available() < 4) return false; // 等待更多数据
            
            uint8_t id_byte = ring_buffer_[(tail_ + 2) % capacity_];
            uint8_t len_byte = ring_buffer_[(tail_ + 3) % capacity_];
            
            size_t total_len = 2 + 1 + 1 + len_byte + 1; // Header(2) + ID(1) + Len(1) + Payload(N) + CRC(1)

            if (total_len > capacity_ - 1) {
                // 当前候选帧即使完整到达也无法驻留在环形缓冲区中，只能丢弃头字节后重同步。
                tail_ = (tail_ + 1) % capacity_;
                continue;
            }

            if (len_byte > config::MAX_PACKET_PAYLOAD_SIZE) {
                tail_ = (tail_ + 1) % capacity_;
                continue;
            }

            const size_t expected_len = config::expected_payload_size(static_cast<PacketID>(id_byte));
            if (expected_len == 0 || len_byte != expected_len) {
                tail_ = (tail_ + 1) % capacity_;
                continue;
            }
            
            if (data_available() < total_len) return false; // 等待完整数据包
            
            // 校验: 在环形缓冲区上直接计算
            bool checksum_ok;
            if constexpr (config::CHECKSUM_ALGO == config::ChecksumAlgo::NONE) {
                checksum_ok = true;
            } else {
                size_t cs_start = (tail_ + 2) % capacity_;
                size_t cs_count = 2 + len_byte;
                uint8_t calc_cs = calculate_checksum_from_ring(ring_buffer_, capacity_, cs_start, cs_count);
                uint8_t recv_cs = ring_buffer_[(tail_ + total_len - 1) % capacity_];
                checksum_ok = (calc_cs == recv_cs);
            }
            
            if (checksum_ok) {
                 out_packet.id = static_cast<PacketID>(id_byte);
                 out_packet.payload.resize(len_byte);
                 
                 size_t payload_start = (tail_ + 4) % capacity_;
                 for (size_t i = 0; i < len_byte; ++i) {
                     out_packet.payload[i] = ring_buffer_[(payload_start + i) % capacity_];
                 }
                 
                 tail_ = (tail_ + total_len) % capacity_;
                 rx_packet_count_++;
                 return true;
            } else {
                 crc_error_count_++;
                 tail_ = (tail_ + 1) % capacity_;
            }
            
        }
        return false;
    }
    
    size_t data_available() const {
        if (head_ >= tail_) return head_ - tail_;
        return capacity_ - tail_ + head_;
    }
    
    
  };

} // namespace auto_serial_bridge
