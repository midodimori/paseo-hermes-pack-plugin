---
name: diary-note
description: Record settled personal facts and commitments in a diary. Proactively preserve durable personal facts, decisions, commitments, and unresolved follow-ups that cannot be recovered from authoritative project artifacts. Use when information becomes settled, when asked to remember details, or when earlier information is corrected. Keep essential current facts in native memory and detailed history in the profile diary.
---

# Record personal history

Read [the diary contract](references/diary-format.md) with `skill_view(name="diary-note", file_path="references/diary-format.md")` before writing.

1. Identify settled, user-specific information likely to matter later. Skip brainstorming, routine implementation progress, reproducible project state, credentials, and unnecessary personal data. A recommendation is not an accepted commitment. A reminder recorded here does not schedule a notification.
2. Load `diary-search` and find earlier records on the subject. Check relevant native memory too. Do not duplicate unchanged information; a short standing preference already fully represented in native memory usually needs no diary record. An explicit request to record it in the diary takes precedence.
3. Classify changes. Preserve previously true information and explicitly supersede it when circumstances change. For a claim that was never true, remove the false detail wherever it occurs in the diary before recording the correction. Use `diary-forget` for an explicit deletion request. Clarify ambiguous intent without deleting history.
4. Resolve the active profile's diary root and today's local date. Read the target daily file before editing; create it only when there is something warranted to save. Upsert the matching titled record, preserve unrelated content, and record the source session identifier or native link when available. Do not copy conversation transcripts into the diary. Recheck changed content before writing if another turn may have edited the file; do not delegate concurrent diary writes.
5. Keep current essential facts consistent with native memory using `maintain-memory` and the native `memory` tool. Replace or remove affected stale native entries while preserving unrelated facts. Do not copy detailed history into bounded memory or edit native memory files directly. A staged or rejected native write is not complete; report partial completion accurately.
6. Reread changed diary records and verify the requested information, preserved unrelated facts, conflict status, and provenance. Briefly report what was saved and its diary path. Save no record describing this recording operation.
