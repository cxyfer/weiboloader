## MODIFIED Requirements

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

<!-- PBT: equivalent_canonical_boundaries => identical_options_hash -->
<!-- PBT: changed_canonical_boundary => incompatible_resume_and_coverage_reuse -->
