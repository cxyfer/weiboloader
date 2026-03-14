## 1. Upgrade progress schema

- [x] 1.1 Bump the unified progress schema to version 3 and reject legacy or malformed checkpoints as fully incompatible state
- [x] 1.2 Extend resume serialization to store the exact current-page unconsumed suffix snapshot alongside the iterator frontier
- [x] 1.3 Keep resume and coverage reuse independently hash-gated while preserving `count` and `fast_update` compatibility

## 2. Restore exact iterator replay

- [x] 2.1 Update `NodeIterator.freeze()` and `thaw()` to replay the saved current-page suffix before fetching later pages
- [x] 2.2 Ensure page transition state is committed only after the buffered posts for the current page are fully consumed
- [x] 2.3 Add iterator-focused tests for mid-page freeze/thaw, exact suffix replay, and progressive `--count` expansion

## 3. Correct resume and coverage semantics

- [x] 3.1 Refactor stop-reason handling in `WeiboLoader.download_target()` so non-terminal stops keep the latest exact resume frontier
- [x] 3.2 Prevent failed or unfinished timestamp groups from advancing resume past a gap or entering coverage
- [x] 3.3 Finalize coverage sealing so monotonicity breaks split intervals and only fully successful normalized timestamp groups are materialized

## 4. Preserve honest rerun behavior

- [x] 4.1 Skip fully covered posts before post processing while still revisiting uncovered posts
- [x] 4.2 Report existing size-`> 0` media outside coverage as item-level `SKIPPED` instead of silently skipping the whole post
- [x] 4.3 Add regression tests for landed-but-uncovered reruns and group sealing after mixed `DOWNLOADED`/`SKIPPED` outcomes

## 5. Fail closed on checkpoint errors

- [x] 5.1 Propagate lock contention and save-path failures as `CheckpointError` instead of logging and continuing
- [x] 5.2 Preserve the last durable checkpoint bytes when temporary-file creation, serialization, fsync, or rename fails
- [x] 5.3 Keep multi-target runs alive by failing only the current target when checkpoint persistence aborts

## 6. Validate end-to-end regressions

- [x] 6.1 Update loader regression tests for `--count`, `--fast-update`, interruption, and failed-group resume behavior
- [x] 6.2 Add checkpoint failure injection coverage for atomic-write and fail-closed guarantees
- [x] 6.3 Run the relevant resumable-download test suites and confirm the new progress semantics match the spec delta
