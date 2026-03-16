## MODIFIED Requirements

### Requirement: Count limit
The system SHALL support `--count N` to limit the maximum number of in-range posts processed per target after applying boundary filters and coverage skips.

#### Scenario: Count limit applied
- **WHEN** user passes `--count 50` and target has 200 in-range processable posts
- **THEN** system SHALL process at most 50 in-range posts and stop

#### Scenario: Count zero means unlimited
- **WHEN** user does not pass `--count` or passes `--count 0`
- **THEN** system SHALL process all available in-range posts

#### Scenario: Available posts fewer than count
- **WHEN** user passes `--count 100` but target only has 30 in-range processable posts
- **THEN** system SHALL process all 30 in-range posts without error

#### Scenario: Out-of-range posts do not consume count
- **WHEN** boundary filtering excludes the first 20 scanned posts and user passes `--count 5`
- **THEN** system SHALL continue scanning until it has processed 5 in-range posts or exhausts the target

### Requirement: Fast update mode
When `--fast-update` is enabled, the system SHALL stop fetching posts for a target as soon as it encounters an in-range post whose media files already exist on disk (exists && size > 0).

#### Scenario: Fast update hits existing in-range file
- **WHEN** `--fast-update` is enabled and the 5th in-range post's media file already exists
- **THEN** system SHALL stop processing that target after the previous 4 in-range posts

#### Scenario: Out-of-range existing file does not stop fast update
- **WHEN** `--fast-update` is enabled and an out-of-range post has existing media files on disk
- **THEN** system SHALL ignore that post for fast-update stopping and continue scanning for later in-range posts

#### Scenario: Fast update with no existing in-range files
- **WHEN** `--fast-update` is enabled but no in-range post has existing media files
- **THEN** system SHALL process all in-range posts (behaves like normal mode)

### Requirement: Coverage-based incremental updates
The system SHALL persist per-target coverage as successful run intervals in `output_dir/.progress` and use it to skip already confirmed timestamp ranges on subsequent runs. Coverage is options-aware, applies only to in-range posts, and only applies when the stored hash matches the current output options and canonical boundary selections.

#### Scenario: First run creates coverage
- **WHEN** unified progress for a target does not exist
- **THEN** system SHALL download all in-range posts and persist confirmed coverage as run intervals for completed in-range timestamp groups

#### Scenario: Coverage hit skips in-range post but keeps scanning
- **WHEN** unified progress already covers an in-range timestamp `2025-06-01T12:00:00+08:00`
- **THEN** system SHALL skip posts at that timestamp and continue scanning older posts for uncovered in-range gaps

#### Scenario: Coverage advances as run intervals
- **WHEN** multiple consecutive in-range timestamp groups complete successfully
- **THEN** system SHALL merge them into a single coverage interval `[oldest_timestamp, newest_timestamp]`

#### Scenario: Failed or timed-out posts do not advance coverage
- **WHEN** a post in an in-range timestamp group fails or times out
- **THEN** system SHALL NOT advance coverage for that timestamp group

#### Scenario: All stop points flush sealed runs
- **WHEN** download stops (target complete, failure, Ctrl+C, `--count` limit, `--fast-update` early stop)
- **THEN** system SHALL flush all sealed successful in-range runs to coverage (current incomplete group is never flushed)

#### Scenario: Boundary or output mismatch ignores legacy coverage
- **WHEN** download options change, including a different canonical date boundary or ID boundary
- **THEN** system SHALL ignore stored coverage until a new run rewrites it with a matching options hash

#### Scenario: Equivalent boundary syntax reuses coverage
- **WHEN** two runs use semantically equivalent boundaries such as `20250301:` and `2025-03-01:` or `00123:0456` and `123:456`
- **THEN** system SHALL treat them as coverage-compatible options

#### Scenario: Idempotent incremental run with coverage
- **WHEN** source data has not changed and prior coverage already spans all available in-range timestamp groups
- **THEN** system SHALL download zero new posts while preserving the stored coverage intervals

#### Scenario: Coverage can be disabled explicitly
- **WHEN** user passes `--no-coverage`
- **THEN** system SHALL ignore stored coverage during filtering and SHALL NOT expand coverage during that run

#### Scenario: Out-of-range posts do not affect coverage evaluation
- **WHEN** a scanned post falls outside the active boundary selection
- **THEN** system SHALL ignore it for coverage-hit skipping and SHALL NOT require it to succeed before sealing an in-range timestamp group

## ADDED Requirements

### Requirement: Date boundary filtering
The system SHALL support `--date-boundary START:END` to include only posts whose calendar date falls within the inclusive range when interpreted in the post's original timezone. If a post timestamp has no timezone information, the system SHALL interpret it as CST(+08:00).

#### Scenario: Date boundary includes both endpoints
- **WHEN** user passes `--date-boundary 2025-03-01:2025-03-31`
- **THEN** posts created on `2025-03-01` and `2025-03-31` SHALL both be treated as in-range

#### Scenario: Date boundary may be open-ended
- **WHEN** user passes `--date-boundary 2025-03-01:` or `--date-boundary :2025-03-31`
- **THEN** the omitted side SHALL be treated as unbounded

#### Scenario: Naive timestamp is interpreted as CST
- **WHEN** a post has a naive `created_at` value and user passes `--date-boundary 2025-03-01:2025-03-01`
- **THEN** the system SHALL compare that post against the boundary using the CST calendar date

### Requirement: MID boundary filtering
The system SHALL support `--id-boundary START:END` to include only posts whose `Post.mid` value, interpreted as a non-negative decimal integer, falls within the inclusive range.

#### Scenario: MID boundary includes both endpoints
- **WHEN** user passes `--id-boundary 100:200`
- **THEN** posts with MID `100` and `200` SHALL both be treated as in-range

#### Scenario: MID boundary may be open-ended
- **WHEN** user passes `--id-boundary 100:` or `--id-boundary :200`
- **THEN** the omitted side SHALL be treated as unbounded

#### Scenario: Single-point MID boundary matches exact post
- **WHEN** user passes `--id-boundary 123:123`
- **THEN** only posts whose MID is numerically equal to `123` SHALL be in-range

### Requirement: Combined boundary semantics
When both `--date-boundary` and `--id-boundary` are specified, the system SHALL treat a post as in-range only if it satisfies both boundaries.

#### Scenario: Post must satisfy both boundaries
- **WHEN** a post satisfies the date boundary but not the ID boundary
- **THEN** the system SHALL treat that post as out-of-range

### Requirement: Target-specific boundary traversal
The system SHALL apply boundary selection to all target types, but only `UserTarget` MAY use boundary lower bounds to stop pagination early. `SearchTarget` and `SuperTopicTarget` SHALL always continue scanning until their normal pagination is exhausted, and `MidTarget` SHALL complete successfully even when its single post is out-of-range.

#### Scenario: User timeline lower bound cuts off traversal
- **WHEN** a non-pinned user-timeline post falls below an active lower bound
- **THEN** the system SHALL stop fetching later pages for that target

#### Scenario: Search results do not cut off traversal
- **WHEN** a search result falls outside an active boundary
- **THEN** the system SHALL skip that post but SHALL continue scanning later search results

#### Scenario: Pinned post does not trigger cutoff
- **WHEN** a pinned user-timeline post is out-of-range but a later non-pinned post is still in-range
- **THEN** the system SHALL continue scanning until it reaches a non-pinned cutoff condition or exhausts the timeline

#### Scenario: Out-of-range single-post target succeeds with zero output
- **WHEN** user targets a single post by `-mid` or detail URL and that post is outside the active boundary selection
- **THEN** the target SHALL complete successfully without downloading media or writing metadata

<!-- PBT: narrower_boundary_result ⊆ wider_boundary_result -->
<!-- PBT: equivalent_boundary_syntax => identical_filtered_mid_set -->
<!-- PBT: out_of_range_posts produce no metadata or media side effects -->
<!-- PBT: processed_in_range_count ≤ --count for all runs -->
<!-- PBT: pinned_out_of_range_post never causes earlier loss of later in-range normal posts -->
