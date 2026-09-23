# Hermes Pack for Paseo

A personal assistant package with a native Hermes profile, character, memory and diary skills, a guided installer, and a Paseo provider. Names, preferences, credentials, and conversations stay private.

## Install

Run on the host where the assistant will stay available. Install [Hermes](https://hermes-agent.nousresearch.com/docs/getting-started/installation), [Paseo](https://paseo.sh), and [uv](https://docs.astral.sh/uv/getting-started/installation/) first. The package requires Hermes 0.21.2+, Paseo with plugin support, Python 3.10+, and Node.js. Hermes must be available at `~/.local/bin/hermes`, with profiles under `~/.hermes`. Paseo needs its CLI and a running local daemon.

```sh
git clone https://github.com/midodimori/paseo-hermes-pack-plugin.git
cd paseo-hermes-pack-plugin
./setup install
```

The Textual installer guides profile selection, assistant naming, model sign-in, Paseo messaging, tools, optional services, starting preferences, scheduled review, and background service startup. Yes and No explain each choice. Existing profile data is preserved; account sign-in uses native prompts.

Review the plugin source and enable **Settings → Plugins → Enable plugins** on the Paseo daemon host. Plugins are trusted, unsandboxed code. Messaging setup saves a private provider entry, installs the plugin from Git, creates or reuses a dedicated findings conversation, and offers to route scheduled reports there. Settle active assistant work before changing providers.

Keep the checkout at a stable location: the profile records `hermes/` as its native distribution source. The guided installer can continue setup for an existing profile. Private Paseo provider settings live in `~/.paseo/hermes-pack.json`; the runtime plugin ID is `hermes-pack`.

## Use

Select the assistant's provider in Paseo and open a conversation. The composer offers native model and thinking controls. Management commands appear when the session opens; skill shortcuts use the profile's installed skills.

```text
/help
/cron list --all
/review-context Review current priorities
```

Hermes owns memory, sessions, learned skills, and schedules. The profile includes [six assistant skills](hermes/CAPABILITIES.md) and a private dated diary. Starting facts are blank; setup can seed owner-entered preferences into empty native user memory. Work delegation uses Paseo's upstream skill and CLI, installed through the optional delegation step.

The optional hourly **Personal review** uses medium thinking and native continuity. It reviews authorized context and task health, recommends at most one useful action, and stays silent when nothing material changed. It does not execute recommendations. Findings arrive directly as assistant messages in the dedicated conversation and use Paseo completion notifications. Delivery starts no extra model turn. Reply there to discuss the latest finding. The target must be idle and unarchived; routine failures remain in native local records.

Optional web, browser, perception, Proton, and other service setup uses native tools and private account settings. Availability depends on installed backends, permissions, and credentials. [Capabilities](hermes/CAPABILITIES.md) lists the package contents.

## Update

Settle active work before applying updates.

```sh
git pull --ff-only
./setup update --profile assistant
paseo plugin update hermes-pack
```

Profile updates preserve local configuration, personal data, learned skills, independent extensions, and schedules. The configured name is reapplied to SOUL. Updating the Paseo plugin reloads its provider; no daemon restart is required. Git synchronization alone does not update profile files.

## Uninstall and restore

```sh
./setup uninstall --profile assistant
```

Uninstall backs up and removes the selected profile and its background service, including credentials, memory, conversations, skills, and schedules. Hermes itself and other profiles remain installed. Retire that profile's Paseo conversations and private provider entry separately; the shared plugin may serve other profiles. `paseo plugin remove hermes-pack` removes only the Paseo integration.

Only the latest successful backup per selected profile is retained outside the profile; failed backups preserve the previous copy. Native full backups cover the Hermes root, including other profiles. Backups contain credentials and belong outside Git. Restore through native `hermes import /absolute/path/backup.zip`, then review accounts, paths, and schedules before starting services.

## Unattended setup

Use a private copy of [examples/install.json](hermes/examples/install.json) for explicit choices. Unspecified settings remain unchanged; credentials still require native sign-in.

```sh
./setup install --config /absolute/path/install.json --check
./setup install --config /absolute/path/install.json --yes
```

`--check` validates choices without applying them. `--yes` runs authorized choices without prompts; it does not grant permission. An assistant updating its own profile must run the operation independently of its gateway process and verify completion. The private `local/pack-repo` setting identifies this checkout for the `maintain-pack` skill.
