"""Configuration management."""
import json
import os

DEFAULT_CONFIG = {
    "port": 8081,
    "host": "127.0.0.1",          # Changed default: localhost only for security
    "retry_attempts": 3,
    "retry_delay_sec": 2,
    "request_timeout_sec": 180,
    "gemini_bl": "boq_assistant-bard-web-server_20260525.09_p0",
    "default_model": "gemini-3.5-flash",
    "log_requests": True,
    # ── Security ──────────────────────────────────────────────────────────────
    # List of accepted Bearer tokens. REQUIRED — server rejects all requests
    # if this list is empty or missing. Generate with: python -c "import secrets; print(secrets.token_urlsafe(32))"
    "api_keys": [],
    # ── Legacy single-account fields (still supported) ────────────────────────
    "cookie_file": None,
    "auth_user": None,
    "proxy": None,
    "xsrf_token": None,
    # ── Multi-account list (takes precedence over legacy fields) ──────────────
    # "accounts": [
    #   {"name": "account1", "cookie_file": "cookies/account1.json", "auth_user": null, "proxy": null},
    #   {"name": "account2", "cookie_file": "cookies/account2.json", "auth_user": 1,    "proxy": null}
    # ]
    "accounts": [],
}

CONFIG = dict(DEFAULT_CONFIG)


def load_config(path: str = None):
    """Load config from JSON file."""
    if path and os.path.exists(path):
        with open(path) as f:
            CONFIG.update(json.load(f))
    return CONFIG


def find_config():
    """Search for config file in standard locations."""
    for p in ["./config.json", os.path.expanduser("~/.config/gemini-web2api/config.json")]:
        if os.path.exists(p):
            return p
    return None
