#pragma once

#include <asio/error.hpp>
#include <asio/io_service.hpp>
#include <asio/io_service_strand.hpp>
#include <asio/steady_timer.hpp>

#include <chrono>
#include <cstdint>
#include <deque>
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
    using SendCompletion = std::function<void(bool)>;
    using SendCallback = std::function<void(const std::vector<uint8_t> &, SendCompletion)>;
    using RetryThresholdCallback = std::function<void(PacketID id, int retry_threshold)>;

    ReliableSender(
        asio::io_service &io_service,
        asio::io_service::strand &strand,
        SendCallback send_callback,
        RetryThresholdCallback retry_threshold_callback,
        std::chrono::milliseconds retry_interval,
        int retry_warning_threshold)
        : io_service_(io_service),
          strand_(strand),
          send_callback_(std::move(send_callback)),
          retry_threshold_callback_(std::move(retry_threshold_callback)),
          retry_interval_(retry_interval),
          retry_warning_threshold_(retry_warning_threshold)
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
      run_or_post([self]() { self->clear_all_impl(); });
    }

  private:
    struct PendingEntry
    {
      PacketID id;
      std::vector<uint8_t> packed_bytes;
      uint8_t expected_seq = 0;
      bool seq_assigned = false;
      uint64_t send_generation = 0;
      int retries_since_warning = 0;
      bool send_in_progress = false;
      bool awaiting_ack = false;
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

    static void inject_trailing_seq(std::vector<uint8_t> &frame, uint8_t seq)
    {
      if (frame.size() < 5)
      {
        return;
      }
      frame[3]++;
      frame.insert(frame.end() - 1, seq);
      frame.back() = PacketHandler::calculate_checksum(
          frame.data() + 2, frame.size() - 3);
    }

    void send_impl(PacketID id, std::vector<uint8_t> packed_bytes)
    {
      const uint8_t key = static_cast<uint8_t>(id);
      PendingEntry entry;
      entry.id = id;
      entry.packed_bytes = std::move(packed_bytes);
      entry.timer = std::make_shared<asio::steady_timer>(io_service_);

      auto &queue = pending_[key];
      queue.push_back(std::move(entry));
      if (queue.size() == 1)
      {
        attempt_send(key);
      }
    }

    void attempt_send(uint8_t key)
    {
      auto it = pending_.find(key);
      if (it == pending_.end() || it->second.empty())
      {
        return;
      }

      auto &entry = it->second.front();
      if (entry.send_in_progress)
      {
        return;
      }

      if (!entry.seq_assigned)
      {
        entry.expected_seq = seq_counter_++;
        inject_trailing_seq(entry.packed_bytes, entry.expected_seq);
        entry.seq_assigned = true;
      }

      entry.send_in_progress = true;
      const uint8_t seq = entry.expected_seq;
      const uint64_t generation = ++entry.send_generation;
      std::weak_ptr<ReliableSender> weak_self = weak_from_this();
      send_callback_(
          entry.packed_bytes,
          [weak_self, key, seq, generation](bool success)
          {
            auto self = weak_self.lock();
            if (!self)
            {
              return;
            }
            self->run_or_post(
                [self, key, seq, generation, success]()
                {
                  self->handle_send_completion(key, seq, generation, success);
                });
          });
    }

    void handle_send_completion(
        uint8_t key,
        uint8_t seq,
        uint64_t generation,
        bool success)
    {
      auto it = pending_.find(key);
      if (it == pending_.end() || it->second.empty())
      {
        return;
      }

      auto &entry = it->second.front();
      if (entry.expected_seq != seq || entry.send_generation != generation)
      {
        return;
      }

      entry.send_in_progress = false;
      entry.awaiting_ack = success;
      schedule_retry(key);
    }

    void schedule_retry(uint8_t key)
    {
      auto it = pending_.find(key);
      if (it == pending_.end() || it->second.empty())
      {
        return;
      }

      auto timer = it->second.front().timer;
      const uint8_t seq = it->second.front().expected_seq;
      timer->expires_from_now(retry_interval_);

      std::weak_ptr<ReliableSender> weak_self = weak_from_this();
      timer->async_wait(
          [weak_self, key, seq](const asio::error_code &ec)
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
                [self, key, seq]() { self->handle_retry_timeout(key, seq); });
          });
    }

    void handle_retry_timeout(uint8_t key, uint8_t seq)
    {
      auto it = pending_.find(key);
      if (it == pending_.end() || it->second.empty())
      {
        return;
      }

      auto &entry = it->second.front();
      if (entry.expected_seq != seq)
      {
        return;
      }

      entry.awaiting_ack = false;
      ++entry.retries_since_warning;
      if (retry_warning_threshold_ > 0 &&
          entry.retries_since_warning >= retry_warning_threshold_)
      {
        entry.retries_since_warning = 0;
        if (retry_threshold_callback_)
        {
          retry_threshold_callback_(entry.id, retry_warning_threshold_);
        }
      }
      attempt_send(key);
    }

    void on_ack_received_impl(uint8_t acked_id, uint8_t ack_seq)
    {
      auto it = pending_.find(acked_id);
      if (it == pending_.end() || it->second.empty())
      {
        return;
      }
      if (it->second.front().expected_seq != ack_seq ||
          !it->second.front().awaiting_ack)
      {
        return;
      }

      it->second.front().timer->cancel();
      it->second.pop_front();
      if (it->second.empty())
      {
        pending_.erase(it);
        return;
      }
      attempt_send(acked_id);
    }

    void clear_all_impl()
    {
      for (auto &kv : pending_)
      {
        for (auto &entry : kv.second)
        {
          entry.timer->cancel();
        }
      }
      pending_.clear();
    }

    asio::io_service &io_service_;
    asio::io_service::strand &strand_;
    SendCallback send_callback_;
    RetryThresholdCallback retry_threshold_callback_;
    std::chrono::milliseconds retry_interval_;
    int retry_warning_threshold_;
    uint8_t seq_counter_ = 0;
    std::unordered_map<uint8_t, std::deque<PendingEntry>> pending_;
  };

} // namespace auto_serial_bridge
