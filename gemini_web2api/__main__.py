"""Entry point: python -m gemini_web2api"""
import argparse
import os

from .config import CONFIG, load_config, find_config
from .models import MODELS
from .gemini import HAS_HTTPX, log
from .accounts import POOL
from .server import GeminiHandler, ThreadedServer
from . import __version__


def main():
    parser = argparse.ArgumentParser(description="Gemini Web to OpenAI API (multi-account)")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--host", type=str, default=None, help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--config", type=str, default=None)
    parser.add_argument("--cookie-file", type=str, default=None, help="Cookie file for single-account mode")
    parser.add_argument("--proxy", type=str, default=None, help="HTTP proxy, e.g. http://127.0.0.1:7890")
    parser.add_argument("--version", action="version", version=f"gemini-web2api {__version__}")
    args = parser.parse_args()

    config_path = args.config or os.environ.get("GEMINI_WEB2API_CONFIG") or find_config()
    if config_path:
        load_config(config_path)

    if args.port:
        CONFIG["port"] = args.port
    if args.host:
        CONFIG["host"] = args.host
    if args.cookie_file:
        CONFIG["cookie_file"] = args.cookie_file
    if args.proxy:
        CONFIG["proxy"] = args.proxy

    # Initialise account pool from config
    POOL.load_from_config(CONFIG)

    # Safety check: refuse to start without api_keys
    if not CONFIG.get("api_keys"):
        print("ERROR: No api_keys configured.")
        print("  Add at least one token to config.json:")
        print('  "api_keys": ["<your-secret-token>"]')
        print()
        print("  Generate a secure token with:")
        print('  python -c "import secrets; print(secrets.token_urlsafe(32))"')
        raise SystemExit(1)

    port = CONFIG["port"]
    host = CONFIG["host"]
    server = ThreadedServer((host, port), GeminiHandler)

    print(f"gemini-web2api v{__version__}")
    print(f"  Listening: http://{host}:{port}")
    print(f"  Base URL:  http://{host}:{port}/v1")
    print(f"  Models:    {', '.join(MODELS.keys())}")
    print(f"  Accounts:  {POOL.count()} ({', '.join(POOL.names())})")
    print(f"  API keys:  {len(CONFIG['api_keys'])} configured")
    print(f"  Proxy:     {CONFIG.get('proxy') or 'system env'}")
    print(f"  Streaming: {'httpx (true streaming)' if HAS_HTTPX else 'urllib (buffered)'}")
    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.shutdown()


if __name__ == "__main__":
    main()
