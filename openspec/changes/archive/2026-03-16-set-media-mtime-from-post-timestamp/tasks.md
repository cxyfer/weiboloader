## 1. Add timestamp application boundaries

- [x] 1.1 Add a loader helper that derives the target filesystem epoch from `_cst(post.created_at)` and applies it to landed files
- [x] 1.2 Attach media timestamp application at the post-download boundary without changing the existing `_download(url, dest)` call signature
- [x] 1.3 Fail closed when media `mtime` application fails by removing the landed file and surfacing a failed media outcome

## 2. Make sidecar writes timestamp-aware and option-aware

- [x] 2.1 Update `_write_json()` to skip existing non-empty sidecars on compatible reruns, rewrite them when output-affecting options force reprocessing, and apply normalized post `mtime` after successful writes
- [x] 2.2 Update `_write_txt()` to follow the same compatible-skip / incompatible-rewrite policy and apply normalized post `mtime` after successful writes
- [x] 2.3 Fail closed when sidecar `mtime` application fails by removing the just-written sidecar and failing the current target

## 3. Preserve existing loader invariants

- [x] 3.1 Thread the output-compatibility signal needed by sidecar writers through `download_target()` without changing resume, coverage, or `fast_update` semantics
- [x] 3.2 Keep existing non-empty media files reported as `SKIPPED` with unchanged `mtime` on compatible reruns
- [x] 3.3 Preserve coverage-hash-mismatch behavior so reprocessed posts rewrite sidecars and realign `mtime` while compatible covered posts remain untouched

## 4. Add regression coverage

- [x] 4.1 Add tests for downloaded media `mtime` alignment, skipped-media `mtime` preservation, and empty-file redownload retimestamping
- [x] 4.2 Add tests for JSON and TXT sidecar `mtime` alignment plus compatible-rerun skip preservation
- [x] 4.3 Add tests proving output-affecting option changes rewrite existing sidecars and realign `mtime`
- [x] 4.4 Add failure-injection tests for media and sidecar `mtime` errors that verify landed artifacts are removed and the flow fails closed
- [x] 4.5 Run the relevant loader test suites and confirm filesystem timestamp assertions use tolerant epoch comparison
