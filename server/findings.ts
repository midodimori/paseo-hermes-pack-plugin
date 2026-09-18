import { createHash } from "node:crypto";
import { chmodSync, lstatSync, mkdirSync, readFileSync, renameSync, unlinkSync, writeFileSync } from "node:fs";
import { createServer, type Server, type Socket } from "node:net";
import { isAbsolute, join } from "node:path";
import type { ProviderEvent, ProviderRegistration } from "@getpaseo/plugin/server/provider";
import { messageStream } from "./streaming.ts";

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const limit = 64 * 1024;

type Lane = {
  sessionId: string;
  agentId: string;
  directory: string;
  ready: boolean;
  prompts: Set<string>;
  turns: Set<string>;
  server?: Server;
  sockets: Set<Socket>;
};

function privateDirectory(path: string) {
  mkdirSync(path, { recursive: true, mode: 0o700 });
  const stat = lstatSync(path);
  if (!stat.isDirectory() || stat.uid !== process.getuid?.()) throw new Error("Unsafe findings directory");
  chmodSync(path, 0o700);
}

function stop(lane: Lane) {
  lane.ready = false;
  for (const socket of lane.sockets) socket.destroy();
  lane.server?.close();
  // The listening server owns unlinking its socket. Never remove another process's socket.
}

function replay(lane: Lane, emit: (event: ProviderEvent) => void) {
  try {
    const path = join(lane.directory, `${lane.agentId}.json`);
    const stat = lstatSync(path);
    if (!stat.isFile() || stat.size > limit) return;
    const record = JSON.parse(readFileSync(path, "utf8"));
    if (record.agent_id !== lane.agentId || record.published !== true || typeof record.message !== "string") return;
    const id = createHash("sha256").update(record.message).digest("hex");
    if (record.id !== id) return;
    // Replay has no turn lifecycle: restoring the finding must not notify or start inference.
    emit({ type: "timeline.item", sessionId: lane.sessionId, timestamp: stat.mtime.toISOString(),
      item: { type: "assistant_message", id: `finding:${id}`, text: record.message } });
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") console.error("Latest Hermes finding could not be restored");
  }
}

function listen(lane: Lane, emit: (event: ProviderEvent) => void) {
  const socketPath = join(lane.directory, `${lane.agentId}.sock`);
  // Refuse an occupied or stale socket rather than stealing a live conversation's destination.
  const server = createServer((socket) => {
    lane.sockets.add(socket);
    socket.on("close", () => lane.sockets.delete(socket));
    socket.on("error", () => {});
    socket.setTimeout(20_000, () => socket.destroy());
    let data = Buffer.alloc(0);
    socket.on("data", (chunk) => {
      data = Buffer.concat([data, chunk]);
      if (data.length > limit) { socket.removeAllListeners("data"); socket.end('{"error":"Finding exceeds 64 KiB"}\n'); return; }
      if (!data.includes(10)) return;
      socket.removeAllListeners("data");
      const reply = (value: object) => socket.end(JSON.stringify(value) + "\n");
      try {
        const request = JSON.parse(data.toString("utf8"));
        if (request.agent_id !== lane.agentId || typeof request.message !== "string" || !request.message.trim()) {
          reply({ error: "Invalid finding or destination" }); return;
        }
        if (!lane.ready || lane.prompts.size || lane.turns.size) { reply({ error: "Conversation is busy or unavailable" }); return; }
        const id = createHash("sha256").update(request.message).digest("hex");
        const path = join(lane.directory, `${lane.agentId}.json`);
        let previous: { id?: string; published?: boolean } = {};
        try { previous = JSON.parse(readFileSync(path, "utf8")); }
        catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
        if (previous.id === id && previous.published) { reply({ status: "published", id }); return; }
        // Synchronous file writes and event emission keep admission atomic with incoming human prompts.
        const record = { agent_id: lane.agentId, id, message: request.message, published: false };
        const save = () => {
          const temporary = `${path}.tmp`;
          const encoded = JSON.stringify(record);
          if (Buffer.byteLength(encoded) > limit) throw new Error("Finding context exceeds 64 KiB");
          writeFileSync(temporary, encoded, { mode: 0o600, flag: "wx" });
          try { renameSync(temporary, path); }
          finally { try { unlinkSync(temporary); } catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; } }
        };
        save();
        const turnId = `finding:${id}`;
        emit({ type: "session.turn", sessionId: lane.sessionId, turnId, state: "started" });
        emit({ type: "timeline.item", sessionId: lane.sessionId,
          item: { type: "assistant_message", id: turnId, text: request.message } });
        emit({ type: "session.turn", sessionId: lane.sessionId, turnId, state: "completed" });
        // ponytail: this receipt confirms event emission, not daemon persistence or phone delivery.
        record.published = true;
        save();
        reply({ status: "published", id });
      } catch {
        reply({ error: "Publication uncertain; inspect the conversation before retrying" });
      }
    });
  });
  lane.server = server;
  server.on("error", () => { lane.ready = false; console.error("Hermes findings socket unavailable; chat remains active"); });
  server.listen(socketPath, () => {
    try { chmodSync(socketPath, 0o600); }
    catch { stop(lane); console.error("Hermes findings socket permissions unavailable; chat remains active"); }
  });
}

/** Keep normal ACP behavior; scheduled publication never calls connection.send(). */
export function withFindings(provider: ProviderRegistration, defaultHome?: string): ProviderRegistration {
  return {
    ...provider,
    async connect(request) {
      const connection = await provider.connect(request);
      const listeners = new Set<(event: ProviderEvent) => void>();
      const lanes = new Map<string, Lane>();
      const emit = (event: ProviderEvent) => { for (const listener of listeners) listener(event); };
      const normalize = messageStream();
      const unsubscribe = connection.onEvent((event) => {
        const lane = "sessionId" in event ? lanes.get(event.sessionId) : undefined;
        if (lane && event.type === "session.ready" && !lane.server) { lane.ready = true; listen(lane, emit); replay(lane, emit); }
        if (lane && event.type === "session.turn") {
          if (event.state === "started") lane.turns.add(event.turnId);
          else lane.turns.delete(event.turnId);
        }
        if (lane && event.type === "session.prompt_result") lane.prompts.delete(event.clientMessageId);
        if (lane && ["session.closed", "session.runtime_failed"].includes(event.type)) { stop(lane); lanes.delete(lane.sessionId); }
        emit(normalize(event));
      });
      return {
        version: connection.version,
        capabilities: connection.capabilities,
        onEvent(listener) { listeners.add(listener); return () => { listeners.delete(listener); }; },
        async send(input) {
          if (input.type === "session.open") {
            const home = input.config.env.HERMES_HOME || defaultHome;
            const agentId = input.config.env.PASEO_AGENT_ID;
            if (!home || !isAbsolute(home) || !agentId || !uuid.test(agentId)) {
              throw new Error("Direct findings require absolute HERMES_HOME and a Paseo conversation ID");
            }
            if (defaultHome && home !== defaultHome) throw new Error("Hermes home does not match this provider's profile");
            input = { ...input, config: { ...input.config, env: { ...input.config.env, HERMES_HOME: home } } };
            const directory = join(home, "paseo");
            privateDirectory(directory);
            if (lanes.has(input.sessionId)) throw new Error("Session already open");
            lanes.set(input.sessionId, { sessionId: input.sessionId, agentId, directory, ready: false, prompts: new Set(), turns: new Set(), sockets: new Set() });
          }
          const lane = "sessionId" in input ? lanes.get(input.sessionId) : undefined;
          if (lane && input.type === "session.prompt") lane.prompts.add(input.prompt.clientMessageId);
          try { await connection.send(input); }
          catch (error) {
            if (lane && input.type === "session.open") { stop(lane); lanes.delete(input.sessionId); }
            if (lane && input.type === "session.prompt") lane.prompts.delete(input.prompt.clientMessageId);
            throw error;
          }
          if (lane && input.type === "session.close") { stop(lane); lanes.delete(input.sessionId); }
        },
        async close() {
          for (const lane of lanes.values()) stop(lane);
          lanes.clear();
          unsubscribe();
          listeners.clear();
          await connection.close();
        },
      };
    },
  };
}
