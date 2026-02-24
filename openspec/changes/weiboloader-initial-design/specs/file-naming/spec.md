## ADDED Requirements

### Requirement: Directory pattern
The system SHALL organize downloaded files into directories based on a configurable pattern via `--dirname-pattern`. Default patterns:
- User targets: `./{nickname}/`
- SuperTopic targets: `./topic/{topic_name}/`
- Search targets: `./search/{keyword}/`

#### Scenario: Default user directory
- **WHEN** downloading user `SomeUser` without `--dirname-pattern`
- **THEN** files SHALL be saved under `./SomeUser/`

#### Scenario: Default supertopic directory
- **WHEN** downloading supertopic `MyTopic` without `--dirname-pattern`
- **THEN** files SHALL be saved under `./topic/MyTopic/`

#### Scenario: Custom dirname pattern
- **WHEN** user passes `--dirname-pattern "{uid}_{nickname}"`
- **THEN** files SHALL be saved under `./{uid}_{nickname}/`

### Requirement: Filename pattern with template variables
The system SHALL support filename templates via `--filename-pattern` with the following variables: `{nickname}`, `{uid}`, `{mid}`, `{bid}`, `{date}`, `{date:FORMAT}`, `{index}`, `{index:PAD}`, `{text}`, `{type}`. Default pattern: `{date}_{name}`.

#### Scenario: Default filename
- **WHEN** downloading without `--filename-pattern`
- **THEN** filenames SHALL follow `{date}_{name}` pattern

#### Scenario: Custom filename with date format
- **WHEN** user passes `--filename-pattern "{date:%Y%m%d}_{mid}_{index:02}"`
- **THEN** filenames SHALL be formatted as e.g. `20250101_5120123456789_01.jpg`

#### Scenario: Index padding
- **WHEN** `{index:03}` is used and index is 5
- **THEN** output SHALL be `005`

### Requirement: Filename sanitization
The system SHALL remove characters `\/:*?"<>|` from all generated filenames and directory names. The `{text}` variable MUST be truncated to 50 characters.

#### Scenario: Illegal characters in nickname
- **WHEN** nickname contains `User/Name*Special`
- **THEN** sanitized dirname SHALL be `UserNameSpecial`

#### Scenario: Text truncation
- **WHEN** `{text}` value is 80 characters long
- **THEN** system SHALL truncate to first 50 characters

#### Scenario: Sanitization idempotency
- **WHEN** `sanitize(sanitize(x))` is applied
- **THEN** result MUST equal `sanitize(x)`

#### Scenario: All-illegal-character filename
- **WHEN** all characters in a template variable are illegal (e.g., `???`)
- **THEN** system SHALL produce a non-empty fallback filename (e.g., using mid)

<!-- PBT: ∀ generated paths, path contains no char in {\/:*?"<>|} -->
<!-- PBT: sanitize(sanitize(x)) == sanitize(x) -->
<!-- PBT: len({text} after substitution) ≤ 50 -->
