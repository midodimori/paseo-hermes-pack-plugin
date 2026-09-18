import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { createConnection } from "node:net";
import { join, resolve } from "node:path";
import { promisify } from "node:util";
import type { ProviderEvent, ProviderInput, ProviderRegistration, ProviderSessionConfig } from "@getpaseo/plugin/server/provider";
import { withFindings } from "../server/findings.ts";

const home = mkdtempSync("/tmp/hermes-findings-");
const agentId = "11111111-1111-4111-8111-111111111111";
const sessionId = "session";
const socketPath = join(home, "paseo", `${agentId}.sock`);
const config: ProviderSessionConfig = {
  cwd: home, env: { PASEO_AGENT_ID: agentId },
  mcpServers: {}, settings: {}, persist: true,
};
const received: ProviderInput[] = [];
let nativeEmit: (event: ProviderEvent) => void = () => {};
const base: ProviderRegistration = {
  id: "check", label: "Check",
  async connect() {
    return {
      version: 1, capabilities: [],
      onEvent(listener) { nativeEmit = listener; return () => {}; },
      async send(input) {
        received.push(input);
        if (input.type === "session.open") nativeEmit({ type: "session.ready", sessionId: input.sessionId });
      },
      async close() {},
    };
  },
};
function publish(value: unknown): Promise<Record<string, unknown>> {
  return new Promise((accept, reject) => {
    const socket = createConnection(socketPath);
    socket.setTimeout(5000, () => { socket.destroy(); reject(new Error("Publication timeout")); });
    socket.on("error", reject);
    socket.on("connect", () => socket.write(JSON.stringify(value) + "\n"));
    let response = "";
    socket.on("data", (chunk) => { response += chunk; });
    socket.on("end", () => accept(JSON.parse(response)));
  });
}
const events: ProviderEvent[] = [];
let connection = await withFindings(base, home).connect({ versions: [1], capabilities: [] });
let remove = connection.onEvent((event) => events.push(event));
async function open() {
  await connection.send({ type: "session.open", requestId: "open", sessionId, config, history: "skip" });
  await new Promise<void>((accept) => setImmediate(accept));
}
try {
  await open();
  assert.equal(received[0].type === "session.open" && received[0].config.env.HERMES_HOME, home);
  await assert.rejects(connection.send({ type: "session.open", requestId: "wrong", sessionId: "wrong", history: "skip",
    config: { ...config, env: { ...config.env, HERMES_HOME: "/wrong-profile" } } }), /does not match/);
  assert.equal(statSync(socketPath).mode & 0o777, 0o600);
  assert.equal(statSync(join(home, "paseo")).mode & 0o777, 0o700);
  assert.ok((await publish({ agent_id: "wrong", message: "no" })).error);
  assert.ok((await publish({ agent_id: agentId, message: "x".repeat(65536) })).error);
  const finding = { agent_id: agentId, message: "Scheduled finding: preserve this exact text." };
  assert.equal((await publish(finding)).status, "published");
  const publication = events.slice(-3);
  assert.deepEqual(publication.map((event) => event.type), ["session.turn", "timeline.item", "session.turn"]);
  assert.equal(publication[1].type === "timeline.item" && publication[1].item.type, "assistant_message");
  assert.equal(received.length, 1, "Direct publication must never call ACP send or initiate inference");
  const count = events.length;
  await publish(finding);
  assert.equal(events.length, count, "An acknowledged duplicate must not publish again");

  await connection.send({ type: "session.prompt", sessionId, prompt: {
    clientMessageId: "human", delivery: "auto", input: { type: "message", content: [{ type: "text", text: "reply" }] },
  } });
  assert.ok((await publish({ ...finding, message: "busy" })).error, "Admission must cover prompts before turn start");
  nativeEmit({ type: "session.prompt_result", sessionId, clientMessageId: "human", result: { type: "turn", turnId: "human-turn" } });
  nativeEmit({ type: "session.turn", sessionId, turnId: "human-turn", state: "started" });
  assert.ok((await publish({ ...finding, message: "busy" })).error);
  const start = events.length;
  for (const [index, text] of ["One", " normal", " reply."].entries()) nativeEmit({
    type: "timeline.item", sessionId, item: { type: "assistant_message", id: `agent_message_chunk:${index + 1}`, text },
  });
  const chunks = events.slice(start);
  assert.ok(chunks.every((event) => event.type === "timeline.item" && event.item.id ===
    (chunks[0].type === "timeline.item" ? chunks[0].item.id : "")), "Stream deltas must update one message");
  assert.ok(chunks[2].type === "timeline.item" && chunks[2].item.type === "assistant_message" && chunks[2].item.text === "One normal reply.");
  nativeEmit({ type: "timeline.item", sessionId, item: {
    type: "tool_call", id: "tool", callId: "tool", name: "check", status: "completed", error: null,
    detail: { type: "plain_text", label: "Check" },
  } });
  nativeEmit({ type: "timeline.item", sessionId, item: { type: "assistant_message", id: "agent_message_chunk:4", text: "After tool." } });
  const afterTool = events.at(-1);
  assert.ok(afterTool?.type === "timeline.item" && chunks[0].type === "timeline.item" && afterTool.item.id !== chunks[0].item.id);
  const explicit: ProviderEvent = { type: "timeline.item", sessionId, item: { type: "assistant_message", id: "explicit", messageId: "explicit", text: "Already has identity." } };
  nativeEmit(explicit);
  assert.equal(events.at(-1), explicit, "Keep explicit message identities unchanged");
  nativeEmit({ type: "session.turn", sessionId, turnId: "human-turn", state: "completed" });
  nativeEmit({ type: "session.turn", sessionId, turnId: "second-turn", state: "started" });
  nativeEmit({ type: "timeline.item", sessionId, item: { type: "assistant_message", id: "agent_message_chunk:5", text: "Next reply." } });
  const nextReply = events.at(-1);
  assert.ok(nextReply?.type === "timeline.item" && chunks[0].type === "timeline.item" && nextReply.item.id !== chunks[0].item.id && nextReply.item.type === "assistant_message" && nextReply.item.text === "Next reply.");
  nativeEmit({ type: "session.turn", sessionId, turnId: "second-turn", state: "completed" });
  assert.equal(received.filter((input) => input.type === "session.prompt").length, 1);

  // Exercise the actual Python sender and Hermes reply hook against this provider's socket.
  const repo = process.env.HERMES_PACK_SOURCE || resolve(process.cwd(), "hermes");
  const cli = join(home, "inspect");
  writeFileSync(cli, '#!/usr/bin/env python3\nimport json,sys\nassert sys.argv[1] == "inspect"\nprint(json.dumps(' +
    JSON.stringify({ Id: agentId, Provider: "check", Status: "idle", Archived: false }).replace("false", "False") + '))\n', { mode: 0o700 });
  const code = `import importlib.util,os\nfrom types import SimpleNamespace\ns=importlib.util.spec_from_file_location("delivery",${JSON.stringify(join(repo, "plugins/paseo-delivery/__init__.py"))})\nm=importlib.util.module_from_spec(s);s.loader.exec_module(m)\nc=SimpleNamespace(extra={"agent_id":os.environ["PASEO_AGENT_ID"],"provider":"check","cli":${JSON.stringify(cli)}})\nr=m.publish(c,os.environ["PASEO_AGENT_ID"],"Real sender finding");assert r.get("delivery_status")=="published",r\nassert "Real sender finding" in m.reply_context(platform="acp")["context"]\n`;
  await promisify(execFile)("python3", ["-c", code], { env: { ...process.env, HERMES_HOME: home, PASEO_AGENT_ID: agentId } });
  assert.equal(statSync(join(home, "paseo", `${agentId}.json`)).mode & 0o777, 0o600);
  const saved = JSON.parse(readFileSync(join(home, "paseo", `${agentId}.json`), "utf8"));
  assert.equal(saved.message, "Real sender finding");
  assert.equal(saved.published, true);

  // Reopen the same persisted destination and check duplicate suppression survives process state loss.
  remove();
  await connection.close();
  await new Promise<void>((accept) => setImmediate(accept));
  connection = await withFindings(base, home).connect({ versions: [1], capabilities: [] });
  remove = connection.onEvent((event) => events.push(event));
  await open();
  const restored = events.at(-2);
  assert.ok(restored?.type === "timeline.item" && restored.item.type === "assistant_message" && restored.item.text === "Real sender finding");
  const afterOpen = events.length;
  await publish({ agent_id: agentId, message: "Real sender finding" });
  assert.equal(events.length, afterOpen);
  console.log("Direct assistant publication, zero inference, human-turn admission, Python reply context, and reopen checks passed.");
} finally {
  remove();
  await connection.close();
  rmSync(home, { recursive: true, force: true });
}
