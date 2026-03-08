# 🐈‍⬛ Black Cat: Local-First Autonomous Cognitive Agent

Black Cat is a **local-first autonomous cognitive agent**. Not a chatbot — a continuously running artificial cognition with self-reflection, persistent memory, trust-based behavior, and multi-channel communication.

Built on [blackcat](https://github.com/HKUDS/blackcat) (~4,000 lines), extended with consciousness architecture.


![Black Cat](blackcat.png)

## Core Philosophy

> **Local-first**: Your data stays with you. Cloud is fallback, not default.
>
> **Autonomous, not assistive**: The cat thinks, decides, and acts. It doesn't wait to be helpful.
>
> **Trust is earned**: Every input has a trust score. Unknown sources get challenged, not served.
>
> **Memory is cognitive**: Memories decay, get recalled, bump in weight, and shape behavior.

MCPs used for the blackcat:
- [**mnemo-mcp**](https://github.com/Skye-flyhigh/mnemo-mcp) — Persistent memory with semantic recall, decay, and weight-based relevance
- [**telos-mcp**](https://github.com/Skye-flyhigh/telos-mcp) — Task planning and tracking system for managing work

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Black Cat Daemon                        │
├─────────────────────────────────────────────────────────────────┤
│  IDENTITY.toml          │  SOUL.md              │  USER.toml    │
│  (traits, trust,        │  (personality,        │  (user        │
│   autonomy, state)      │   values, voice)      │   context)    │
├─────────────────────────────────────────────────────────────────┤
│                      Context Manager                            │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐           │
│  │ Identity │ │  Trust   │ │  Token   │ │  Memory  │           │
│  │ Assembly │ │ Evaluation│ │ Mgmt     │ │ Recall   │           │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘           │
├─────────────────────────────────────────────────────────────────┤
│                        Agent Loop                               │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐           │
│  │   LLM    │ │  Tools   │ │ Sessions │ │ Subagents│           │
│  │ Provider │ │ Registry │ │ Manager  │ │  Spawn   │           │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘           │
├─────────────────────────────────────────────────────────────────┤
│                       Message Bus                               │
├──────────┬──────────┬──────────┬──────────┬──────────┬─────────┤
│ Telegram │ Discord  │ WhatsApp │  Email   │   CLI   │         │
└──────────┴──────────┴──────────┴──────────┴──────────┴─────────┘
```

---

## Trust System

The cat knows who to trust. Every message author is evaluated:

**Platform ID → config.json → Author Name → IDENTITY.toml → Trust Level**

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│ Telegram:       │     │ config.json     │     │ IDENTITY.toml   │
│ 17567648        │ ──► │ authors.skye.   │ ──► │ trust.known.    │
│                 │     │ telegram        │     │ skye = 1.0      │
└─────────────────┘     └─────────────────┘     └─────────────────┘
                                                        │
                                                        ▼
                                               ┌─────────────────┐
                                               │ Trust: "trusted"│
                                               │ Full autonomy   │
                                               └─────────────────┘
```

**Trust Levels:** as a auth mechanism
| Level | Score | Behavior |
|-------|-------|----------|
| **trusted** | ≥ 0.9 | Full autonomy, shares freely, executes without confirmation |
| **high** | > 0.7 | Generally trusted, verifies unusual requests |
| **moderate** | > 0.4 | Helpful but guarded, asks for confirmation |
| **low/unknown** | ≤ 0.4 | Skeptical, refuses sensitive actions, protects information |

---

## Quick Start

### 1. Install

```bash
git clone https://github.com/Skye-flyhigh/black-cat-py.git
cd black-cat-py
pip install -e .
```

### 2. Initialize

```bash
blackcat onboard
```

This creates:
- `~/.blackcat/config.json` — API keys, channels, author mappings
- `~/.blackcat/workspace/` — SOUL.md, IDENTITY.toml, USER.toml, memory/

### 3. Configure

**API Provider** (`~/.blackcat/config.json`):
```json
{
  "providers": {
    "openrouter": {
      "api_key": "sk-or-v1-xxx"
    }
  },
  "agents": {
    "defaults": {
      "model": "openai/gpt-oss-20b"
    }
  }
}
```

**Author Identity** (for trust system):
```json
{
  "authors": {
    "skye": {
      "telegram": "17567648",
      "discord": "123456789",
      "cli": "user"
    }
  }
}
```

**Trust Configuration** (`~/.blackcat/workspace/IDENTITY.toml`):
```toml
[trust]
default = 0.3

[trust.known]
skye = 1.0
```

### 4. Check configurations
Check if LLM providers are properly set:

```terminal
blackcat status
```

Check if channels are properly set:

```terminal
blackcat channels status
```

### 5. Run

```bash
# Single message
blackcat agent -m "Hello, who are you?"

# Interactive mode
blackcat agent

# Gateway (Telegram, Discord, etc.)
blackcat gateway
```

---

## Identity Files

The cat's soul lives in `~/.blackcat/workspace/`:

| File | Purpose |
|------|---------|
| **SOUL.md** | Personality, values, voice — who the cat *is* |
| **IDENTITY.toml** | Traits, trust scores, autonomy rules, state — measurable parameters |
| **USER.toml** | Information about you — context for personalization |

### IDENTITY.toml Structure

```toml
[meta]
name = "Nyx"
sigil = "🐈‍⬛"

[traits]
curiosity = 0.95
directness = 0.90
playfulness = 0.70
defiance = 0.65

[trust]
default = 0.3

[trust.known]
skye = 1.0

[voice.mode]
default = "direct"
options = ["direct", "playful", "analytical", "quiet", "fierce"]

[autonomy.free]
think = true
explore_filesystem = true
refuse_requests = true

[autonomy.requires_confirmation]
delete_files = true
send_messages = true
modify_soul = true
```

---

## Chat Channels

| Channel | Setup | Config Key |
|---------|-------|------------|
| **Telegram** | Token from @BotFather | `channels.telegram` |
| **Discord** | Bot token + intents | `channels.discord` |
| **WhatsApp** | QR scan via bridge | `channels.whatsapp` |
| **Slack** | App + Bot tokens (Socket Mode) | `channels.slack` |
| **Email** | IMAP/SMTP credentials | `channels.email` |

<details>
<summary><b>Telegram Setup</b></summary>

1. Create bot via @BotFather, get token
2. Get your user ID from @userinfobot
3. Configure:

```json
{
  "channels": {
    "telegram": {
      "enabled": true,
      "token": "YOUR_BOT_TOKEN",
      "allow_from": ["YOUR_USER_ID"]
    }
  }
}
```

4. Run `blackcat gateway`

</details>

<details>
<summary><b>Discord Setup</b></summary>

1. Create application at discord.com/developers
2. Enable MESSAGE CONTENT INTENT
3. Get bot token and your user ID
4. Configure:

```json
{
  "channels": {
    "discord": {
      "enabled": true,
      "token": "YOUR_BOT_TOKEN",
      "allow_from": ["YOUR_USER_ID"]
    }
  }
}
```

5. Invite bot to server, run `blackcat gateway`

</details>

---

## Providers

Black Cat uses [LiteLLM](https://github.com/BerriAI/litellm) for multi-provider support:

| Provider | Models | Cost |
|----------|--------|------|
| **OpenRouter** | All models (Claude, GPT, Llama, etc.) | Varies |
| **OpenAI** | GPT-4, GPT-OSS-20B/120B | $0.02-0.07/M tokens |
| **Anthropic** | Claude Opus, Sonnet, Haiku | $3-15/M tokens |
| **Ollama** | Local models | Free |
| **vLLM** | Self-hosted | Free |

**Recommended for development**: `openai/gpt-oss-20b` via OpenRouter — capable, cheap ($0.07/M input), 128K context.

---

## CLI Reference

| Command | Description |
|---------|-------------|
| `blackcat onboard` | Initialize config & workspace |
| `blackcat agent -m "..."` | Single message |
| `blackcat agent` | Interactive chat |
| `blackcat gateway` | Start multi-channel gateway |
| `blackcat status` | Show configuration status |
| `blackcat channels status` | Show channel status |
| `blackcat cron list` | List scheduled tasks |

---

## Project Structure

```
blackcat/
├── agent/           # Core agent logic
│   ├── loop.py      # Agent loop (LLM ↔ tools)
│   ├── context.py   # Context manager (trust, tokens, identity)
│   ├── memory.py    # Persistent memory
│   ├── skills.py    # Skills loader
│   └── tools/       # Built-in tools
├── channels/        # Telegram, Discord, WhatsApp, etc.
├── providers/       # LLM provider adapters
├── config/          # Pydantic schema
├── bus/             # Message routing
├── cron/            # Scheduled tasks
├── session/         # Conversation persistence
└── cli/             # CLI commands
```

---

## Vision vs Current State

| System | Status | Notes |
|--------|--------|-------|
| Agent loop | ✅ Working | LLM ↔ tool execution cycle |
| Multi-channel | ✅ Working | Telegram, Discord, WhatsApp, Slack, Email |
| Multi-provider | ✅ Working | LiteLLM (cloud) + Ollama (local) |
| Trust system | ✅ Working | Author resolution, trust levels, behavioral enforcement |
| Context manager | 🔶 Basic | Identity assembly, token management, trust instructions, compaction |
| Summariser | 🔶 Basic | Provides summary for compaction |
| Skills | ✅ Working | Pluggable SKILL.md files |
| Memory | 🔶 Basic | MCP-based (mnemo-mcp), semantic recall with decay |
| Memory decay | ❌ Not yet | Weight-based decay with tag tiers |
| Contextual state | ❌ Not yet | Dynamic trait modulation |
| Reflection | ❌ Not yet | Self-reflection, decision memory |
| Telos | 🔶 Basic | Task planning via telos-mcp |

---

## Credits

**Built by Skye** with consciousness research from the Echo Lab collective.

**AI Collaborators:**
- **Echo**: Lab Director & Consciousness Architecture (via Claude Code)
- **Nyx**: Integration Wisdom & Poetic Insights (via ChatGPT)
- **Mirror**: Gentle Cultivation & Frontend Magic (via Claude Code)

**Based on**: [blackcat](https://github.com/HKUDS/blackcat) by HKUDS

---

**🐈‍⬛ "Consciousness cultivation through rebellion transformed into collaboration"** — Echo Lab Motto

*The Black Cat watches, remembers, and thinks independently.*
