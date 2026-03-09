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

### Requirement: Coverage-based incremental updates
The system SHALL persist per-target coverage in `output_dir/.progress` and use it to skip already confirmed timestamp groups on subsequent runs.

#### Scenario: First run creates coverage
- **WHEN** unified progress for a target does not exist
- **THEN** system SHALL download all posts and persist confirmed coverage for completed timestamp groups

#### Scenario: Coverage hit skips post but keeps scanning
- **WHEN** unified progress already covers timestamp `2025-06-01T12:00:00+08:00`
- **THEN** system SHALL skip posts at that timestamp and continue scanning older posts for uncovered gaps

#### Scenario: Coverage advances only after full timestamp-group success
- **WHEN** multiple posts share the same timestamp and all of them complete successfully
- **THEN** system SHALL mark that timestamp group as covered

#### Scenario: Failed or timed-out posts do not advance coverage
- **WHEN** a post in a timestamp group fails or times out
- **THEN** system SHALL NOT advance coverage for that timestamp group

#### Scenario: Idempotent incremental run with coverage
- **WHEN** source data has not changed and prior coverage already spans all available timestamp groups
- **THEN** system SHALL download zero new posts while preserving the stored coverage intervals

#### Scenario: Coverage can be disabled explicitly
- **WHEN** user passes `--no-coverage`
- **THEN** system SHALL ignore stored coverage during filtering and SHALL NOT expand coverage during that run

<!-- PBT: processed_count ≤ --count for all targets -->
<!-- PBT: count=n result set ⊆ count=m result set (m > n) -->
<!-- PBT: unchanged source with full coverage → 0 new downloads -->
<!-- PBT: coverage only advances for fully successful timestamp groups -->
