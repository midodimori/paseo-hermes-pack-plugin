---
name: maintain-memory
description: Save, correct, or forget facts in native Hermes memory.
---

# Maintain memory

Use when writing persistent memory, including corrections and selective forgetting. Use the native `memory` tool and the existing profile; keep its storage, limits, and approval handling.

## Scope

Follow Hermes's memory scope: compact, durable facts and standing preferences that matter across sessions. Ordinary task progress belongs in session history or its existing task home, not a growing memory log. Honor explicit requests to retain or correct particular context without turning them into a general activity ledger. A context review alone is not a request to save its recommendations.

Detailed durable personal history belongs in the profile diary through `diary-note`. For a correction or forgetting request, use `diary-search` to check matching diary records unless this has already been done in the current operation. Reconcile affected records through the `diary-note` or `diary-forget` procedure within the user's scope. Do not repeat successful writes when returning from those procedures. Native memory and diary changes are separate; report a partial or pending result if either cannot finish.

## Write procedure

1. Identify what the user changed, what remains valid, and what they asked to forget. Use the latest known complete entries. The session-start memory snapshot can lag writes made in this conversation; include subsequent successful tool operations when determining current state. Do not guess the contents of a partially known entry.
2. Store independently changing facts in separate entries. Use `user` for standing user preferences and `memory` for durable notes. Keep related details together only when they should change or be removed together.
3. **`old_text` identifies an entire entry; it is not a text-edit range.** Both `replace` and `remove` affect that whole entry. For a partial correction, supply the complete replacement, preserving unrelated facts that still matter. For partial forgetting, replace the entry with its remaining facts; remove it only when nothing should remain. Splitting an existing mixed entry is also valid: retain one fact in the replacement and add the others in the same target's native batch.
4. Before submitting, compare the planned result with the complete affected entries. Preserve independent commitments, relevant cancellations, and standing limits unless newer evidence supersedes them or the user requests their removal. Do not infer that changing one subject resets another. Do not reintroduce forgotten content elsewhere.
5. Submit related changes through the native `operations` batch for each target. If a limit, ambiguous match, or stale-entry error occurs, use the returned current entries to revise the operation; do not guess, bypass the tool, or silently discard valid facts to make room. Stop and explain an unresolved failure instead of claiming success.
6. Check the tool result. A staged write is pending, not saved. Confirm successful writes briefly and do not repeat an acknowledged operation. If saved entries are returned, check that the requested change and unrelated facts are present. A success acknowledgment alone does not mean storage was independently reread.

## Verification

Check the requested change, retention of unrelated facts, and absence of obsolete or explicitly forgotten facts. Fresh-session recall should reflect the saved result without replaying the original conversation. This skill guides memory writes; installation alone does not guarantee it is loaded for every write.
