# Auto Serial Bridge Deferred Robustness Work

## #1 Heartbeat mismatch after non-conforming MCU restart

- Why deferred: current generated MCU contract is to echo the latest received heartbeat count, so the normal generated peer does not exhibit the permanent mismatch described in the review. The remaining risk is a non-conforming MCU implementation that originates or echoes the wrong count.
- Revisit trigger: field logs repeatedly show heartbeat ACK mismatch warnings immediately after MCU-only restarts while the peer firmware is known to be custom or hand-edited.
- Likely fix direction: introduce a bounded resynchronization strategy for heartbeat ACK state, such as a handshake-gated heartbeat reset or a one-shot acceptance window after reconnect, without weakening the normal strict timeout path.

## #4 Combined `is_connected_` / `driver_` access

- Why deferred: current connection-sensitive send/reset/receive paths are serialized onto the strand, which is the main protection against cross-thread use-after-reset here. Adding a separate lock now would increase concurrency complexity without fixing an observed non-strand path.
- Revisit trigger: any new send, reset, or receive path starts touching `driver_` outside `post_serial(...)` / strand-wrapped execution, or field crashes point at stale port/driver access.
- Likely fix direction: centralize all connection state reads behind strand-only helpers, and only introduce extra synchronization if a true non-strand access requirement appears.

## #6 `dispatch_packet()` static on-change state

- Why deferred: RX dispatch currently runs in a single serialized execution sequence, so the generated static previous-packet caches are not contended in the current architecture.
- Revisit trigger: RX dispatch becomes multi-threaded, or publishers are moved onto multiple concurrent executors/queues that can call the generated dispatch logic simultaneously.
- Likely fix direction: move per-message previous-packet state into an explicit runtime state holder owned by the node or publishers, instead of relying on function-local statics.

## #8 Intra-process loopback skip policy

- Why deferred: removing the current `from_intra_process` fallback naively risks self-loop storms for `both` messages that mirror on the same topic. The current behavior can over-suppress some intra-process peers, but it is still the safer default than allowing bridge-originated feedback loops.
- Revisit trigger: a real same-process publisher distinct from the bridge is confirmed to be suppressed incorrectly on a shared topic.
- Likely fix direction: redesign mirrored `both` traffic to use explicit topic separation or an origin tag, so loopback suppression no longer depends on coarse intra-process heuristics.

## #10 Overflow reset policy in `feed_data_with_recovery()`

- Why deferred: the receive path already logs dropped bytes when recovery cannot drain the ring buffer, and the current reset behavior prioritizes fast resynchronization over preserving an ambiguous partial buffer. That is the more stable choice until field evidence shows it is too aggressive.
- Revisit trigger: logs show repeated overflow bursts where recovery resets correlate with loss of otherwise salvageable packets, or recorder traces prove partial valid frames are commonly discarded during noisy conditions.
- Likely fix direction: add richer overflow diagnostics first, then consider a more selective recovery strategy that distinguishes obviously unsalvageable garbage from incomplete but still potentially valid frames.
