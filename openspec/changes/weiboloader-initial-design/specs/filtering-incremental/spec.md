## ADDED Requirements

### Requirement: Count limit
The system SHALL support `--count N` to limit the maximum number of posts processed per target.

#### Scenario: Count limit applied
- **WHEN** user passes `--count 50` and target has 200 posts
- **THEN** system SHALL process at most 50 posts and stop

#### Scenario: Count zero means unlimited
- **WHEN** user does not pass `--count` or passes `--count 0`
- **THEN** system SHALL process all available posts

#### Scenario: Available posts fewer than count
- **WHEN** user passes `--count 100` but target only has 30 posts
- **THEN** system SHALL process all 30 posts without error

### Requirement: Fast update mode
When `--fast-update` is enabled, the system SHALL stop fetching posts for a target as soon as it encounters a post whose media files already exist on disk (exists && size > 0).

#### Scenario: Fast update hits existing file
- **WHEN** `--fast-update` is enabled and the 5th post's media file already exists
- **THEN** system SHALL stop processing that target after the 4th post

#### Scenario: Fast update with no existing files
- **WHEN** `--fast-update` is enabled but no files exist on disk
- **THEN** system SHALL process all posts (behaves like normal mode)

### Requirement: Latest stamps for incremental updates
The system SHALL support `--latest-stamps <path>` to record the timestamp of the most recent post downloaded per target. On subsequent runs, only posts newer than the recorded stamp SHALL be downloaded.

#### Scenario: First run with latest-stamps
- **WHEN** `--latest-stamps stamps.json` is used and the file does not exist
- **THEN** system SHALL download all posts and create `stamps.json` with each target's latest post timestamp

#### Scenario: Incremental run with latest-stamps
- **WHEN** `--latest-stamps stamps.json` is used and the file records target A's last stamp as `2025-06-01T12:00:00+08:00`
- **THEN** system SHALL only download posts from target A with timestamp > `2025-06-01T12:00:00+08:00`

#### Scenario: Stamps file round-trip
- **WHEN** stamps file is saved and loaded
- **THEN** the target→timestamp mapping MUST be semantically equivalent, with timestamps as aware CST (+0800)

#### Scenario: Idempotent incremental run
- **WHEN** source data has not changed and `--latest-stamps` is used for a second run
- **THEN** system SHALL download zero new posts and stamps file SHALL remain unchanged

<!-- PBT: processed_count ≤ --count for all targets -->
<!-- PBT: count=n result set ⊆ count=m result set (m > n) -->
<!-- PBT: Load(Save(stamps)) == stamps (round-trip, aware CST) -->
<!-- PBT: second run with unchanged source → 0 new downloads -->
