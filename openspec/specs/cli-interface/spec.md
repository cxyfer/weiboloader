## ADDED Requirements

### Requirement: Prefix-based target parsing
The CLI SHALL parse positional arguments as targets using prefix-based pattern matching with the following priority (highest first):
1. URL — starts with `http://` or `https://` (extract mid from `m.weibo.cn/detail/<mid>`)
2. Mid — passed via `-mid <mid>` flag
3. SuperTopic — starts with `#` (name or containerid)
4. Search — starts with `:` (keyword search)
5. User — all other strings (UID if all digits, otherwise nickname)

#### Scenario: Parse user UID
- **WHEN** target is `1234567890` (all digits)
- **THEN** system SHALL classify it as User target with UID `1234567890`

#### Scenario: Parse user nickname
- **WHEN** target is `SomeNickname` (not all digits, no prefix)
- **THEN** system SHALL classify it as User target with nickname `SomeNickname`

#### Scenario: Parse supertopic by name
- **WHEN** target is `#超話名稱`
- **THEN** system SHALL classify it as SuperTopic target and search for the topic name `超話名稱`

#### Scenario: Parse supertopic by containerid
- **WHEN** target is `#100808abcdef123`
- **THEN** system SHALL classify it as SuperTopic target with containerid `100808abcdef123`

#### Scenario: Parse single post by mid flag
- **WHEN** user passes `-mid 5120123456789`
- **THEN** system SHALL classify it as Mid target with mid `5120123456789`

#### Scenario: Parse single post by URL
- **WHEN** target is `https://m.weibo.cn/detail/5120123456789`
- **THEN** system SHALL classify it as Mid target with mid `5120123456789`

#### Scenario: Parse search keyword
- **WHEN** target is `:some keyword`
- **THEN** system SHALL classify it as Search target with keyword `some keyword`

#### Scenario: Priority disambiguation
- **WHEN** target is `https://m.weibo.cn/detail/123#fragment`
- **THEN** system SHALL classify it as URL (Mid target), NOT as SuperTopic

### Requirement: Batch target processing with fault isolation
The CLI SHALL process multiple targets sequentially. A failure on one target MUST NOT prevent processing of subsequent targets.

#### Scenario: Mixed success and failure
- **WHEN** user runs `weiboloader user1 user2 user3` and `user2` fails (e.g., UID not found)
- **THEN** system SHALL process `user1`, report error for `user2`, continue to process `user3`

#### Scenario: All targets succeed
- **WHEN** all targets complete without error
- **THEN** system SHALL exit with code `0`

### Requirement: Structured exit codes
The CLI SHALL return exit codes matching instaloader convention:
- `0` — all targets succeeded
- `1` — at least one target failed, others may have succeeded
- `2` — initialization failure (invalid arguments, missing dependencies)
- `3` — authentication failure (no valid cookie, SUB missing)
- `5` — user interrupt (KeyboardInterrupt / SIGINT)

#### Scenario: Partial failure exit code
- **WHEN** 2 of 3 targets succeed and 1 fails
- **THEN** system SHALL exit with code `1`

#### Scenario: Auth failure exit code
- **WHEN** no valid cookie is available and authentication is required
- **THEN** system SHALL exit with code `3`

#### Scenario: User interrupt exit code
- **WHEN** user presses Ctrl+C during download
- **THEN** system SHALL flush unified progress (`resume` + confirmed `coverage`) and exit with code `5`

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

<!-- PBT: exit_code ∈ {0,1,2,3,5} for all possible argv combinations -->
<!-- PBT: batch order permutation SHALL NOT change per-target success/failure classification -->
