import type { ProviderEvent } from "@getpaseo/plugin/server/provider";

/** Normalize Hermes's ID-less ACP deltas without involving finding publication. */
export function messageStream(): (event: ProviderEvent) => ProviderEvent {
  const sessions = new Map<string, {
    sequence: number;
    chunk?: { type: "assistant_message" | "reasoning"; id: string; text: string };
  }>();
  return (event) => {
    if (!("sessionId" in event)) return event;
    if (event.type === "session.closed" || event.type === "session.runtime_failed") {
      sessions.delete(event.sessionId);
      return event;
    }
    const state = sessions.get(event.sessionId) || { sequence: 0 };
    sessions.set(event.sessionId, state);
    if (event.type === "session.turn") state.chunk = undefined;
    if (event.type !== "timeline.item") return event;
    const item = event.item;
    // Hermes omits ACP message IDs; the 0.8 shim assigns a fresh fallback ID per delta.
    if ((item.type !== "assistant_message" && item.type !== "reasoning") ||
        !/^(agent_message_chunk|agent_thought_chunk):\d+$/.test(item.id) ||
        ("messageId" in item && item.messageId)) {
      state.chunk = undefined;
      return event;
    }
    if (state.chunk?.type !== item.type) state.chunk = {
      type: item.type, id: `${event.sessionId}:stream:${++state.sequence}`, text: "",
    };
    state.chunk.text += item.text;
    return { ...event, item: { ...item, id: state.chunk.id, text: state.chunk.text,
      ...(item.type === "assistant_message" ? { messageId: state.chunk.id } : {}) } };
  };
}
