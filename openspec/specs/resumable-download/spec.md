## Purpose
Define resumable download progress persistence, rerun semantics, and atomic checkpoint behavior.
## Requirements
### Requirement: Unified progress store
The system SHALL persist per-target unified progress in `output_dir/.progress` as a single schema-version-3 JSON record. The record MUST keep `resume` and `coverage` as independent, options-aware state components: `resume` MUST represent the exact unfinished iterator frontier, including the current page's unconsumed suffix snapshot, while `coverage` MUST represent only fully sealed successful timestamp ranges. The compatibility `options_hash` for both components SHALL include output-affecting options and the canonical date/id boundary selection, where omitted boundaries and `:` are equivalent.

#### Scenario: Freeze exact resume state mid-pagination
- **WHEN** iteration stops after some posts from the current page have already been processed and the current post does not time out
- **THEN** the unified progress record SHALL serialize the exact frontier needed to replay the remaining unconsumed suffix of that same page before any later page is fetched

#### Scenario: Thaw and resume exact remaining suffix when options are compatible
- **WHEN** system starts and a valid schema-version-3 unified progress record exists with a matching `resume` `options_hash`
- **THEN** system SHALL restore the saved frontier, replay the saved current-page suffix before fetching later pages, and SHALL NOT skip any unseen post from the interrupted page

#### Scenario: Preserve latest exact frontier at non-terminal stop points
- **WHEN** download stops because of `--count`, `--fast-update`, or interruption after successfully processing at least one post in the current unfinished run
- **THEN** system SHALL persist the latest exact `resume` frontier rather than reverting to an older sealed-group checkpoint

#### Scenario: Ignore incompatible resume state independently
- **WHEN** unified progress contains `resume` with a different `options_hash`, including a different canonical date boundary or ID boundary
- **THEN** system SHALL ignore the stored `resume` state and start iteration from the beginning while still evaluating stored `coverage` independently

#### Scenario: Ignore incompatible coverage state independently
- **WHEN** unified progress contains `coverage` with a different `options_hash` or missing `options_hash`
- **THEN** system SHALL ignore the stored `coverage` intervals while still evaluating stored `resume` independently

#### Scenario: Equivalent boundary syntax keeps compatibility
- **WHEN** two runs use semantically equivalent boundaries such as `20250301:` and `2025-03-01:` or `00123:0456` and `123:456`
- **THEN** the system SHALL treat them as the same `options_hash` input for both `resume` and `coverage`

#### Scenario: No-op boundary keeps compatibility
- **WHEN** user adds `--date-boundary :` or `--id-boundary :` without changing any other compatible option
- **THEN** the system SHALL treat that boundary as unset and SHALL NOT invalidate otherwise compatible `resume` or `coverage`

#### Scenario: Reject legacy checkpoint schemas
- **WHEN** unified progress file is missing `version`, has `version` other than `3`, or does not match the schema-version-3 resume payload
- **THEN** system SHALL ignore both stored `resume` and stored `coverage` and restart the target from a clean state

#### Scenario: Flush sealed progress at every stop point
- **WHEN** download stops because the target completes, fails, is interrupted, hits `--count`, or triggers `--fast-update`
- **THEN** system SHALL atomically persist the unified progress record with all sealed successful coverage runs and the stop-appropriate `resume` state

### Requirement: Atomic unified progress writes
Unified progress files MUST be written atomically using tmp-file + rename, and checkpoint persistence failures MUST fail closed so the system never continues a target under the false assumption that progress was saved.

#### Scenario: Crash during unified progress write
- **WHEN** process crashes between writing the temporary file and renaming it
- **THEN** the previous valid unified progress file MUST remain intact

#### Scenario: Concurrent access fails fast
- **WHEN** another process already holds the lock for the same target
- **THEN** system SHALL fail the current target with `CheckpointError` instead of continuing without durable progress

#### Scenario: Save-path failure aborts current target
- **WHEN** temporary file creation, JSON serialization, fsync, or rename fails during unified progress save
- **THEN** system SHALL raise `CheckpointError`, leave the last durable checkpoint unchanged, and stop downloading the current target

#### Scenario: Other targets may continue after checkpoint failure
- **WHEN** one target in a multi-target run fails with `CheckpointError`
- **THEN** system SHALL mark that target as failed while allowing later targets in the same invocation to continue

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

### Requirement: Group-atomic coverage sealing
The system SHALL materialize `coverage` only from fully successful timestamp groups normalized by `_cst(post.created_at)`, and SHALL preserve gaps across failed, unfinished, or non-monotonic groups.

#### Scenario: Seal only fully successful group
- **WHEN** every post in a timestamp group finishes successfully
- **THEN** system SHALL add that group's timestamp to the currently sealable coverage run

#### Scenario: Failed group remains uncovered
- **WHEN** any post in a timestamp group fails or times out
- **THEN** system SHALL leave that group outside coverage and SHALL NOT advance `resume` past the failed gap

#### Scenario: Unfinished current group stays unsealed
- **WHEN** download stops before the current timestamp group is fully processed
- **THEN** system SHALL NOT write that unfinished group into coverage

#### Scenario: Monotonicity break splits coverage
- **WHEN** observed normalized timestamps break the expected monotonic order between otherwise successful groups
- **THEN** system SHALL commit the current sealed run and start a new coverage interval rather than bridging across the break

### Requirement: Coverage-aware rerun semantics
The system SHALL distinguish fully covered posts from uncovered-but-landed posts during reruns with compatible options so that skipped download work remains observable and coverage remains truthful.

#### Scenario: Skip covered post before processing
- **WHEN** a post's normalized timestamp is already inside materialized coverage
- **THEN** system SHALL skip the post before scheduling media downloads or metadata output for that rerun

#### Scenario: Revisit uncovered landed post honestly
- **WHEN** a post lies outside coverage but one or more of its target media files already exist with size > 0
- **THEN** system SHALL still process the post and SHALL report each existing media item as `SKIPPED` instead of re-downloading it

#### Scenario: Successful rerun can seal uncovered landed group
- **WHEN** an uncovered timestamp group is revisited and every media item either downloads successfully or reports `SKIPPED`
- **THEN** system SHALL treat the group as successful and allow it to enter coverage

<!-- PBT: thaw(freeze(state_after_k)) replays the exact remaining suffix -->
<!-- PBT: coverage never includes failed or unfinished timestamp groups -->
<!-- PBT: save failure preserves the last durable checkpoint bytes -->
<!-- PBT: equivalent_canonical_boundaries => identical_options_hash -->
<!-- PBT: changed_canonical_boundary => incompatible_resume_and_coverage_reuse -->
