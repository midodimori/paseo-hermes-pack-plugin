import { homedir } from "node:os";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { z } from "zod";
import type { PluginServerContext } from "@getpaseo/plugin/server";
import { runAcpProvider } from "@getpaseo/plugin/server/acp";
import { withFindings } from "./server/findings.ts";
import { modelBridge } from "./server/models.ts";
import { withCommands } from "./server/commands.ts";

export default function contribute(server: PluginServerContext) {
  const path = join(process.env.PASEO_HOME || join(homedir(), ".paseo"), "hermes-pack.json");
  const providers = z.array(z.object({
    id: z.string().regex(/^[a-z][a-z0-9._-]*$/),
    profile: z.string().regex(/^[a-zA-Z0-9][a-zA-Z0-9._-]*$/),
    label: z.string().min(1).optional(),
  }).strict()).parse(existsSync(path) ? JSON.parse(readFileSync(path, "utf8")) : []);
  if (new Set(providers.map((provider) => provider.id)).size !== providers.length) throw new Error("Duplicate Hermes provider ID");
  for (const provider of providers) {
    const home = provider.profile === "default" ? join(homedir(), ".hermes") : join(homedir(), ".hermes/profiles", provider.profile);
    const cli = join(homedir(), ".local/bin/hermes");
    server.registerProvider(withFindings(withCommands(runAcpProvider({
      id: provider.id,
      label: provider.label || provider.profile,
      command: ["python3", "-u", "-c", modelBridge, home,
        cli, "-p", provider.profile, "acp"],
      acpOptions: { waitForInitialCommands: true },
    }), cli, provider.profile), home));
  }
  return () => {};
}
