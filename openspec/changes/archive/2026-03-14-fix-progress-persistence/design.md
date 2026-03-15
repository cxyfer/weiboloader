## Context

`weiboloader` currently persists per-target progress as a unified JSON record containing iterator `resume` state and time-based `coverage`. This model is intended to support interruption recovery, `--count`-limited reruns, and `--fast-update` early stops without re-downloading completed media.

The proposal identifies three correctness gaps in the current semantics:
- partial-page resume can advance too far and skip unseen posts when a run stops mid-page;
- progressively increasing `--count` can resume from an older safe checkpoint instead of the latest successful frontier;
- coverage can overstate success if it bridges across failed or unfinished groups.

This change is cross-cutting across `weiboloader/nodeiterator.py`, `weiboloader/weiboloader.py`, and `weiboloader/progress.py`, and it also requires regression tests for resumed reruns and failure handling. The main stakeholder is a CLI user who relies on resumable downloads to be monotonic and trustworthy when expanding the requested history over multiple runs.

## Goals / Non-Goals

**Goals:**
- Define unambiguous semantics for `resume` versus `coverage` inside the unified progress file.
- Guarantee monotonic recovery for non-terminal stops such as `--count`, `--fast-update`, and interruption.
- Preserve partial-page and cross-page same-group recovery without skipping unseen posts or reusing stale checkpoints.
- Ensure coverage only represents fully sealed successful ranges and never hides failed gaps.
- Make checkpoint persistence failures fail closed so the user is not left with silently stale resume state.
- Define rerun behavior for already-landed files that are not yet covered so user-visible progress remains predictable.

**Non-Goals:**
- Do not redesign progress persistence into a per-post ledger or any new storage backend.
- Do not migrate legacy v1 progress files; incompatible checkpoints will be ignored.
- Do not optimize `seen_mids` growth or introduce compaction in this change.
- Do not alter unrelated download, filtering, or metadata behavior beyond the option-compatibility rules required for correct resume/coverage reuse.

## Decisions

### D1. Keep a unified progress record, but give `resume` and `coverage` separate semantics

The progress file remains a single per-target JSON record, but its two state components are explicitly separated:
- `resume` represents the in-flight iterator frontier needed to continue unfinished work.
- `coverage` represents only fully sealed successful time ranges that are safe to skip wholesale on rerun.

This keeps the existing file layout and compatibility model while removing the ambiguity that previously allowed resume and coverage to be treated as interchangeable notions of progress.

**Alternatives considered:**
- **Single safe-checkpoint model:** simpler on paper, but it cannot express partial-page recovery and sealed coverage independently.
- **Per-post durable ledger:** would provide finer-grained recovery, but it is a much larger architectural change than this bug fix requires.

### D2. Freeze/thaw must preserve the current page frontier exactly

`NodeIterator.freeze()` must serialize the currently consumable frontier, not the next page frontier. If iteration stops mid-page, thawing must replay exactly the unseen suffix of that same page before any page advance occurs.

This decision formalizes the delayed page-transition behavior already needed for correctness when rerunning with a larger `--count` or when a timestamp group spans page boundaries.

**Alternatives considered:**
- **Persist the next page cursor immediately after fetch:** simpler state transitions, but it can skip unseen posts when a run stops before draining the current buffer.

### D3. Coverage sealing is group-atomic and gap-preserving

Coverage advances only when a timestamp group is fully successful. A failed or unfinished group must never enter coverage, and coverage materialization must not bridge across:
- a failed group,
- an unfinished group,
- or a monotonicity break in the observed timestamp order.

The persisted intervals therefore represent the normalized union of maximal sealed successful runs, not an optimistic approximation of “mostly completed” time ranges.

**Alternatives considered:**
- **Best-effort interval merging across nearby successes:** reduces rerun work, but risks permanently hiding gaps and contradicts the proposal’s requirement that coverage reflect only real completed ranges.

### D4. Rerun behavior distinguishes covered posts from uncovered-but-landed posts

On rerun with compatible options:
- posts whose timestamps are already within materialized coverage are skipped before any download work begins;
- posts outside coverage still go through normal post processing;
- for those posts, any media file that already exists with size `> 0` is reported as `SKIPPED` item-by-item rather than re-downloaded.

This preserves correctness for partially completed groups while keeping the observable behavior honest: the user can still see why a post was revisited, and successfully skipped items can still contribute to sealing the group into coverage.

**Alternatives considered:**
- **Silently skip entire uncovered-but-landed posts:** cleaner output, but it requires a stronger completeness proof than this design can safely provide.

### D5. Resume and coverage reuse remain independently hash-gated

`resume` and `coverage` continue to be guarded by option hashes, but reuse is evaluated independently for each component. Output-affecting options must invalidate reuse because they change what “complete” means for a post. Traversal-only options must not invalidate reuse.

For this change:
- output-affecting options include `dirname_pattern`, `filename_pattern`, `no_videos`, `no_pictures`, `metadata_json`, and `post_metadata_txt`;
- traversal-only options include `count` and `fast_update`.

This preserves the ability to expand `--count` or toggle `--fast-update` without discarding valid progress, while still forcing revisits when artifact layout or content expectations change.

**Alternatives considered:**
- **Hash all CLI options:** simple to implement, but would unnecessarily destroy valid progress reuse for shallow-to-deep reruns.
- **Hash only media-selection flags:** too weak, because metadata and naming outputs would become stale.

### D6. Legacy checkpoints are invalidated instead of migrated

Any legacy v1 checkpoint is treated as fully incompatible. When `load()` sees a missing or mismatched version, it must ignore both stored `resume` and stored `coverage` and restart the target from a clean state.

This intentionally chooses one-time rescan cost over introducing best-effort migration logic for data whose old semantics do not match the new guarantees.

**Alternatives considered:**
- **Best-effort v1 → v2 migration:** reduces one-time user cost, but increases implementation and testing complexity for a bug-fix change whose goal is to tighten semantics.

### D7. Progress persistence failures must fail closed

Checkpoint persistence is part of the correctness boundary. Lock acquisition failure or any write-path failure during save must abort the current target immediately and surface a `CheckpointError` rather than being logged and ignored.

The last durable checkpoint file must remain intact after failure; the system must not continue downloading under the false assumption that current progress has been saved.

**Alternatives considered:**
- **Log and continue:** more permissive, but leaves the next rerun with stale or missing recovery data and violates the guarantee that progress semantics are trustworthy.

## Risks / Trade-offs

- **`seen_mids` remains unbounded for long-running targets** → Accept for this change because correctness is the immediate priority; revisit with a bounded or compact representation in a separate performance-focused change.
- **Fail-closed checkpoint writes can stop a target earlier than today** → Surface the error clearly so the user can fix the filesystem/lock problem instead of continuing with silently stale progress.
- **Invalidating legacy v1 checkpoints causes a one-time rescan** → Prefer explicit incompatibility over partial migration that could reintroduce the very resume bugs this change is meant to fix.
- **Gap-preserving coverage may revisit some already-landed posts on rerun** → Use per-item `SKIPPED` outcomes to keep the rerun observable and safe while still allowing the group to seal once fully successful.

## Migration Plan

1. Write the unified progress file using schema v3.
2. Treat any checkpoint with missing or mismatched `version` as incompatible and restart from a clean state.
3. Reuse existing v3 checkpoints only when their relevant option hashes are compatible with the current run.
4. On rollout, rely on regression tests to confirm monotonic rerun behavior for `--count`, partial-page resume, failed-group gap preservation, and save-failure propagation.
5. If the change must be rolled back, remove or ignore checkpoints produced under the stricter semantics before returning to the older behavior, rather than attempting mixed-semantics reuse.

## Open Questions

None. The previously ambiguous behaviors for legacy checkpoint handling, rerun observability, and save-failure policy are resolved by this design.