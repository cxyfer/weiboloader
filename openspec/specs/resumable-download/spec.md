## ADDED Requirements

### Requirement: Unified progress store
The system SHALL persist per-target unified progress in `output_dir/.progress`, combining iterator `resume` state and incremental `coverage` state in a single JSON record. Both `resume` and `coverage` are **options-aware** and only apply when download options match their stored hashes.

#### Scenario: Freeze resume state mid-pagination
- **WHEN** iterator has processed part of a target and the current post does not time out
- **THEN** system SHALL serialize the current cursor and seen mids into the target's unified progress record

#### Scenario: Thaw and resume when options are compatible
- **WHEN** system starts and a valid unified progress record exists for the target with matching `options_hash`
- **THEN** system SHALL restore iterator state and resume from the saved cursor, without re-fetching already-processed pages

#### Scenario: Ignore incompatible resume state
- **WHEN** unified progress contains `resume` with a different `options_hash`
- **THEN** system SHALL ignore the stored `resume` state and start from the beginning for iteration

#### Scenario: Ignore incompatible coverage state
- **WHEN** unified progress contains `coverage` with a different `options_hash` or missing `options_hash`
- **THEN** system SHALL ignore the stored `coverage` intervals until new run rewrites them with matching hash

#### Scenario: All stop points flush sealed runs
- **WHEN** download stops (target complete, failure, Ctrl+C, `--count` limit, `--fast-update` early stop)
- **THEN** system SHALL flush all sealed successful runs to `coverage` and persist unified progress atomically

#### Scenario: Corrupt unified progress file
- **WHEN** unified progress file exists but contains invalid JSON
- **THEN** system SHALL discard the file, log a warning, and start from the beginning

### Requirement: Atomic unified progress writes
Unified progress files MUST be written atomically using tmp-file + rename pattern to prevent corruption from crashes.

#### Scenario: Crash during unified progress write
- **WHEN** process crashes between writing tmp file and renaming
- **THEN** the previous valid unified progress file MUST remain intact (no partial writes)

#### Scenario: Concurrent access prevention
- **WHEN** two processes attempt to write unified progress for the same target simultaneously
- **THEN** system SHALL use a lock file to ensure mutual exclusion; the second process MUST fail-fast or wait

### Requirement: File existence skip
The system SHALL skip downloading media files that already exist on disk with size > 0 bytes.

#### Scenario: File exists with content
- **WHEN** target file exists and has size > 0
- **THEN** system SHALL skip the download and not make an HTTP request

#### Scenario: File exists but empty
- **WHEN** target file exists but has size == 0
- **THEN** system SHALL re-download the file (treat as incomplete)

#### Scenario: File does not exist
- **WHEN** target file does not exist
- **THEN** system SHALL download the file

### Requirement: Partial download protection
Media downloads MUST be written to a `.part` temporary file first, then renamed to the final filename upon completion.

#### Scenario: Download completes successfully
- **WHEN** media file download finishes without error
- **THEN** system SHALL rename `{filename}.part` to `{filename}`

#### Scenario: Download interrupted
- **WHEN** download is interrupted mid-transfer
- **THEN** the `.part` file SHALL remain on disk; the final filename SHALL NOT exist

### Requirement: Disable resume option
The system SHALL support `--no-resume` to disable restoring iterator cursor state from unified progress.

#### Scenario: No-resume flag
- **WHEN** user passes `--no-resume`
- **THEN** system SHALL ignore existing `resume` state and start iteration from the beginning, while leaving stored `coverage` behavior unchanged

<!-- PBT: thaw(freeze(state)).next() == state.next() (round-trip) -->
<!-- PBT: freeze without advancing → identical serialized output (idempotent) -->
<!-- PBT: cursor monotonically advances; no mid is yielded twice -->
<!-- PBT: unified progress file is always valid JSON or absent (atomic write) -->
<!-- PBT: exists && size>0 → skip; size==0 || !exists → download -->
