---
name: diary-forget
description: Forget personal diary facts when explicitly requested. Remove explicitly forgotten personal information from the active profile diary and reconcile matching native memory. Use for personal forgetting requests, not ordinary project deletion. Preserve unrelated facts and distinguish deletion from changed or retracted information. Conversation transcripts and backups are outside this operation.
---

# Forget personal records

Read the shared contract with `skill_view(name="diary-note", file_path="references/diary-format.md")` and load `diary-search`.

1. Identify the exact requested scope. Perform narrow, unambiguous deletion directly. Clarify ambiguous subjects; for broad requests, present matching record titles and count for confirmation unless the owner has already approved that concrete scope. Use `diary-note` for changes or retractions rather than an explicit request to forget.
2. Find all matching diary records, including duplicates and superseded records, and inspect relevant native memory. Read complete records before editing. Related words are search candidates, not evidence that unrelated facts should be deleted.
3. Remove only the requested facts and tags supported solely by them. Preserve unrelated facts, tags, and source references. Remove empty records and daily files. Create no backup, tombstone, or diary entry describing the deletion.
4. Use `maintain-memory` and the native `memory` tool to remove matching remembered facts while preserving other information in the same entries. If the request explicitly restricts deletion to the diary, respect that boundary and mention any known remaining native copy. Never bypass a native approval gate with direct file edits.
5. Reread changed files and search for the deleted facts and known aliases again. Check native tool results separately. If either operation is pending or failed, report the remaining scope accurately. Do not repeat deleted details in the reply or re-save them from history.
6. Report affected diary paths briefly. State when relevant that original conversations, backups, and other systems retain their own copies; this operation does not erase them.
