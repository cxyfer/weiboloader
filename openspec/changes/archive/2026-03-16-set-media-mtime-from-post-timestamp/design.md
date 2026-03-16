## Context

`weiboloader` already parses `Post.created_at` as a timezone-aware timestamp and uses `_cst(post.created_at)` when rendering the `{date}` filename token. Newly downloaded media files, JSON sidecars, and TXT sidecars do not currently align their filesystem `mtime` to that same post timestamp, which leaves file-manager ordering and manual verification inconsistent with the semantic time already encoded in filenames.

This change is intentionally local to the file-materialization boundary in `weiboloader/weiboloader.py`. The data model in `weiboloader/adapter.py` and `weiboloader/structures.py` already provides the required timestamp contract, and the proposal explicitly excludes CLI, progress-schema, and traversal-semantics changes.

The current loader flow also matters for correctness:
- media downloads use `exists && size > 0 => SKIPPED` semantics;
- `fast_update` can stop before sidecar writes are reached;
- covered posts are skipped only when the saved output options hash is compatible with the current run;
- output-affecting option changes already force post reprocessing by invalidating coverage reuse.

The stakeholder is a CLI user who expects the post timestamp to have one meaning across filenames, file metadata, and reruns, without introducing silent side effects or stale sidecar content.

## Goals / Non-Goals

**Goals:**
- Align landed media files and metadata sidecars with the same normalized post timestamp already used for filename rendering.
- Preserve existing media skip semantics: existing non-empty media stays `SKIPPED` and must not have its `mtime` mutated.
- Make sidecar behavior idempotent when output semantics are unchanged, while still rewriting stale sidecars when output-affecting options changed and forced the post to be reprocessed.
- Treat `mtime` application as part of successful file materialization so partially-correct artifacts are not silently accepted.
- Keep verification mechanically testable across filesystems by comparing epoch values with a small tolerance instead of string formatting.

**Non-Goals:**
- Do not change how `Post.created_at` is parsed, normalized, or stored outside the loader boundary.
- Do not add CLI flags, progress fields, or migration logic for historical files.
- Do not backfill untouched old files or modify directory timestamps.
- Do not change `fast_update`, resume, or coverage semantics beyond the file-output behavior required by this change.

## Decisions

### D1. Use one timestamp normalization path for filenames and filesystem metadata

Filesystem `mtime` must be derived from the same normalized timestamp path as `{date}` filenames: `_cst(post.created_at)`. This avoids split semantics where filenames and file metadata could disagree because of different timezone conversions.

**Alternatives considered:**
- **Call `datetime.timestamp()` directly on raw `post.created_at`:** simpler, but risks divergence from the filename path if naive timestamps or future normalization changes appear.
- **Use local machine timezone for `mtime`:** rejected because filesystem metadata would become environment-dependent while filenames remain tied to `_cst()`.

### D2. Apply media `mtime` after the final file lands, and fail closed on timestamp errors

Media timestamping belongs to the successful completion boundary of a downloaded artifact: after the temp file is fsynced and moved into place. If `mtime` application fails after content lands, the media artifact must be treated as failed and the final destination removed so a later rerun can recover automatically instead of being trapped behind `SKIPPED` semantics.

The existing `_download(url, dest)` call surface should remain stable so current tests and monkeypatches do not need a broad signature migration. Timestamp application can therefore be attached through a wrapper or equivalent post-download boundary without changing the low-level download contract.

**Alternatives considered:**
- **Warn and keep the downloaded file:** rejected because reruns would likely classify the file as `SKIPPED`, permanently preserving an incorrect `mtime`.
- **Pass `created_at` into `_download()` directly:** workable, but higher churn with no behavioral benefit over a wrapper boundary.

### D3. Sidecars are skip-aware under compatible output semantics and rewrite-aware under incompatible output semantics

JSON and TXT sidecars should follow two rules:
- if the current run is operating under output semantics compatible with the saved checkpoint state and the sidecar already exists with non-zero size, the sidecar should be skipped and its existing `mtime` preserved;
- if the post is being reprocessed because output-affecting options are incompatible with the saved state, existing sidecars must be rewritten so content and `mtime` reflect the current output semantics.

This preserves idempotency for ordinary reruns while avoiding stale sidecars when `metadata_json`, `post_metadata_txt`, naming, or other output-affecting options force a legitimate replay.

**Alternatives considered:**
- **Always rewrite sidecars:** rejected because repeated compatible reruns would keep mutating files unnecessarily.
- **Always skip existing sidecars:** rejected because output-affecting option changes could leave sidecar contents permanently stale.

### D4. Sidecar `mtime` failure aborts the current target

Sidecar content and its aligned `mtime` are both part of the sidecar contract. If a sidecar write succeeds but `mtime` application fails, the current target should fail closed rather than continuing with a partially-correct artifact.

Unlike media downloads, sidecars are written synchronously as part of post processing, so failing the current target is sufficient to allow a future rerun to repair the artifact.

**Alternatives considered:**
- **Warn and keep the sidecar:** rejected because it silently weakens the guarantee that filesystem timestamps reflect post time.

### D5. Verification uses tolerant epoch comparison, not exact string equality

Regression tests should compare `st_mtime` to the expected normalized epoch with a small tolerance so the spec remains portable across filesystems with different timestamp precision.

**Alternatives considered:**
- **Require exact equality:** rejected because second-level or coarser timestamp precision can make otherwise-correct behavior flaky on some platforms.

### D6. No backfill for untouched files

This change only affects artifacts that are actually materialized or intentionally reprocessed during the current run. Files skipped because they are already covered, skipped media, and directory mtimes are outside the mutation scope.

**Alternatives considered:**
- **Retroactively repair all previously downloaded files:** rejected because it would expand this bug fix into a filesystem migration with different performance and UX implications.

## Risks / Trade-offs

- **Compatible reruns will leave previously incorrect sidecar `mtime` untouched** → Accept because the chosen contract favors idempotency when output semantics did not change; backfill behavior is a separate change.
- **Fail-closed `mtime` handling can surface more target failures than today** → Prefer explicit failure over silently persisting partially-correct artifacts.
- **Sidecar rewrite logic depends on output-option compatibility state** → Keep the decision tied to the existing options-hash compatibility model so no new persistence schema is required.
- **Filesystem timestamp precision varies by platform** → Use tolerance-based assertions in regression tests and compare epoch values instead of formatted strings.

## Migration Plan

1. Add a loader-level helper that derives the target epoch from `_cst(post.created_at)` and applies it to landed files.
2. Attach media timestamp application at the post-download boundary without changing the public `_download(url, dest)` test surface.
3. Update JSON/TXT sidecar writes to decide between skip and rewrite based on output-option compatibility and then apply `mtime` on successful writes.
4. Add regression tests for media alignment, sidecar alignment, compatible-rerun idempotency, incompatible-option sidecar rewrites, and fail-closed timestamp errors.
5. Roll back by removing the timestamp-application boundary while leaving progress data untouched, because this change does not add or mutate checkpoint schema.

## Open Questions

None. The sidecar overwrite policy, failure handling, and test precision rules are resolved by this design.
