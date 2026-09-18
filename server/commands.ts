import { execFile } from "node:child_process";
import { randomUUID } from "node:crypto";
import { z } from "zod";
import type {
  ProviderCommand, ProviderConfigState, ProviderEvent, ProviderRegistration, ProviderSessionConfig,
} from "@getpaseo/plugin/server/provider";

const commandsSchema = z.object({ commands: z.array(z.object({
  name: z.string().regex(/^[a-z0-9][a-z0-9-]*$/), description: z.string(), argumentHint: z.string().optional(),
})) });

type Session = {
  config: ProviderSessionConfig;
  nativeId?: string;
  state?: ProviderConfigState;
  core: ProviderCommand[];
  skills: ProviderCommand[];
  turns: Set<string>;
  prompts: Set<string>;
  commandPending: boolean;
  skillInputs: Map<string, string>;
};

/** Add working commands using native Hermes helpers; management replies need no inference. */
export function withCommands(provider: ProviderRegistration, cli: string, profile: string): ProviderRegistration {
  return {
    ...provider,
    async connect(request) {
      const connection = await provider.connect(request);
      const sessions = new Map<string, Session>();
      const listeners = new Set<(event: ProviderEvent) => void>();
      const waiting = new Map<string, { resolve: () => void; reject: (error: Error) => void }>();
      const nativeCalls = new Set<Promise<void>>();
      let closed = false;
      const emit = (event: ProviderEvent) => { for (const listener of listeners) listener(event); };
      const extra = (session: Session): ProviderCommand[] => [
        { name: "cron", description: "Manage this profile's scheduled tasks", argumentHint: "[list|status|create|edit|pause|resume|run|remove|runs] ..." },
        ...(session.state?.thinkingOptions.length ? [{ name: "reasoning", description: "Show or change this conversation's reasoning level", argumentHint: "[level|auto]" }] : []),
        ...session.skills,
      ].filter((command) => !session.core.some((core) => core.name === command.name));
      const publish = (sessionId: string, session: Session) => emit({ type: "session.commands", sessionId,
        commands: [...session.core, ...extra(session)] });
      const unsubscribe = connection.onEvent((event) => {
        if ((event.type === "request.completed" || event.type === "request.failed") && waiting.has(event.requestId)) {
          const pending = waiting.get(event.requestId)!;
          waiting.delete(event.requestId);
          if (event.type === "request.failed") pending.reject(new Error(event.error.message));
          else pending.resolve();
          return;
        }
        const session = "sessionId" in event ? sessions.get(event.sessionId) : undefined;
        if (session && "sessionId" in event) {
          if (event.type === "timeline.item" && event.item.type === "user_message" && event.item.clientMessageId) {
            const text = session.skillInputs.get(event.item.clientMessageId);
            if (text !== undefined) {
              session.skillInputs.delete(event.item.clientMessageId);
              event = { ...event, item: { ...event.item, text } };
            }
          }
          if (event.type === "session.opened") {
            const data = event.persistence?.data;
            if (data && typeof data === "object" && !Array.isArray(data) && typeof data.sessionId === "string") session.nativeId = data.sessionId;
          }
          if (event.type === "session.config") { session.state = event.config; emit(event); publish(event.sessionId, session); return; }
          if (event.type === "session.commands") { session.core = [...event.commands]; publish(event.sessionId, session); return; }
          if (event.type === "session.prompt_result") session.prompts.delete(event.clientMessageId);
          if (event.type === "session.turn") {
            if (event.state === "started") session.turns.add(event.turnId);
            else session.turns.delete(event.turnId);
          }
          if (event.type === "session.closed" || event.type === "session.runtime_failed") sessions.delete(event.sessionId);
        }
        emit(event);
      });

      function native(session: Session, operation: string, args: string[] = []): Promise<unknown> {
        const call = new Promise<unknown>((resolve, reject) => {
          const child = execFile(cli, ["-p", profile, "paseo-command", operation, ...args], {
            cwd: session.config.cwd, env: { ...process.env, ...session.config.env, HERMES_PLATFORM: "acp" },
            timeout: 30_000, maxBuffer: 2 * 1024 * 1024,
          }, (error, stdout) => {
            if (error) { reject(new Error("Hermes command failed or timed out; inspect before retrying")); return; }
            try {
              const value: unknown = JSON.parse(stdout);
              if (value && typeof value === "object" && "error" in value && typeof value.error === "string") reject(new Error(value.error));
              else resolve(value);
            } catch { reject(new Error("Hermes returned an invalid command response")); }
          });
          child.stdin?.end();
        });
        const settled = call.then(() => undefined, () => undefined);
        nativeCalls.add(settled);
        void settled.then(() => nativeCalls.delete(settled));
        return call;
      }

      async function configure(sessionId: string, thinkingOption: string) {
        const requestId = `command:${randomUUID()}`;
        let timer: ReturnType<typeof setTimeout>;
        const acknowledged = new Promise<void>((resolve, reject) => {
          waiting.set(requestId, { resolve, reject });
          timer = setTimeout(() => reject(new Error("Reasoning change timed out; check the composer before retrying")), 30_000);
        });
        try {
          await connection.send({ type: "session.configure", requestId, sessionId, changes: { thinkingOption } });
          await acknowledged;
        } finally { clearTimeout(timer!); waiting.delete(requestId); }
      }

      return {
        version: connection.version, capabilities: connection.capabilities,
        onEvent(listener) { listeners.add(listener); return () => { listeners.delete(listener); }; },
        async send(input) {
          if (closed) throw new Error("Command connection is closed");
          if (input.type === "session.open") {
            // ponytail: discover shortcuts on open; reload refreshes newly learned skills without a watcher.
            const session: Session = { config: input.config, core: [], skills: [], turns: new Set(), prompts: new Set(), commandPending: false, skillInputs: new Map() };
            session.skills = commandsSchema.parse(await native(session, "skills")).commands;
            if (closed) throw new Error("Conversation closed during skill discovery");
            sessions.set(input.sessionId, session);
            try { await connection.send(input); }
            catch (error) { sessions.delete(input.sessionId); throw error; }
            return;
          }
          const session = "sessionId" in input ? sessions.get(input.sessionId) : undefined;
          if (input.type !== "session.prompt" || !session) { await connection.send(input); return; }
          let name: string | undefined, args = "";
          if (input.prompt.input.type === "command") {
            name = input.prompt.input.name.replace(/^\//, "").toLowerCase(); args = input.prompt.input.arguments;
          } else if (input.prompt.input.content.length === 1 && input.prompt.input.content[0].type === "text") {
            const match = /^\/([a-z0-9-]+)(?:\s+([\s\S]*))?$/i.exec(input.prompt.input.content[0].text);
            if (match) { name = match[1].toLowerCase(); args = match[2] || ""; }
          }
          const skill = session.skills.find((command) => command.name === name);
          if (!skill && name !== "reasoning" && name !== "cron" && !(name === "help" && !args.trim())) {
            session.prompts.add(input.prompt.clientMessageId);
            try { await connection.send(input); } catch (error) { session.prompts.delete(input.prompt.clientMessageId); throw error; }
            return;
          }
          const complete = (text: string) => {
            emit({ type: "timeline.item", sessionId: input.sessionId,
              item: { type: "user_message", id: input.prompt.clientMessageId, clientMessageId: input.prompt.clientMessageId,
                text: `/${name}${args ? " " + args : ""}` } });
            emit({ type: "timeline.item", sessionId: input.sessionId,
              item: { type: "assistant_message", id: `command:${input.prompt.clientMessageId}`, text: text.slice(0, 64 * 1024) } });
            emit({ type: "session.prompt_result", sessionId: input.sessionId, clientMessageId: input.prompt.clientMessageId, result: { type: "completed" } });
          };
          if (session.commandPending || session.turns.size || session.prompts.size) { complete("This conversation is busy. Try the command after the current turn finishes."); return; }
          session.commandPending = true;
          try {
            if (skill) {
              if (!session.nativeId) throw new Error("Hermes session is not ready for skill invocation");
              const result = z.object({ message: z.string().min(1) }).parse(await native(session, "skill", [
                `--name=${name}`, `--arguments=${args}`, `--session-id=${session.nativeId}`,
              ]));
              if (closed) throw new Error("Conversation closed during skill loading");
              session.prompts.add(input.prompt.clientMessageId);
              session.skillInputs.set(input.prompt.clientMessageId, `/${name}${args ? " " + args : ""}`);
              await connection.send({ ...input, prompt: { ...input.prompt, input: { type: "message", content: [{ type: "text", text: result.message }] } } });
            } else if (name === "reasoning") {
              const options = session.state?.thinkingOptions || [];
              if (!options.length) throw new Error("This provider does not expose reasoning controls");
              let value = args.trim().toLowerCase();
              if (value === "default" || value === "reset") value = "auto";
              if (value) {
                if (!options.some((option) => option.id === value)) throw new Error(`Choose: ${options.map((option) => option.id).join(", ")}`);
                await configure(input.sessionId, value);
              }
              complete(`Reasoning: ${session.state?.thinkingOption || "auto"}. Choices: ${options.map((option) => option.id).join(", ")}.`);
            } else if (name === "cron") {
              const result = z.object({ text: z.string(), failed: z.boolean() }).parse(await native(session, "cron", [`--arguments=${args}`]));
              complete(result.text || (result.failed ? "Hermes rejected the cron command." : "Cron command completed."));
            } else complete([...session.core, ...extra(session)].map((command) => `/${command.name} — ${command.description}`).join("\n") + "\n\nSkill shortcuts start a normal agent turn.");
          } catch (error) {
            session.prompts.delete(input.prompt.clientMessageId);
            session.skillInputs.delete(input.prompt.clientMessageId);
            complete(error instanceof Error ? error.message : "Command failed; inspect before retrying");
          } finally { session.commandPending = false; }
        },
        async close() {
          closed = true;
          for (const pending of waiting.values()) pending.reject(new Error("Conversation closed during the command"));
          waiting.clear();
          await Promise.all(nativeCalls);
          await connection.close(); unsubscribe(); sessions.clear(); listeners.clear();
        },
      };
    },
  };
}
