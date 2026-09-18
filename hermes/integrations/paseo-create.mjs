// Create an empty conversation through Paseo's public SDK bundled with its CLI.
import { realpathSync } from "node:fs";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const { listen, provider, model, title, profile, cwd } = JSON.parse(input);
const endpoint = new URL(`ws://${listen}/ws`);
if (!["localhost", "127.0.0.1", "[::1]"].includes(endpoint.hostname)) {
  throw new Error("Findings setup requires the same-host daemon's loopback TCP listener.");
}
const require = createRequire(realpathSync(process.argv[2]));
const { createPaseoClient } = await import(pathToFileURL(require.resolve("@getpaseo/client")).href);
const client = createPaseoClient({
  url: endpoint.href, password: process.env.PASEO_PASSWORD,
  connectTimeoutMs: 15000, reconnect: { enabled: false },
});
try {
  await client.connect();
  const agent = await client.agents.create({
    config: { provider: `${provider}/${model}` }, title, cwd,
    labels: { "hermes-pack.findings": profile },
  });
  console.log(JSON.stringify({ agentId: agent.id }));
} finally {
  await client.close();
}
