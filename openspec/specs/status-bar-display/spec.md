## CHANGED Requirements

### Requirement: Status bar displays filename and POST index

The system SHALL display enriched progress information during media downloads, including the current POST index and the filename being processed.

#### Scenario: Normal media download with full context
- **WHEN** a media file download completes (success, skip, or fail)
- **THEN** the status bar SHALL display `[#N] Media X/Y - filename.ext`
  - `N` = 1-based index of the current POST being processed (`posts_processed + 1`)
  - `X` = number of media items completed so far in this POST
  - `Y` = total media items in this POST
  - `filename.ext` = `dest.name` (the final filename from the destination Path)

#### Scenario: Filename contains Rich markup characters
- **WHEN** filename contains `[`, `]`, or other Rich markup characters
- **THEN** the system SHALL escape the filename via `rich.markup.escape()` before rendering
- **AND** the status bar SHALL render correctly without corruption

#### Scenario: Backward compatibility with missing fields
- **WHEN** `UIEvent` is constructed without `post_index` or `filename` (both default to `None`)
- **THEN** `NullSink.emit()` SHALL accept the event without error
- **AND** `RichSink._handle()` SHALL fall back to current format:
  - No `post_index` → omit `[#N]` prefix
  - No `filename` → omit `- filename.ext` suffix
  - Both missing → render `Media X/Y` (identical to current behavior)

### Requirement: UIEvent dataclass extension

The `UIEvent` dataclass SHALL be extended with two optional fields appended after the existing `ok` field.

#### Field specification
- `filename: str | None = None` — basename of the destination file
- `post_index: int | None = None` — 1-based index of the current POST

#### Constraint: slots compatibility
- Fields MUST be appended at the end of the dataclass to maintain positional argument compatibility with `slots=True`

### Requirement: Future-to-path mapping for filename resolution

The download loop SHALL use a `dict[Future, Path]` mapping instead of a plain list to enable filename lookup for both success and exception branches.

#### Scenario: Successful download
- **WHEN** a future completes with a `DownloadResult`
- **THEN** the emitted `MEDIA_DONE` event SHALL include `filename=future_to_path[f].name`

#### Scenario: Future raises exception
- **WHEN** a future raises an exception
- **THEN** the emitted `MEDIA_DONE` event SHALL include `filename=future_to_path[f].name`

---

## PBT Properties

### P1.1: Backward compatibility invariant
- **Property**: `∀ UIEvent where filename=None ∧ post_index=None, RichSink output == current format`
- **Falsification**: Construct UIEvent with only legacy fields, compare rendered description against `f"Media {done}/{total}"`

### P1.2: Rich markup safety
- **Property**: `∀ filename ∈ arbitrary_text(), RichSink._handle() does not raise`
- **Falsification**: Generate filenames with `[`, `]`, `{`, `}`, `\`, and Rich markup sequences

### P1.3: Format string invariant
- **Property**: `∀ (post_index, media_done, media_total, filename) where all present, output matches r"\[#\d+\] Media \d+/\d+ - .+"`
- **Falsification**: Generate random positive integers and non-empty strings, verify regex match

### P1.4: Event field completeness
- **Property**: `∀ MEDIA_DONE event emitted during download_target(), event.post_index is not None ∧ event.filename is not None`
- **Falsification**: Mock download_target with N posts × M media, collect events, verify all MEDIA_DONE have both fields
