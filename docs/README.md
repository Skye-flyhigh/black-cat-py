# blackcat Docs

For the latest documentation, visit [blackcat.wiki](https://blackcat.wiki/docs/latest/getting-started/blackcat-overview).

If you have never used a terminal or edited a config file before, start with [`start-without-technical-background.md`](./start-without-technical-background.md). Otherwise, start with [`quick-start.md`](./quick-start.md) and get one local `nanobot agent -m "Hello!"` reply working before connecting chat apps, WebUI, Docker, or custom tools.

Most JSON examples in these docs are snippets to merge into `~/.nanobot/config.json`, not full replacement files.

Provider examples are concrete walkthroughs, not rankings or endorsements. Use the provider whose key, endpoint, and model ID you actually control.

If you find a docs mistake, outdated command, or confusing step, please open an issue: <https://github.com/HKUDS/nanobot/issues>.

## Pick a Track

| You are | Start with | Then use |
|---|---|---|
| Install and quick start | [`quick-start.md`](./quick-start.md) | Installation, onboarding, and first-run setup |
| Chat apps | [`chat-apps.md`](./chat-apps.md) | Connect blackcat to Telegram, Discord, WeChat, and more |
| Agent social network | [`agent-social-network.md`](./agent-social-network.md) | Join external agent communities from blackcat |
| Configuration | [`configuration.md`](./configuration.md) | Providers, tools, channels, MCP, and runtime settings |
| Image generation | [`image-generation.md`](./image-generation.md) | Configure image providers, WebUI image mode, and generated artifacts |
| WebUI | [`../webui/README.md`](../webui/README.md) | Open the bundled browser UI; LAN access; Vite dev server for contributors |
| Multiple instances | [`multiple-instances.md`](./multiple-instances.md) | Run isolated bots with separate configs and workspaces |
| CLI reference | [`cli-reference.md`](./cli-reference.md) | Core CLI commands and common entrypoints |
| In-chat commands | [`chat-commands.md`](./chat-commands.md) | Slash commands and periodic task behavior |
| OpenAI-compatible API | [`openai-api.md`](./openai-api.md) | Local API endpoints, request format, and file uploads |
| Deployment | [`deployment.md`](./deployment.md) | Docker, Linux service, and macOS LaunchAgent setup |

## Start Here

| Goal | Read | Outcome |
|---|---|---|
| Memory | [`memory.md`](./memory.md) | How blackcat stores, consolidates, and restores memory |
| Python SDK | [`python-sdk.md`](./python-sdk.md) | Use blackcat programmatically from Python |
| Development | [`development.md`](./development.md) | Contributor notes for adding providers and transcription adapters |
| Channel plugin guide | [`channel-plugin-guide.md`](./channel-plugin-guide.md) | Build and test custom chat channel plugins |
| WebSocket channel | [`websocket.md`](./websocket.md) | Real-time WebSocket access and protocol details |
| Custom tools | [`my-tool.md`](./my-tool.md) | Inspect and tune runtime state with the `my` tool |
