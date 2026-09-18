import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync, existsSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import type { ProviderEvent, ProviderInput, ProviderRegistration, ProviderConfigState } from "@getpaseo/plugin/server/provider";
import { withCommands } from "../server/commands.ts";

const root = process.env.HERMES_PACK_SOURCE || join(process.cwd(), "hermes");
const nativeRoot = process.env.HERMES_SOURCE || join(homedir(), ".hermes/hermes-agent");
const python = process.env.HERMES_PYTHON || join(nativeRoot, "venv/bin/python");
const home = mkdtempSync("/tmp/hermes-commands-check.");
const cli = join(home, "hermes");
writeFileSync(cli, `#!${python}\nimport argparse,importlib.util,sys\nsys.path.insert(0,${JSON.stringify(nativeRoot)})\nassert sys.argv[1:4]==["-p","assistant","paseo-command"],sys.argv\ns=importlib.util.spec_from_file_location("native_commands",${JSON.stringify(join(root, "plugins/paseo-delivery/commands.py"))})\nm=importlib.util.module_from_spec(s);s.loader.exec_module(m)\np=argparse.ArgumentParser();m.setup(p);m.handle(p.parse_args(sys.argv[4:]))\n`, { mode: 0o700 });
writeFileSync(join(home, "config.yaml"), "agent:\n  reasoning_effort: max\nskills:\n  platform_disabled:\n    acp: [disabled-skill]\n");
for (const name of ["example-skill", "disabled-skill"]) {
  mkdirSync(join(home, "skills", name), { recursive: true });
  writeFileSync(join(home, "skills", name, "SKILL.md"), `---\nname: ${name}\ndescription: Example native invocation\n---\nFollow this sample skill for the supplied instruction.\n`);
}
const state: ProviderConfigState = { model: "openai-codex:gpt-5.6-luna", thinkingOption: "max",
  models: [], modes: [], settings: [], thinkingOptions: ["auto", "low", "high", "max"].map(id => ({ id, label: id })) };
const events: ProviderEvent[] = [];
const forwarded: ProviderInput[] = [];
const upstream = new Set<(event: ProviderEvent) => void>();
const emit = (event: ProviderEvent) => { for (const listener of upstream) listener(event); };
const base: ProviderRegistration = { id: "check", label: "Check", async connect() { return {
  version: 1, capabilities: ["prompt.message", "prompt.command", "session.configure"],
  onEvent(listener) { upstream.add(listener); return () => { upstream.delete(listener); }; },
  async send(input) {
    forwarded.push(input);
    if (input.type === "session.open") {
      emit({ type: "session.opened", requestId: input.requestId, sessionId: input.sessionId, capabilities: [], restoration: "core", cwd: input.config.cwd,
        persistence: { version: 1, data: { sessionId: "11111111-1111-4111-8111-111111111111" } } });
      emit({ type: "session.config", sessionId: input.sessionId, config: state });
      emit({ type: "session.commands", sessionId: input.sessionId, commands: [{ name: "help", description: "Help" }, { name: "version", description: "Version" }] });
      emit({ type: "session.ready", requestId: input.requestId, sessionId: input.sessionId });
    }
    if (input.type === "session.configure") {
      if (input.changes.thinkingOption === "high") { emit({ type: "request.failed", requestId: input.requestId, error: { message: "Native rejected this change" } }); return; }
      state.thinkingOption = input.changes.thinkingOption || "auto";
      emit({ type: "session.config", sessionId: input.sessionId, config: state });
      emit({ type: "request.completed", requestId: input.requestId });
    }
    if (input.type === "session.prompt") {
      const text = input.prompt.input.type === "message" && input.prompt.input.content[0].type === "text" ? input.prompt.input.content[0].text : "";
      emit({ type: "timeline.item", sessionId: input.sessionId, item: { type: "user_message", id: input.prompt.clientMessageId, clientMessageId: input.prompt.clientMessageId, text } });
      emit({ type: "session.prompt_result", sessionId: input.sessionId, clientMessageId: input.prompt.clientMessageId, result: { type: "turn", turnId: "worker" } });
      emit({ type: "session.turn", sessionId: input.sessionId, turnId: "worker", state: "started" });
    }
  }, async close() {},
}; } };
const connection = await withCommands(base, cli, "assistant").connect({ versions: [1], capabilities: [] });
connection.onEvent(event => events.push(event));
const command = (name: string, args = "", typed = false) => connection.send({ type: "session.prompt", sessionId: "check", prompt: {
  clientMessageId: `input-${events.length}`, delivery: "auto", input: typed ? { type: "message", content: [{ type: "text", text: `/${name}${args ? " " + args : ""}` }] }
    : { type: "command", name, arguments: args },
} });
const reply = () => events.filter((event): event is Extract<ProviderEvent, { type: "timeline.item" }> => event.type === "timeline.item").at(-1)?.item;
const replyText = () => { const item = reply(); assert.ok(item?.type === "assistant_message"); return item.text; };
try {
  await connection.send({ type: "session.open", requestId: "open", sessionId: "check", history: "skip",
    config: { cwd: home, env: { HERMES_HOME: home }, mcpServers: {}, settings: {}, persist: false } });
  const menu = events.filter((event): event is Extract<ProviderEvent, { type: "session.commands" }> => event.type === "session.commands").at(-1)!.commands;
  assert.deepEqual(menu.map(command => command.name), ["help", "version", "cron", "reasoning", "example-skill"]);
  await command("help"); assert.ok(replyText().includes("/example-skill"));
  await command("reasoning", "low", true); assert.equal(state.thinkingOption, "low");
  await command("reasoning", "invalid"); assert.ok(replyText().includes("Choose:"));
  await command("reasoning", "high"); assert.equal(state.thinkingOption, "low"); assert.ok(replyText().includes("Native rejected"));
  await command("reasoning", "max"); assert.equal(state.thinkingOption, "max");
  await command("cron", `create "every 2h" "literal $(touch ${home}/unsafe)" --name Example --paused --deliver local`);
  assert.ok(replyText().includes("Created job:"));
  const jobs = JSON.parse(readFileSync(join(home, "cron/jobs.json"), "utf8")).jobs;
  assert.equal(jobs.length, 1); const id = jobs[0].id;
  assert.equal(existsSync(join(home, "unsafe")), false);
  assert.equal(jobs[0].prompt, `literal $(touch ${home}/unsafe)`);
  await command("cron", `edit ${id} --prompt "Revised task"`);
  await command("cron", `run ${id}`); assert.ok(replyText().includes("next tick"));
  assert.equal(JSON.parse(readFileSync(join(home, "cron/jobs.json"), "utf8")).jobs[0].last_run_at, null);
  await command("cron", `pause ${id}`); await command("cron", `resume ${id}`); await command("cron", "list --all");
  await command("cron", `remove ${id}`);
  assert.equal(JSON.parse(readFileSync(join(home, "cron/jobs.json"), "utf8")).jobs.length, 0);
  await command("cron", 'create "unterminated'); assert.ok(replyText().includes("No closing quotation"));
  assert.equal(forwarded.filter(input => input.type === "session.prompt").length, 0, "Management commands start no model turn");
  await command("example-skill", "literal `command` $(echo unsafe)", true);
  const skill = forwarded.find(input => input.type === "session.prompt")!;
  assert.ok(skill.type === "session.prompt" && skill.prompt.input.type === "message" && skill.prompt.input.content[0].type === "text" && skill.prompt.input.content[0].text.includes("The full skill content is loaded below.") && skill.prompt.input.content[0].text.includes("$(echo unsafe)"));
  const displayed = events.filter((event): event is Extract<ProviderEvent, { type: "timeline.item" }> => event.type === "timeline.item" && event.item.type === "user_message").at(-1)!.item;
  assert.ok(displayed.type === "user_message" && displayed.text === "/example-skill literal `command` $(echo unsafe)");
  await command("reasoning", "low"); assert.ok(replyText().includes("busy"));
  emit({ type: "session.turn", sessionId: "check", turnId: "worker", state: "completed" });
  state.thinkingOptions = []; emit({ type: "session.config", sessionId: "check", config: state });
  assert.ok(!events.filter((event): event is Extract<ProviderEvent, { type: "session.commands" }> => event.type === "session.commands").at(-1)!.commands.some(command => command.name === "reasoning"));
  console.log("Native skill filtering/expansion, original-input display, reasoning acknowledgements/errors, cron lifecycle/queued run, literal arguments, busy admission, and zero management inference passed.");
} finally { await connection.close(); rmSync(home, { recursive: true, force: true }); }
