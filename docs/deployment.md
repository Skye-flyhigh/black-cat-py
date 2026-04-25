# Deployment

## Docker

> [!TIP]
> The `-v ~/.blackcat:/home/blackcat/.blackcat` flag mounts your local config directory into the container, so your config and workspace persist across container restarts.
> The container runs as user `blackcat` (UID 1000). If you get **Permission denied**, fix ownership on the host first: `sudo chown -R 1000:1000 ~/.blackcat`, or pass `--user $(id -u):$(id -g)` to match your host UID. Podman users can use `--userns=keep-id` instead.

### Docker Compose

```bash
docker compose run --rm blackcat-cli onboard   # first-time setup
vim ~/.blackcat/config.json                     # add API keys
docker compose up -d blackcat-gateway           # start gateway
```

```bash
docker compose run --rm blackcat-cli agent -m "Hello!"   # run CLI
docker compose logs -f blackcat-gateway                   # view logs
docker compose down                                      # stop
```

### Docker

```bash
# Build the image
docker build -t blackcat .

# Initialize config (first time only)
docker run -v ~/.blackcat:/home/blackcat/.blackcat --rm blackcat onboard

# Edit config on host to add API keys
vim ~/.blackcat/config.json

# Run gateway (connects to enabled channels, e.g. Telegram/Discord/Mochat)
docker run -v ~/.blackcat:/home/blackcat/.blackcat -p 18790:18790 blackcat gateway

# Or run a single command
docker run -v ~/.blackcat:/home/blackcat/.blackcat --rm blackcat agent -m "Hello!"
docker run -v ~/.blackcat:/home/blackcat/.blackcat --rm blackcat status
```

## Linux Service

Run the gateway as a systemd user service so it starts automatically and restarts on failure.

**1. Find the blackcat binary path:**

```bash
which blackcat   # e.g. /home/user/.local/bin/blackcat
```

**2. Create the service file** at `~/.config/systemd/user/blackcat-gateway.service` (replace `ExecStart` path if needed):

```ini
[Unit]
Description=Nanobot Gateway
After=network.target

[Service]
Type=simple
ExecStart=%h/.local/bin/blackcat gateway
Restart=always
RestartSec=10
NoNewPrivileges=yes
ProtectSystem=strict
ReadWritePaths=%h

[Install]
WantedBy=default.target
```

**3. Enable and start:**

```bash
systemctl --user daemon-reload
systemctl --user enable --now blackcat-gateway
```

**Common operations:**

```bash
systemctl --user status blackcat-gateway        # check status
systemctl --user restart blackcat-gateway       # restart after config changes
journalctl --user -u blackcat-gateway -f        # follow logs
```

If you edit the `.service` file itself, run `systemctl --user daemon-reload` before restarting.

> **Note:** User services only run while you are logged in. To keep the gateway running after logout, enable lingering:
>
> ```bash
> loginctl enable-linger $USER
> ```

## macOS LaunchAgent

Use a LaunchAgent when you want `blackcat gateway` to stay online after you log in, without keeping a terminal open.

**1. Get the absolute `blackcat` path:**

```bash
which blackcat   # e.g. /Users/youruser/.local/bin/blackcat
```

Use that exact path in the plist. It keeps the Python environment from your install method.

**2. Create `~/Library/LaunchAgents/ai.blackcat.gateway.plist`:**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>ai.blackcat.gateway</string>

  <key>ProgramArguments</key>
  <array>
    <string>/Users/youruser/.local/bin/blackcat</string>
    <string>gateway</string>
    <string>--workspace</string>
    <string>/Users/youruser/.blackcat/workspace</string>
  </array>

  <key>WorkingDirectory</key>
  <string>/Users/youruser/.blackcat/workspace</string>

  <key>RunAtLoad</key>
  <true/>

  <key>KeepAlive</key>
  <dict>
    <key>SuccessfulExit</key>
    <false/>
  </dict>

  <key>StandardOutPath</key>
  <string>/Users/youruser/.blackcat/logs/gateway.log</string>

  <key>StandardErrorPath</key>
  <string>/Users/youruser/.blackcat/logs/gateway.error.log</string>
</dict>
</plist>
```

**3. Load and start it:**

```bash
mkdir -p ~/Library/LaunchAgents ~/.blackcat/logs
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.blackcat.gateway.plist
launchctl enable gui/$(id -u)/ai.blackcat.gateway
launchctl kickstart -k gui/$(id -u)/ai.blackcat.gateway
```

**Common operations:**

```bash
launchctl list | grep ai.blackcat.gateway
launchctl kickstart -k gui/$(id -u)/ai.blackcat.gateway   # restart
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/ai.blackcat.gateway.plist
```

After editing the plist, run `launchctl bootout ...` and `launchctl bootstrap ...` again.

> **Note:** if startup fails with "address already in use", stop the manually started `blackcat gateway` process first.
