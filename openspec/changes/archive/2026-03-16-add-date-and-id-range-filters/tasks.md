## 1. Extend CLI boundary parsing

- [x] 1.1 Add `-b/--date-boundary` and `-B/--id-boundary` arguments in `weiboloader/__main__.py` with inclusive `START:END` help text
- [x] 1.2 Implement canonical boundary parsing helpers for date and MID ranges, including `:` as unset, equivalent date formats, ID leading-zero normalization, and `START > END` validation
- [x] 1.3 Pass canonical boundary selections from CLI parsing into `WeiboLoader` and add CLI-focused tests for valid, invalid, and equivalent inputs

## 2. Preserve boundary comparison semantics

- [x] 2.1 Update datetime parsing and CST normalization so boundary comparisons use the post's original timezone while naive timestamps are treated as CST
- [x] 2.2 Add boundary helper logic that evaluates inclusive date and MID membership with AND semantics when both filters are present
- [x] 2.3 Add focused tests for timezone-aware dates, naive dates, exact endpoint inclusion, and single-point MID ranges

## 3. Apply boundary-aware traversal rules

- [x] 3.1 Refactor `WeiboLoader.download_target()` so boundary evaluation happens before metadata writes, media scheduling, `--count`, and `--fast-update`
- [x] 3.2 Implement target-specific traversal behavior: User timeline supports cutoff, Search/SuperTopic remain filter-only, and single-post targets succeed with zero output when out-of-range
- [x] 3.3 Detect pinned user-timeline posts from raw payload (`mblogtype == 2`) so pinned posts never trigger cutoff but still download when in-range

## 4. Make progress compatibility boundary-aware

- [x] 4.1 Extend `_hash_options()` to include canonical date and ID boundary selections while keeping traversal-only flags reusable
- [x] 4.2 Ensure resume and coverage reuse are invalidated by boundary changes but preserved for canonical-equivalent boundary syntax
- [x] 4.3 Add regression tests for boundary-aware resume/coverage compatibility and freeze/thaw behavior after skipping out-of-range posts

## 5. Lock in in-range side-effect semantics

- [x] 5.1 Ensure out-of-range posts produce no metadata writes, media downloads, count consumption, or fast-update stop decisions
- [x] 5.2 Ensure coverage skip and coverage sealing only consider in-range posts and do not require out-of-range posts to succeed inside a timestamp group
- [x] 5.3 Add loader regression tests covering out-of-range side-effect isolation, pinned-post cutoff behavior, and fast-update/count interactions inside boundaries

## 6. Validate OpenSpec and implementation readiness

- [x] 6.1 Update delta specs only if implementation details require clarification during apply, keeping `cli-interface`, `filtering-incremental`, and `resumable-download` aligned with code behavior
- [x] 6.2 Run the relevant CLI and loader test suites for boundary parsing, traversal, and progress compatibility behavior
- [x] 6.3 Verify the final implementation against this change's design/spec decisions before closing the implementation phase
