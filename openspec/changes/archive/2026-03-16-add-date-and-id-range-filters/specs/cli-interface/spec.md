## ADDED Requirements

### Requirement: Boundary flag parsing
The CLI SHALL accept `-b/--date-boundary START:END` and `-B/--id-boundary START:END` as optional post-selection flags.

#### Scenario: Parse boundary flags
- **WHEN** user runs `weiboloader -b 2025-03-01:2025-03-31 -B 100:200 123456`
- **THEN** the CLI SHALL parse both boundary flags and start the download with both active boundaries

### Requirement: Canonical boundary validation
The CLI SHALL canonicalize equivalent boundary inputs before execution and SHALL reject invalid boundary syntax during initialization.

#### Scenario: Canonicalize equivalent date formats
- **WHEN** user passes `--date-boundary 20250301:2025-03-31`
- **THEN** the CLI SHALL treat it as the same boundary as `--date-boundary 2025-03-01:2025-03-31`

#### Scenario: Canonicalize ID leading zeros
- **WHEN** user passes `--id-boundary 00123:0456`
- **THEN** the CLI SHALL treat it as the same boundary as `--id-boundary 123:456`

#### Scenario: No-op boundary is treated as unset
- **WHEN** user passes `--date-boundary :` or `--id-boundary :`
- **THEN** the CLI SHALL treat that boundary as unset

#### Scenario: Reject descending boundary range
- **WHEN** user passes a boundary whose `START` is greater than its `END`
- **THEN** the CLI SHALL reject the arguments during initialization and exit with code `2`

#### Scenario: Reject invalid boundary endpoint
- **WHEN** user passes an invalid date endpoint or a non-decimal ID endpoint
- **THEN** the CLI SHALL reject the arguments during initialization and exit with code `2`

### Requirement: Boundary help text
The CLI SHALL describe both boundary flags as inclusive `START:END` ranges with open-ended omission semantics.

#### Scenario: Help output documents boundary syntax
- **WHEN** user runs `weiboloader --help`
- **THEN** the help text SHALL mention `-b/--date-boundary` and `-B/--id-boundary`, their inclusive endpoint semantics, and the supported date formats `YYYYMMDD` and `YYYY-MM-DD`
