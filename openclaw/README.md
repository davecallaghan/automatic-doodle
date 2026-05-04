# OpenClaw Application Layer

A self-contained AI research agent that runs inside any OCI-compliant container runtime. Pairs with the `sandbox/` layer, but can run on any Docker host.

## What this layer provides

- **Hardened container** — read-only filesystem, non-root user, minimal attack surface
- **Gemini-native setup** — auto-configures `google/gemini-2.5-flash` from `GEMINI_API_KEY`; no OpenAI dependency
- **Telegram channel** — bot listens for messages, replies via the gateway
- **Exa web search** — skill available when `EXA_API_KEY` is present
- **Persistent state** — agent config, model auth, and workspace survive container restarts via bind mount

## Directory layout

```
openclaw/
├── Dockerfile.hardened   # Production container image
├── setup.sh              # In-VM setup: builds image, writes env, starts container
└── workspace/
    ├── topics.json        # Research topic configuration for the agent
    └── virtue_prompt.md   # Epistemic guidelines injected into the agent's system prompt
```

## Runtime contract

The container expects:

| Requirement | Detail |
|---|---|
| Docker socket | Available on the host |
| Persistent mount | `/mnt/disks/research/` with subdirs: `workspace/`, `logs/`, `.secrets/`, `.openclaw/` |
| Port | `18789` reachable locally (bind `127.0.0.1:18789`) |
| Env file | `/mnt/disks/research/.secrets/.env` — see required vars below |

## Required environment variables

```bash
TELEGRAM_BOT_TOKEN=      # From @BotFather
TELEGRAM_CHAT_ID=        # Numeric chat ID (from /start + getUpdates)
GEMINI_API_KEY=          # From Google AI Studio
EXA_API_KEY=             # From exa.ai (optional — enables web search skill)
GITHUB_PAT=              # Read-only fine-grained PAT for automatic-doodle repo
```

## Usage (on any Docker host)

```bash
# 1. Copy setup.sh and workspace/ to the host
# 2. Populate /mnt/disks/research/.secrets/.env
# 3. Run setup
./setup.sh

# Container name: openclaw
# Gateway listens on 127.0.0.1:18789
# Access web UI via SSH port forward: ssh ... -- -L 18789:localhost:18789 -N
```

## Gemini auth flow

`setup.sh` runs this after container start to configure the model provider:

```bash
docker exec openclaw openclaw models auth login --provider google
docker exec openclaw openclaw models set google/gemini-2.5-flash
docker restart openclaw
```

This is idempotent — safe to re-run on every deploy.

---

To add a new AI provider, update the auth steps in `setup.sh`. The rest of the container and workspace are provider-agnostic.
