## ADDED Requirements

### Requirement: Polling-based future completion wait
The download loop SHALL replace `concurrent.futures.as_completed(futures, timeout=post_timeout)` with a polling loop using `concurrent.futures.wait(futures, timeout=0.5, return_when=FIRST_COMPLETED)`, checking for interruption between each poll cycle. A monotonic deadline (`time.monotonic() + post_timeout`) SHALL enforce the original per-post timeout semantics.

#### Scenario: Normal completion without interruption
- **WHEN** all media futures complete before the deadline
- **THEN** every completed future SHALL be processed exactly once with the same outcome logic (downloaded/skipped/failed) as the original `as_completed` path
- **AND** the set of emitted `MEDIA_DONE` events SHALL be identical in count and content to the original behavior

#### Scenario: Per-post timeout expires
- **WHEN** the monotonic deadline is reached and futures remain incomplete
- **THEN** all incomplete futures SHALL be cancelled via `future.cancel()`
- **AND** each cancelled future SHALL be counted as failed and emit a `MEDIA_DONE` event with `MediaOutcome.FAILED`
- **AND** `timed_out` SHALL be set to `True`, preserving existing checkpoint-skip logic

### Requirement: SIGINT responsiveness on Windows within 1 second
On Windows, pressing Ctrl+C (SIGINT) during any blocking operation SHALL cause the main thread to exit the wait loop within 1 second. The existing three-level `KeyboardInterrupt` handler chain SHALL remain unchanged.

#### Scenario: Ctrl+C during media download wait
- **WHEN** user presses Ctrl+C while the polling loop is waiting on futures
- **THEN** the main thread SHALL break out of the polling loop within 1 second
- **AND** `executor.shutdown(wait=False, cancel_futures=True)` SHALL be called
- **AND** the `KeyboardInterrupt` SHALL propagate to the existing handler at `weiboloader.py` download_target level, which saves checkpoint and emits `INTERRUPTED` event
- **AND** exit code SHALL be 5

#### Scenario: Ctrl+C during rate-limit backoff sleep
- **WHEN** user presses Ctrl+C while `SlidingWindowRateController._sleep()` is blocking
- **THEN** the sleep SHALL be interrupted (Python 3.10+ `time.sleep` is interruptible on Windows)
- **AND** the `KeyboardInterrupt` SHALL propagate through the existing handler chain
- **AND** exit code SHALL be 5

#### Scenario: No regression on Unix/WSL
- **WHEN** the program runs on Unix/WSL with the new polling loop
- **THEN** Ctrl+C behavior SHALL remain identical to the current behavior
- **AND** the polling loop SHALL produce the same event sequence as `as_completed` for any given set of futures and outcomes

### Requirement: Interrupt-safe executor shutdown
When `KeyboardInterrupt` is caught in the download loop, the executor SHALL be shut down with `shutdown(wait=False, cancel_futures=True)` to prevent the `ThreadPoolExecutor` context manager from blocking on in-flight downloads.

#### Scenario: Executor shutdown on interrupt
- **WHEN** `KeyboardInterrupt` is raised during the polling loop
- **THEN** `executor.shutdown(wait=False, cancel_futures=True)` SHALL be called before re-raising or entering the existing interrupt handler
- **AND** no new futures SHALL be submitted after the interrupt

## PBT Properties

### Property: Interrupt response bound
- **INVARIANT**: Time from SIGINT to exiting the wait loop ≤ 1 second
- **FALSIFICATION**: Generate futures with varying delays, trigger SIGINT at random points, measure exit latency

### Property: Completion set monotonicity post-interrupt
- **INVARIANT**: After interrupt, the done_futures set SHALL not grow
- **FALSIFICATION**: Inject interrupt during polling, observe done_futures size over subsequent cycles

### Property: Event sequence equivalence (non-interrupt path)
- **INVARIANT**: For identical futures and outcomes, the polling loop produces the same MEDIA_DONE/POST_DONE/TARGET_DONE event sequence as as_completed
- **FALSIFICATION**: Fixed-seed PBT generating posts/media with deterministic delays, compare event sequences between old and new implementations
