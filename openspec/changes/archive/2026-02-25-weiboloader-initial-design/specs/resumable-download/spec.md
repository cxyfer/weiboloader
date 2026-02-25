## ADDED Requirements

### Requirement: NodeIterator with freeze/thaw
The system SHALL implement a `NodeIterator` that supports serializing its pagination state (cursor, seen post IDs, options hash) to a JSON checkpoint file, and restoring from it on subsequent runs.

#### Scenario: Freeze state mid-pagination
- **WHEN** iterator has processed 3 pages and is interrupted
- **THEN** system SHALL serialize the current cursor and seen mids to a checkpoint JSON file

#### Scenario: Thaw and resume
- **WHEN** system starts and a valid checkpoint file exists for the target
- **THEN** system SHALL restore iterator state and resume from the saved cursor, without re-fetching already-processed pages

#### Scenario: Freeze/thaw round-trip
- **WHEN** `thaw(freeze(iterator_state))` is applied
- **THEN** the restored iterator MUST produce the same subsequent sequence as the original

#### Scenario: Corrupted checkpoint file
- **WHEN** checkpoint file exists but contains invalid JSON
- **THEN** system SHALL discard the checkpoint, log a warning, and start from the beginning

### Requirement: Atomic checkpoint writes
Checkpoint files MUST be written atomically using tmp-file + rename pattern to prevent corruption from crashes.

#### Scenario: Crash during checkpoint write
- **WHEN** process crashes between writing tmp file and renaming
- **THEN** the previous valid checkpoint MUST remain intact (no partial writes)

#### Scenario: Concurrent access prevention
- **WHEN** two processes attempt to write checkpoints for the same target simultaneously
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
The system SHALL support `--no-resume` to disable checkpoint-based resumption.

#### Scenario: No-resume flag
- **WHEN** user passes `--no-resume`
- **THEN** system SHALL ignore existing checkpoint files and start from the beginning

<!-- PBT: thaw(freeze(state)).next() == state.next() (round-trip) -->
<!-- PBT: freeze without advancing → identical serialized output (idempotent) -->
<!-- PBT: cursor monotonically advances; no mid is yielded twice -->
<!-- PBT: checkpoint file is always valid JSON or absent (atomic write) -->
<!-- PBT: exists && size>0 → skip; size==0 || !exists → download -->
