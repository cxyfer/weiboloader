## ADDED Requirements

### Requirement: Downloaded media files inherit the normalized post timestamp
The system SHALL set the filesystem `mtime` of each newly downloaded media file to the post timestamp normalized through the same `_cst(post.created_at)` path used for filename rendering.

#### Scenario: Successful media download applies post timestamp
- **WHEN** a media file is downloaded successfully for a post
- **THEN** the landed file SHALL have `mtime` equal to the normalized post timestamp within the filesystem-supported verification tolerance

#### Scenario: Existing non-empty media remains skipped without timestamp mutation
- **WHEN** the target media file already exists and has size greater than zero
- **THEN** the system SHALL report the media item as `SKIPPED` and SHALL NOT modify the existing file `mtime`

#### Scenario: Existing empty media file is replaced and retimestamped
- **WHEN** the target media file already exists but has size equal to zero
- **THEN** the system SHALL redownload the media and SHALL set the landed file `mtime` to the normalized post timestamp

#### Scenario: Media timestamp application failure removes the landed file
- **WHEN** media content has been written to the final destination but applying `mtime` fails
- **THEN** the system SHALL remove the landed media file and SHALL report that media item as failed so a later rerun can retry it

### Requirement: Metadata sidecars inherit the normalized post timestamp with option-aware rewrite semantics
The system SHALL apply the normalized post timestamp to JSON and TXT sidecars, and SHALL choose skip-versus-rewrite behavior based on whether the current run is reusing compatible output semantics.

#### Scenario: Metadata JSON sidecar receives post timestamp
- **WHEN** `--metadata-json` is enabled and a JSON sidecar is written for a post
- **THEN** the system SHALL set that sidecar file `mtime` to the normalized post timestamp within the filesystem-supported verification tolerance

#### Scenario: Post metadata TXT sidecar receives post timestamp
- **WHEN** `--post-metadata-txt` is enabled and a TXT sidecar is written for a post
- **THEN** the system SHALL set that sidecar file `mtime` to the normalized post timestamp within the filesystem-supported verification tolerance

#### Scenario: Compatible rerun preserves existing sidecar and timestamp
- **WHEN** a non-empty sidecar file already exists and the current run reuses compatible output semantics for that post
- **THEN** the system SHALL leave the existing sidecar content and `mtime` unchanged

#### Scenario: Output-affecting option change rewrites existing sidecar
- **WHEN** a post is reprocessed because output-affecting options are incompatible with the saved state
- **THEN** the system SHALL rewrite any existing sidecar content for that post and SHALL set its `mtime` to the normalized post timestamp

#### Scenario: Sidecar timestamp application failure removes the sidecar and fails the target
- **WHEN** sidecar content is written but applying `mtime` fails
- **THEN** the system SHALL remove that sidecar file and SHALL fail the current target instead of continuing with a partially-correct artifact

<!-- PBT: fresh_media_mtime == _cst(post.created_at).timestamp() within tolerance -->
<!-- PBT: skipped_nonempty_media preserves prior mtime exactly -->
<!-- PBT: compatible_rerun keeps sidecar bytes and mtime unchanged -->
<!-- PBT: incompatible_output_options force sidecar rewrite with mtime realignment -->
<!-- PBT: failed_mtime_application leaves no landed media or sidecar artifact behind -->
