# gemini-web2api

<p align="center">
  <img src="logo.png" width="200" alt="gemini-web2api logo">
</p>

[中文文档](README_CN.md)

Convert Google Gemini's web interface into an OpenAI-compatible API. Multi-account load balancing, mandatory Bearer token authentication, cross-platform.

## Features

- **Multi-account**: Distribute requests across multiple Google accounts with automatic round-robin load balancing
- **Mandatory Auth**: Every request requires a Bearer token — the server refuses to start without configured keys
- **OpenAI Compatible**: Drop-in replacement for `/v1/chat/completions` and `/v1/models`
- **Tool Calling**: Full function calling support (OpenAI format)
- **Multiple Models**: Flash, Flash Thinking (20k+ char output), Pro, Auto, Lite
- **Thinking Depth**: Adjustable via `@think=N` suffix (0=deepest, 4=shallowest)
- **Web Search**: Built-in internet access (Gemini's native search)
- **Streaming**: SSE streaming support via `httpx`
- **Codex CLI**: Responses API (`/v1/responses`) for OpenAI Codex integration
- **Gemini CLI**: Google native API (`/v1beta/models`) for Gemini CLI compatibility
- **Localhost-only by default**: Binds to `127.0.0.1` out of the box for safety

---

## Quick Start (local)

### 1. Install dependencies

```bash
pip install httpx
```

### 2. Generate a secret token

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
# Example output: Xk3mP9qR2vLn8wJdFtYuCbHsAeZoGiNx4KlMpQrWvUy
```

### 3. Create `config.json`

```json
{
  "port": 8081,
  "host": "127.0.0.1",
  "api_keys": ["YOUR_TOKEN_HERE"],
  "accounts": [
    {
      "name": "account1",
      "cookie_file": "cookies/account1.json",
      "auth_user": null
    }
  ],
  "default_model": "gemini-3.5-flash",
  "log_requests": true
}
```

### 4. Run

```bash
python gemini_web2api.py --config config.json
# or
python -m gemini_web2api --config config.json
```

The server will print the number of accounts loaded and API keys configured, then listen at `http://127.0.0.1:8081`.

---

## Authentication

Every request **must** include a Bearer token. The server returns `401` without one.

```bash
# Header (preferred)
Authorization: Bearer YOUR_TOKEN_HERE

# Alternative header
x-api-key: YOUR_TOKEN_HERE
```

The server **refuses to start** if `api_keys` is empty or missing. You can configure multiple keys (one per user/application):

```json
"api_keys": [
  "token-for-cherry-studio",
  "token-for-my-scripts",
  "token-for-colleague"
]
```

---

## Multi-account Setup

Add all your Google accounts to the `accounts` list. Requests are distributed in round-robin order.

```json
{
  "api_keys": ["YOUR_TOKEN"],
  "accounts": [
    {
      "name": "personal",
      "cookie_file": "cookies/personal.json",
      "auth_user": null,
      "proxy": null
    },
    {
      "name": "work",
      "cookie_file": "cookies/work.json",
      "auth_user": 1,
      "proxy": null
    },
    {
      "name": "via-proxy",
      "cookie_file": "cookies/account3.json",
      "auth_user": null,
      "proxy": "http://127.0.0.1:7890"
    }
  ]
}
```

Each account has its own `cookie_file`, `auth_user` index, and optional `proxy`. If you only have one account, you can still use the legacy flat fields (`cookie_file`, `auth_user`, `proxy`) at the top level.

### How to obtain cookies

1. Open Chrome and go to [gemini.google.com](https://gemini.google.com). Sign in with a Google account.
2. Open DevTools (`F12`) → **Network** tab → click any request to `gemini.google.com`.
3. In the **Headers** panel, find the `Cookie` request header and copy its full value.
4. Find the `SAPISID` cookie specifically (it appears inside that string).
5. Create a file like `cookies/account1.json`:

```json
{
  "cookie": "SID=xxx; HSID=xxx; SSID=xxx; APISID=xxx; SAPISID=xxx; __Secure-1PSID=xxx",
  "sapisid": "XXXXXXXX/YYYYYYYYYYYYYYYY"
}
```

> **`auth_user`**: If the signed-in Gemini URL contains `/u/1/`, `/u/2/`, etc., set `auth_user` to that number. For the primary account it is `null`.

---

## Client Configuration

### Cherry Studio / ChatBox / any OpenAI client

| Field | Value |
|-------|-------|
| Base URL | `http://127.0.0.1:8081/v1` |
| API Key | One of the tokens in `api_keys` |
| Model | `gemini-3.5-flash-thinking` |

### curl

```bash
curl http://127.0.0.1:8081/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN_HERE" \
  -d '{"model":"gemini-3.5-flash","messages":[{"role":"user","content":"Hello!"}]}'
```

### OpenAI Python SDK

```python
from openai import OpenAI
client = OpenAI(base_url="http://127.0.0.1:8081/v1", api_key="YOUR_TOKEN_HERE")
resp = client.chat.completions.create(
    model="gemini-3.5-flash-thinking",
    messages=[{"role": "user", "content": "Explain quantum computing"}]
)
print(resp.choices[0].message.content)
```

### Gemini CLI

```bash
export GEMINI_API_KEY=YOUR_TOKEN_HERE
export GOOGLE_GEMINI_BASE_URL=http://127.0.0.1:8081
gemini
```

---

## Available Models

| Model | Description | Output |
|-------|-------------|--------|
| `gemini-3.5-flash` | Fast general-purpose | ~12k chars |
| `gemini-3.5-flash-thinking` | Deep thinking, longest output | **~20k chars** |
| `gemini-3.5-flash-thinking-lite` | Adaptive thinking depth | ~15k chars |
| `gemini-3.1-pro` | Pro (needs cookie for real routing) | ~12k chars |
| `gemini-3.1-pro-enhanced` | Pro with enhanced output | ~12k chars |
| `gemini-auto` | Auto model selection | varies |
| `gemini-flash-lite` | Lightweight fast | ~10k chars |

### Thinking Depth

Append `@think=N` to any model name:

```
gemini-3.5-flash-thinking@think=0   # deepest (default)
gemini-3.5-flash-thinking@think=2   # medium
gemini-3.5-flash-thinking@think=4   # shallowest / fastest
```

---

## Proxy

If you cannot reach `gemini.google.com` directly, configure a proxy per-account or globally:

```json
{
  "proxy": "http://127.0.0.1:7890",
  "accounts": [
    { "name": "account1", "cookie_file": "cookies/a1.json", "proxy": "http://127.0.0.1:7891" }
  ]
}
```

Account-level proxy takes precedence over the global one. Also works via environment variable:

```bash
export HTTPS_PROXY=http://127.0.0.1:7890
```

---

## Linux Server Deployment

This section covers deploying gemini-web2api on a Linux server (Ubuntu/Debian) with automatic startup via **systemd**.

### Step 1 — Connect to your server and install Python

```bash
ssh user@your-server-ip
sudo apt update && sudo apt install -y python3 python3-pip git
pip3 install httpx
```

### Step 2 — Create a dedicated user (recommended)

Running the service as a non-root user limits the damage if something goes wrong.

```bash
sudo useradd -r -m -d /opt/gemini-web2api -s /bin/bash gemini
```

### Step 3 — Upload the project files

**Option A — Copy from your local machine:**

```bash
# Run this on your LOCAL machine
scp gemini-web2api-multicuenta.zip user@your-server-ip:/tmp/
```

Then on the server:

```bash
sudo -u gemini bash -c "
  cd /opt/gemini-web2api
  unzip /tmp/gemini-web2api-multicuenta.zip
  mv gemini-web2api-main/* .
  rmdir gemini-web2api-main
"
```

**Option B — Clone from GitHub (if you push the repo):**

```bash
sudo -u gemini git clone https://github.com/YOUR_USER/gemini-web2api.git /opt/gemini-web2api
```

### Step 4 — Create the cookies directory and cookie files

```bash
sudo -u gemini mkdir -p /opt/gemini-web2api/cookies
```

Create each account's cookie file. Replace the placeholder values with the real cookies you copied from the browser:

```bash
sudo -u gemini nano /opt/gemini-web2api/cookies/account1.json
```

Paste and save:

```json
{
  "cookie": "SID=xxx; HSID=xxx; SSID=xxx; APISID=xxx; SAPISID=xxx; __Secure-1PSID=xxx",
  "sapisid": "XXXXXXXX/YYYYYYYYYYYYYYYY"
}
```

Restrict permissions so only the `gemini` user can read them:

```bash
sudo chmod 600 /opt/gemini-web2api/cookies/*.json
sudo chown gemini:gemini /opt/gemini-web2api/cookies/*.json
```

### Step 5 — Generate a secure token and create `config.json`

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Copy the output, then create the config:

```bash
sudo -u gemini nano /opt/gemini-web2api/config.json
```

```json
{
  "port": 8081,
  "host": "127.0.0.1",
  "api_keys": ["PASTE_YOUR_TOKEN_HERE"],
  "accounts": [
    {
      "name": "account1",
      "cookie_file": "/opt/gemini-web2api/cookies/account1.json",
      "auth_user": null,
      "proxy": null
    }
  ],
  "default_model": "gemini-3.5-flash",
  "retry_attempts": 3,
  "retry_delay_sec": 2,
  "request_timeout_sec": 180,
  "log_requests": true
}
```

```bash
sudo chmod 640 /opt/gemini-web2api/config.json
sudo chown gemini:gemini /opt/gemini-web2api/config.json
```

### Step 6 — Test the server manually

Before creating the service, confirm it starts correctly:

```bash
sudo -u gemini python3 /opt/gemini-web2api/gemini_web2api.py --config /opt/gemini-web2api/config.json
```

Expected output:

```
gemini-web2api v1.2.0
  Listening: http://127.0.0.1:8081
  Accounts:  1 (account1)
  API keys:  1 configured
```

Press `Ctrl+C` to stop, then continue to the next step.

### Step 7 — Create a systemd service

```bash
sudo nano /etc/systemd/system/gemini-web2api.service
```

Paste the following:

```ini
[Unit]
Description=gemini-web2api — Gemini Web to OpenAI API proxy
Documentation=https://github.com/YOUR_USER/gemini-web2api
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=gemini
Group=gemini
WorkingDirectory=/opt/gemini-web2api
ExecStart=/usr/bin/python3 /opt/gemini-web2api/gemini_web2api.py --config /opt/gemini-web2api/config.json
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=gemini-web2api

# Security hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/opt/gemini-web2api

[Install]
WantedBy=multi-user.target
```

### Step 8 — Enable and start the service

```bash
# Reload systemd so it picks up the new file
sudo systemctl daemon-reload

# Enable the service to start automatically on boot
sudo systemctl enable gemini-web2api

# Start it now
sudo systemctl start gemini-web2api

# Verify it is running
sudo systemctl status gemini-web2api
```

You should see `Active: active (running)`.

### Step 9 — Check the logs

```bash
# Follow live logs
sudo journalctl -u gemini-web2api -f

# Last 50 lines
sudo journalctl -u gemini-web2api -n 50

# Logs since last boot
sudo journalctl -u gemini-web2api -b
```

### Step 10 — Test the API from the server

```bash
curl http://127.0.0.1:8081/v1/models \
  -H "Authorization: Bearer YOUR_TOKEN_HERE"
```

You should receive a JSON list of available models.

---

## Exposing the API over HTTPS (optional but recommended)

If you want to use the API from outside the server, **do not expose port 8081 directly**. Instead, put it behind a reverse proxy with TLS. Below is a minimal nginx + Certbot setup.

### Install nginx and Certbot

```bash
sudo apt install -y nginx certbot python3-certbot-nginx
```

### Create the nginx site config

```bash
sudo nano /etc/nginx/sites-available/gemini-api
```

```nginx
server {
    listen 80;
    server_name api.yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:8081;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;

        # Required for SSE streaming
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;
        chunked_transfer_encoding on;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/gemini-api /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

### Get a free TLS certificate

```bash
sudo certbot --nginx -d api.yourdomain.com
```

Certbot will automatically edit your nginx config to add HTTPS and renew the certificate. After this your API is accessible at `https://api.yourdomain.com/v1`.

In your client, update the base URL to `https://api.yourdomain.com/v1`.

---

## Docker

```bash
cp config.example.json config.json
# Edit config.json with your tokens and cookie paths
docker build -t gemini-web2api .
docker run -d \
  --name gemini-web2api \
  -p 127.0.0.1:8081:8081 \
  -v ./config.json:/app/config.json \
  -v ./cookies:/app/cookies \
  gemini-web2api
```

Or with Docker Compose:

```bash
docker compose up -d
```

---

## Service Management (quick reference)

```bash
# Start / stop / restart
sudo systemctl start gemini-web2api
sudo systemctl stop gemini-web2api
sudo systemctl restart gemini-web2api

# Enable / disable autostart on boot
sudo systemctl enable gemini-web2api
sudo systemctl disable gemini-web2api

# Current status
sudo systemctl status gemini-web2api

# Live logs
sudo journalctl -u gemini-web2api -f

# Update cookies without full restart
# (just edit the cookie file — it is reloaded on next request automatically)

# Apply a config.json change
sudo systemctl restart gemini-web2api
```

---

## Configuration Reference

| Key | Default | Description |
|-----|---------|-------------|
| `port` | `8081` | Port to listen on |
| `host` | `127.0.0.1` | Bind address. Use `0.0.0.0` only behind a reverse proxy |
| `api_keys` | `[]` | **Required.** List of accepted Bearer tokens. Server won't start if empty |
| `accounts` | `[]` | List of Google account objects (see below). Falls back to legacy flat fields |
| `default_model` | `gemini-3.5-flash` | Model used when none is specified |
| `retry_attempts` | `3` | Number of retries on upstream error |
| `retry_delay_sec` | `2` | Seconds between retries |
| `request_timeout_sec` | `180` | HTTP timeout for Gemini upstream requests |
| `gemini_bl` | *(built-in)* | Gemini build label. Update if requests start failing |
| `log_requests` | `true` | Log requests and errors to stderr |
| `proxy` | `null` | Global HTTP proxy. Overridden per-account |

### Account object fields

| Key | Description |
|-----|-------------|
| `name` | Label shown in logs |
| `cookie_file` | Path to the JSON cookie file for this account |
| `auth_user` | Account index from the Gemini URL (`/u/N/`). `null` for primary account |
| `proxy` | Per-account proxy, overrides global `proxy` |
| `xsrf_token` | XSRF token (`SNlM0e` field in page source). Required if requests return HTTP 400 |

---

## Tool Calling

```python
resp = client.chat.completions.create(
    model="gemini-3.5-flash",
    messages=[{"role": "user", "content": "What's the weather in Tokyo?"}],
    tools=[{
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get weather for a city",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"]
            }
        }
    }]
)
```

---

## Limitations

- **No image/multimodal input**: Image inputs in messages will be ignored with a note.
- **Not real Pro/Ultra**: Without a paid subscription cookie, `gemini-3.1-pro` routes to Flash.
- **Single-turn only**: Multi-turn context is simulated by including previous messages in the prompt.
- **Rate limits**: Google may throttle high-frequency requests. The server retries automatically.
- **Cookie expiry**: Google session cookies expire periodically (typically every few weeks). Update them by repeating the export steps and restarting the service.

---

## Requirements

- Python 3.8+
- `httpx` (optional but recommended for true streaming; install with `pip install httpx`)
- Network access to `gemini.google.com`

---

## How It Works

This tool reverse-engineers Google Gemini's web StreamGenerate protocol. It converts between OpenAI's API format and Gemini's internal protobuf-like format, sending requests to the same endpoint the Gemini web app uses.

Model selection is controlled by field `[79]` in the request payload, mapped from Gemini's frontend JavaScript source (`MODE_CATEGORY` enum). Multi-account load balancing distributes requests in round-robin order across all configured cookie sessions.

---

## License

MIT

---

## Acknowledgments

- Inspired by the open-source API proxy ecosystem

### 🚩 友情链接

[![GenericAgent](https://img.shields.io/badge/Agent_Framework-GenericAgent-orange?style=for-the-badge&logo=github)](https://github.com/lsdefine/GenericAgent)
[![LinuxDo](https://img.shields.io/badge/社区-LinuxDo-blue?style=for-the-badge)](https://linux.do/)
