## ADDED Requirements

### Requirement: Media type filtering
The system SHALL download both pictures and videos by default. Users can exclude media types via `--no-videos` and `--no-pictures` flags.

#### Scenario: Default download (pictures + videos)
- **WHEN** user runs `weiboloader <target>` without media type flags
- **THEN** system SHALL download both pictures and videos

#### Scenario: Pictures only
- **WHEN** user passes `--no-videos`
- **THEN** system SHALL download only pictures, skipping all video media

#### Scenario: Videos only
- **WHEN** user passes `--no-pictures`
- **THEN** system SHALL download only videos, skipping all picture media

#### Scenario: Flag order independence
- **WHEN** user passes `--no-videos --no-pictures` in any order
- **THEN** system SHALL download no media files (metadata-only mode if metadata flags are set)

### Requirement: Picture resolution selection
The system SHALL always download the largest available picture size using `pic.large.url` from the API response.

#### Scenario: Large picture available
- **WHEN** a post contains pictures with `large` variant
- **THEN** system SHALL download from `pic['large']['url']`

### Requirement: Video resolution priority
The system SHALL select video URLs in the following priority order: `stream_url_hd` > `mp4_720p_mp4` > `mp4_hd_url` > `stream_url`.

#### Scenario: HD video available
- **WHEN** a post's `media_info` contains `stream_url_hd`
- **THEN** system SHALL download from `stream_url_hd`

#### Scenario: Only SD video available
- **WHEN** a post's `media_info` only contains `stream_url`
- **THEN** system SHALL download from `stream_url`

### Requirement: Raw metadata JSON export
When `--metadata-json` is enabled, the system SHALL save the original raw JSON payload from the API for each post as a `.json` file alongside the media files.

#### Scenario: Metadata JSON enabled
- **WHEN** user passes `--metadata-json`
- **THEN** for each downloaded post, system SHALL write the raw API JSON to `{dirname}/{mid}.json`

#### Scenario: Metadata JSON round-trip
- **WHEN** a metadata JSON file is written and read back
- **THEN** the deserialized content MUST be structurally equivalent to the original API payload

### Requirement: Post metadata text export
When `--post-metadata-txt` is enabled with a format string, the system SHALL write a `.txt` file per post using the provided template.

#### Scenario: Text metadata with template
- **WHEN** user passes `--post-metadata-txt "{date}: {text}"`
- **THEN** system SHALL write a `.txt` file with the formatted content for each post

<!-- PBT: --no-videos → download set contains zero video items -->
<!-- PBT: --no-pictures → download set contains zero picture items -->
<!-- PBT: metadata JSON round-trip: json.loads(written_file) == original_payload -->
