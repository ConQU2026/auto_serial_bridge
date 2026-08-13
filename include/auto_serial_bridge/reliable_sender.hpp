#pragma once

#include <asio/error.hpp>
#include <asio/io_service.hpp>
#include <asio/io_service_strand.hpp>
#include <asio/steady_timer.hpp>

#include <chrono>
#include <cstdint>
#include <functional>
#include <memory>
#include <unordered_map>
#include <utility>
#include <vector>

#include "auto_serial_bridge/packet_handler.hpp"
#include "auto_serial_bridge/protocol.hpp"

namespace auto_serial_bridge
{

  class ReliableSender : public std::enable_shared_from_this<ReliableSender>
  {
  public:
    using SendCallback = std::function<bool(const std::vector<uint8_t> &)>;
    using ExhaustedCallback = std::function<void(PacketID id, int max_retries)>;

    ReliableSender(
        asio::io_service &io_service,
        asio::io_service::strand &strand,
        SendCallback send_callback,
        ExhaustedCallback exhausted_callback,
        std::chrono::milliseconds retry_interval,
        int max_retries)
        : io_service_(io_service),
          strand_(strand),
          send_callback_(std::move(send_callback)),
          exhausted_callback_(std::move(exhausted_callback)),
          retry_interval_(retry_interval),
          max_retries_(max_retries)
    {
    }

    void send(PacketID id, std::vector<uint8_t> packed_bytes)
    {
      auto self = shared_from_this();
      run_or_post(
          [self, id, packed_bytes = std::move(packed_bytes)]() mutable
          {
            self->send_impl(id, std::move(packed_bytes));
          });
    }

    void on_ack_received(uint8_t acked_id, uint8_t ack_seq)
    {
      auto self = shared_from_this();
      run_or_post(
          [self, acked_id, ack_seq]()
          {
            self->on_ack_received_impl(acked_id, ack_seq);
          });
    }

    void clear_all()
    {
      auto self = shared_from_this();
      run_or_post(
          [self]()
          {
            self->clear_all_impl();
          });
    }

  private:
    struct PendingEntry
    {
      PacketID id;
      std::vector<uint8_t> packed_bytes;
      int retries_left;
      uint8_t expected_seq;
      std::shared_ptr<asio::steady_timer> timer;
    };

    template <typename Fn>
    void run_or_post(Fn &&fn)
    {
      if (strand_.running_in_this_thread())
      {
        fn();
        return;
      }
      strand_.post(std::forward<Fn>(fn));
    }

    /// 在已打包帧的 payload 末尾注入 1 字节 seq，并更新 LEN 和校验和。
    static void inject_trailing_seq(std::vector<uint8_t> &frame, uint8_t seq)
    {
      if (frame.size() < 5)
      {
        return;
      }
      // LEN 字段加 1
      frame[3]++;
      // 在校验和（末字节）之前插入 seq
      frame.insert(frame.end() - 1, seq);
      // 重新计算校验和（覆盖 ID + LEN + PAYLOAD + seq）
      frame.back() = PacketHandler::calculate_checksum(
          frame.data() + 2, frame.size() - 3);
    }

    void send_impl(PacketID id, std::vector<uint8_t> packed_bytes)
    {
      const uint8_t key = static_cast<uint8_t>(id);
      auto it = pending_.find(key);
      if (it != pending_.end())
      {
        it->second.timer->cancel();
        pending_.erase(it);
      }

      const uint8_t seq = seq_counter_++;
      inject_trailing_seq(packed_bytes, seq);

      PendingEntry entry;
      entry.id = id;
      entry.packed_bytes = std::move(packed_bytes);
      entry.retries_left = max_retries_;
      entry.expected_seq = seq;
      entry.timer = std::make_shared<asio::steady_timer>(io_service_);

      auto inserted = pending_.emplace(key, std::move(entry));
      const auto &first_send = inserted.first->second.packed_bytes;
      send_callback_(first_send);

      if (pending_.find(key) != pending_.end())
      {
        schedule_retry(key);
      }
    }

    void schedule_retry(uint8_t key)
    {
      auto it = pending_.find(key);
      if (it == pending_.end())
      {
        return;
      }

      auto timer = it->second.timer;
      timer->expires_from_now(retry_interval_);

      std::weak_ptr<ReliableSender> weak_self = weak_from_this();
      timer->async_wait(
          [weak_self, key](const asio::error_code &ec)
          {
            if (ec == asio::error::operation_aborted)
            {
              return;
            }
            auto self = weak_self.lock();
            if (!self)
            {
              return;
            }
            self->run_or_post(
                [self, key]()
                {
                  self->handle_retry_timeout(key);
                });
          });
    }

    void handle_retry_timeout(uint8_t key)
    {
      auto it = pending_.find(key);
      if (it == pending_.end())
      {
        return;
      }

      if (it->second.retries_left <= 0)
      {
        if (exhausted_callback_)
        {
          exhausted_callback_(it->second.id, max_retries_);
        }
        it->second.timer->cancel();
        pending_.erase(it);
        return;
      }

      // 即使底层拒绝了本次发送（未连接、握手未完成被拦截等）也消耗一次重试，
      // 保证 max_retries 上限语义成立；否则过期指令会在链路恢复后被无限延迟送达。
      std::vector<uint8_t> retry_payload = it->second.packed_bytes;
      send_callback_(retry_payload);
      it->second.retries_left--;

      if (pending_.find(key) != pending_.end())
      {
        schedule_retry(key);
      }
    }

    void on_ack_received_impl(uint8_t acked_id, uint8_t ack_seq)
    {
      auto it = pending_.find(acked_id);
      if (it == pending_.end())
      {
        return;
      }
      // 序列号不匹配则忽略（过期 ACK）
      if (it->second.expected_seq != ack_seq)
      {
        return;
      }
      it->second.timer->cancel();
      pending_.erase(it);
    }

    void clear_all_impl()
    {
      for (auto &kv : pending_)
      {
        kv.second.timer->cancel();
      }
      pending_.clear();
    }

    asio::io_service &io_service_;
    asio::io_service::strand &strand_;
    SendCallback send_callback_;
    ExhaustedCallback exhausted_callback_;
    std::chrono::milliseconds retry_interval_;
    int max_retries_;
    uint8_t seq_counter_ = 0;
    std::unordered_map<uint8_t, PendingEntry> pending_;
  };

} // namespace auto_serial_bridge
