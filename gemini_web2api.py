#!/usr/bin/env python3
"""
gemini-web2api - Gemini Web to OpenAI API proxy (multi-account + auth).

Usage:
    pip install httpx
    python gemini_web2api.py [--port 8081] [--config config.json]

Authentication (REQUIRED):
    Every request must include a Bearer token:
        Authorization: Bearer <your-token>
    Configure tokens in config.json under "api_keys".

Multi-account:
    Add an "accounts" list in config.json. Requests are distributed
    round-robin across all configured accounts.
"""
import json
import urllib.request
import urllib.parse
import time
import ssl
import sys
import uuid
import re
import os
import hashlib
import argparse
import base64
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

__version__ = "1.2.0"

# ─── Configuration ────────────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "port": 8081,
    "host": "127.0.0.1",           # localhost-only by default
    "retry_attempts": 3,
    "retry_delay_sec": 2,
    "request_timeout_sec": 180,
    "gemini_bl": "boq_assistant-bard-web-server_20260525.09_p0",
    "default_model": "gemini-3.5-flash",
    "log_requests": True,
    # ── Security ──────────────────────────────────────────────────────────────
    # Required. Generate: python -c "import secrets; print(secrets.token_urlsafe(32))"
    "api_keys": [],
    # ── Legacy single-account (still supported) ───────────────────────────────
    "cookie_file": None,
    "auth_user": None,
    "proxy": None,
    "xsrf_token": None,
    # ── Multi-account list (takes precedence over legacy fields) ──────────────
    "accounts": [],
}

CONFIG = dict(DEFAULT_CONFIG)

# ─── Models ───────────────────────────────────────────────────────────────────

MODELS = {
    "gemini-3.5-flash":              {"mode": 1, "think": 4, "desc": "Fast general-purpose model"},
    "gemini-3.5-flash-thinking":     {"mode": 2, "think": 0, "desc": "Deep thinking mode"},
    "gemini-3.1-pro":                {"mode": 3, "think": 4, "desc": "Pro model"},
    "gemini-3.1-pro-enhanced":       {"mode": 3, "think": 4, "extra": {31: 2, 80: 3}, "desc": "Pro enhanced"},
    "gemini-auto":                   {"mode": 4, "think": 4, "desc": "Auto model selection"},
    "gemini-3.5-flash-thinking-lite":{"mode": 5, "think": 0, "desc": "Dynamic thinking"},
    "gemini-flash-lite":             {"mode": 6, "think": 4, "desc": "Lightweight fast model"},
}

# ─── Logging ──────────────────────────────────────────────────────────────────

def log(msg: str):
    if CONFIG["log_requests"]:
        sys.stderr.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        sys.stderr.flush()

# ─── Multi-account pool ───────────────────────────────────────────────────────

class Account:
    def __init__(self, cfg: dict, index: int):
        self.name        = cfg.get("name") or f"account-{index}"
        self.cookie_file = cfg.get("cookie_file", "")
        self.auth_user   = cfg.get("auth_user", None)
        self.proxy       = cfg.get("proxy", None)
        self.xsrf_token  = cfg.get("xsrf_token", None)
        self._cache      = {"str": "", "sapisid": None, "mtime": 0}

    def load_cookie(self):
        if not self.cookie_file or not os.path.exists(self.cookie_file):
            return "", None
        try:
            mtime = os.path.getmtime(self.cookie_file)
            if mtime == self._cache["mtime"] and self._cache["str"]:
                return self._cache["str"], self._cache["sapisid"]
            with open(self.cookie_file) as f:
                content = f.read().strip()
            if content.startswith("{"):
                data = json.loads(content)
                cookie_str = data.get("cookie", "")
                sapisid = data.get("sapisid", "")
            else:
                cookie_str = content
                pairs = dict(p.split("=", 1) for p in cookie_str.split("; ") if "=" in p)
                sapisid = pairs.get("SAPISID", "")
            self._cache.update({"str": cookie_str, "sapisid": sapisid or None, "mtime": mtime})
            return cookie_str, sapisid if sapisid else None
        except Exception as e:
            log(f"Cookie load error ({self.name}): {e}")
            return self._cache["str"], self._cache["sapisid"]

    def prefix(self):
        return f"/u/{self.auth_user}" if self.auth_user not in (None, "") else ""


class AccountPool:
    def __init__(self):
        self._accounts = []
        self._index = 0
        self._lock = threading.Lock()

    def load(self, config: dict):
        accounts_cfg = config.get("accounts")
        if accounts_cfg:
            self._accounts = [Account(a, i) for i, a in enumerate(accounts_cfg)]
        else:
            self._accounts = [Account({
                "name": "default",
                "cookie_file": config.get("cookie_file", ""),
                "auth_user":   config.get("auth_user", None),
                "proxy":       config.get("proxy", None),
                "xsrf_token":  config.get("xsrf_token", None),
            }, 0)]

    def next(self) -> Account:
        with self._lock:
            if not self._accounts:
                return Account({}, 0)
            acc = self._accounts[self._index % len(self._accounts)]
            self._index += 1
            return acc

    def count(self): return len(self._accounts)
    def names(self):  return [a.name for a in self._accounts]


POOL = AccountPool()

# ─── Gemini protocol ──────────────────────────────────────────────────────────

def _make_sapisidhash(sapisid: str) -> str:
    ts = int(time.time())
    h = hashlib.sha1(f"{ts} {sapisid} https://gemini.google.com".encode()).hexdigest()
    return f"SAPISIDHASH {ts}_{h}"


def _build_headers(acc: Account) -> dict:
    prefix = acc.prefix()
    hdrs = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://gemini.google.com",
        "Referer": f"https://gemini.google.com{prefix}/app",
        "X-Same-Domain": "1",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    if prefix:
        hdrs["X-Goog-AuthUser"] = str(acc.auth_user)
    cookie_str, sapisid = acc.load_cookie()
    if cookie_str:
        hdrs["Cookie"] = cookie_str
    if sapisid:
        hdrs["Authorization"] = _make_sapisidhash(sapisid)
    return hdrs


def _build_payload(prompt: str, model_id: int, think_mode: int, acc: Account) -> str:
    inner = [None] * 102
    inner[0]  = [prompt, 0, None, None, None, None, 0]
    inner[1]  = ["en"]
    inner[2]  = ["", "", "", None, None, None, None, None, None, ""]
    inner[6]  = [0]
    inner[7]  = 1
    inner[10] = 1
    inner[11] = 0
    inner[17] = [[think_mode]]
    inner[18] = 0
    inner[27] = 1
    inner[30] = [4]
    inner[41] = [2]
    inner[53] = 0
    inner[59] = str(uuid.uuid4())
    inner[61] = []
    inner[68] = 1
    inner[79] = model_id
    outer = [None, json.dumps(inner)]
    params = {"f.req": json.dumps(outer)}
    xsrf = acc.xsrf_token or CONFIG.get("xsrf_token")
    if xsrf:
        params["at"] = xsrf
    return urllib.parse.urlencode(params)


def _get_url(acc: Account) -> str:
    reqid = int(time.time()) % 1000000
    prefix = acc.prefix()
    return (
        f"https://gemini.google.com{prefix}/_/BardChatUi/data/"
        "assistant.lamda.BardFrontendService/StreamGenerate"
        f"?bl={CONFIG['gemini_bl']}&hl=en&_reqid={reqid}&rt=c"
    )


def _clean(text: str) -> str:
    text = re.sub(
        r'```(?:python|javascript|text)\?code_(?:reference|stdout)&code_event_index=\d+\n.*?```\n?',
        '', text, flags=re.DOTALL)
    text = re.sub(r'http://googleusercontent\.com/card_content/\d+\n?', '', text)
    return text.strip()


def _texts_from_line(line: str) -> list:
    if '"wrb.fr"' not in line or len(line) < 200:
        return []
    try:
        arr = json.loads(line)
        inner_str = arr[0][2]
        if not inner_str or len(inner_str) < 50:
            return []
        inner = json.loads(inner_str)
        if not (isinstance(inner, list) and len(inner) > 4 and inner[4]):
            return []
        texts = []
        for part in inner[4]:
            if isinstance(part, list) and len(part) > 1 and part[1] and isinstance(part[1], list):
                for t in part[1]:
                    if isinstance(t, str) and t:
                        texts.append(t)
        return texts
    except (json.JSONDecodeError, IndexError, TypeError):
        return []


def _extract_text(raw: str) -> str:
    last = ""
    for line in raw.split("\n"):
        for t in _texts_from_line(line):
            if len(t) > len(last):
                last = t
    return _clean(last)


def gemini_generate(prompt: str, model_id: int, think_mode: int) -> str:
    acc = POOL.next()
    log(f"generate: account={acc.name}")
    body  = _build_payload(prompt, model_id, think_mode, acc).encode()
    url   = _get_url(acc)
    hdrs  = _build_headers(acc)
    ctx   = ssl.create_default_context()
    proxy = acc.proxy or CONFIG.get("proxy")
    last_err = None
    for attempt in range(CONFIG["retry_attempts"]):
        try:
            req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
            if proxy:
                opener = urllib.request.build_opener(
                    urllib.request.ProxyHandler({"http": proxy, "https": proxy}),
                    urllib.request.HTTPSHandler(context=ctx))
                resp = opener.open(req, timeout=CONFIG["request_timeout_sec"])
            else:
                resp = urllib.request.urlopen(req, context=ctx, timeout=CONFIG["request_timeout_sec"])
            return _extract_text(resp.read().decode("utf-8", errors="replace"))
        except Exception as e:
            last_err = e
            if attempt < CONFIG["retry_attempts"] - 1:
                log(f"Retry {attempt+1}/{CONFIG['retry_attempts']} (account={acc.name}): {e}")
                time.sleep(CONFIG["retry_delay_sec"])
    raise last_err


def gemini_generate_stream(prompt: str, model_id: int, think_mode: int):
    acc   = POOL.next()
    log(f"generate_stream: account={acc.name}")
    if not HAS_HTTPX:
        text = gemini_generate(prompt, model_id, think_mode)
        if text: yield text
        return
    body  = _build_payload(prompt, model_id, think_mode, acc)
    url   = _get_url(acc)
    hdrs  = _build_headers(acc)
    proxy = acc.proxy or CONFIG.get("proxy")
    transport = httpx.HTTPTransport(proxy=proxy) if proxy else None
    client = httpx.Client(transport=transport, timeout=CONFIG["request_timeout_sec"], verify=True)
    last_err = None
    for attempt in range(CONFIG["retry_attempts"]):
        try:
            prev = ""
            with client.stream("POST", url, content=body, headers=hdrs) as resp:
                buf = ""
                for chunk in resp.iter_text():
                    buf += chunk
                    while "\n" in buf:
                        line, buf = buf.split("\n", 1)
                        for t in _texts_from_line(line):
                            if len(t) > len(prev):
                                delta = _clean(t[len(prev):])
                                if delta: yield delta
                                prev = t
            return
        except Exception as e:
            last_err = e
            if attempt < CONFIG["retry_attempts"] - 1:
                log(f"Stream retry {attempt+1} (account={acc.name}): {e}")
                time.sleep(CONFIG["retry_delay_sec"])
    raise last_err

# ─── Prompt / tool helpers ────────────────────────────────────────────────────

def messages_to_prompt(messages: list, tools: list = None) -> str:
    parts = []
    if tools:
        tool_defs = []
        for tool in tools:
            fn = tool.get("function", tool) if tool.get("type") == "function" else tool
            tool_defs.append({
                "name": fn.get("name", tool.get("name", "")),
                "description": fn.get("description", tool.get("description", "")),
                "parameters": fn.get("parameters", tool.get("parameters", {})),
            })
        if tool_defs:
            parts.append(
                "[System instruction]: You have access to tools. "
                "To call a tool, respond with:\n"
                '```tool_call\n{"name": "func_name", "arguments": {...}}\n```\n'
                f"Only use tool_call blocks when needed.\n\nAvailable tools:\n{json.dumps(tool_defs, indent=2)}"
            )
    for msg in messages:
        role    = msg.get("role", "user")
        content = msg.get("content", "")
        if isinstance(content, list):
            content = " ".join(c.get("text", "") for c in content if c.get("type") in ("text", "input_text"))
        if role == "system":
            parts.append(f"[System instruction]: {content}")
        elif role == "assistant":
            if msg.get("tool_calls"):
                tc_strs = []
                for tc in msg["tool_calls"]:
                    fn = tc.get("function", {})
                    tc_strs.append(f'```tool_call\n{{"name": "{fn.get("name")}", "arguments": {fn.get("arguments", "{}")}}}\n```')
                parts.append(f"[Assistant]: {content or ''}\n" + "\n".join(tc_strs))
            else:
                parts.append(f"[Assistant]: {content}")
        elif role == "tool":
            parts.append(f"[Tool result for {msg.get('name', '')}]: {content}")
        else:
            parts.append(content if content else "")
    return "\n\n".join(p for p in parts if p)


def parse_tool_calls(text: str) -> tuple:
    tool_calls = []
    pattern = r'```tool_call\s*\n(.*?)\n```'
    clean_parts, last_end = [], 0
    for m in re.finditer(pattern, text, re.DOTALL):
        clean_parts.append(text[last_end:m.start()])
        last_end = m.end()
        try:
            data = json.loads(m.group(1).strip())
            tool_calls.append({"id": f"call_{uuid.uuid4().hex[:8]}", "type": "function",
                                "function": {"name": data["name"],
                                             "arguments": json.dumps(data.get("arguments", {}), ensure_ascii=False)}})
        except (json.JSONDecodeError, KeyError):
            pass
    clean_parts.append(text[last_end:])
    return "".join(clean_parts).strip(), tool_calls

# ─── HTTP handler ─────────────────────────────────────────────────────────────

class GeminiHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log(fmt % args)

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        keys = CONFIG.get("api_keys") or []
        if not keys:
            return False   # deny all if no keys configured
        auth  = self.headers.get("Authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else self.headers.get("x-api-key", "")
        return token in keys

    def _require_auth(self) -> bool:
        if not self._authorized():
            self.send_json({"error": {"message": "Unauthorized. Provide a valid Bearer token.", "type": "auth_error"}}, 401)
            return True
        return False

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    def _resolve_model(self, model_name: str):
        think_override = None
        if "@think=" in model_name:
            model_name, think_str = model_name.rsplit("@think=", 1)
            try: think_override = int(think_str)
            except ValueError: return None, None, None, f"Invalid think level: {think_str}"
        cfg = MODELS.get(model_name)
        if not cfg:
            log(f"Unknown model '{model_name}', falling back to default")
            model_name = CONFIG["default_model"]
            cfg = MODELS[model_name]
        return model_name, cfg["mode"], (think_override if think_override is not None else cfg["think"]), None

    def do_GET(self):
        try:
            if self._require_auth(): return
            if self.path == "/v1/models":
                self.send_json({"object": "list", "data": [
                    {"id": n, "object": "model", "created": 1700000000, "owned_by": "google", "description": c["desc"]}
                    for n, c in MODELS.items()
                ]})
            elif self.path.startswith("/v1beta/models"):
                self.send_json({"models": [
                    {"name": f"models/{n}", "displayName": n, "description": c["desc"],
                     "supportedGenerationMethods": ["generateContent", "streamGenerateContent"]}
                    for n, c in MODELS.items()
                ]})
            elif self.path == "/":
                self.send_json({"status": "ok", "version": __version__, "models": list(MODELS.keys()),
                                "accounts": POOL.count(), "account_names": POOL.names()})
            else:
                self.send_json({"error": "not found"}, 404)
        except (BrokenPipeError, ConnectionResetError): pass

    def do_POST(self):
        try:
            if self._require_auth(): return
            length = int(self.headers.get("Content-Length", 0))
            body   = self.rfile.read(length) if length else b""
            if self.path == "/v1/chat/completions":
                self._handle_chat(body)
            elif self.path == "/v1/responses":
                self._handle_responses(body)
            elif ":generateContent" in self.path:
                self._handle_google_generate(body, stream=False)
            elif ":streamGenerateContent" in self.path:
                self._handle_google_generate(body, stream=True)
            else:
                self.send_json({"error": "not found"}, 404)
        except (BrokenPipeError, ConnectionResetError): pass
        except Exception as e:
            log(f"POST error: {e}")
            try: self.send_json({"error": {"message": str(e)}}, 500)
            except: pass

    def _handle_chat(self, body: bytes):
        try: req = json.loads(body)
        except Exception: self.send_json({"error": {"message": "invalid JSON"}}, 400); return

        model_name, model_id, think_mode, err = self._resolve_model(req.get("model", CONFIG["default_model"]))
        if err: self.send_json({"error": {"message": err}}, 400); return

        tools  = req.get("tools")
        prompt = messages_to_prompt(req.get("messages", []), tools)
        if not prompt.strip(): self.send_json({"error": {"message": "empty prompt"}}, 400); return

        stream = req.get("stream", False)
        cid    = f"chatcmpl-{uuid.uuid4().hex[:12]}"

        if stream and not tools:
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                for delta in gemini_generate_stream(prompt, model_id, think_mode):
                    chunk = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
                             "model": model_name, "choices": [{"index": 0, "delta": {"content": delta}, "finish_reason": None}]}
                    self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode())
                    self.wfile.flush()
                end = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
                       "model": model_name, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
                self.wfile.write(f"data: {json.dumps(end)}\n\ndata: [DONE]\n\n".encode())
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError): pass
            return

        try: text = gemini_generate(prompt, model_id, think_mode)
        except Exception as e: self.send_json({"error": {"message": f"upstream error: {e}"}}, 502); return

        tool_calls = None
        if tools and text:
            text, tool_calls = parse_tool_calls(text)
        msg    = {"role": "assistant", "content": text or None}
        if tool_calls: msg["tool_calls"] = tool_calls
        finish = "tool_calls" if tool_calls else "stop"

        if stream:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            chunk = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
                     "model": model_name, "choices": [{"index": 0, "delta": msg, "finish_reason": finish}]}
            self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\ndata: [DONE]\n\n".encode())
            self.wfile.flush()
        else:
            self.send_json({"id": cid, "object": "chat.completion", "created": int(time.time()),
                            "model": model_name,
                            "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
                            "usage": {"prompt_tokens": len(prompt)//4, "completion_tokens": len(text or "")//4,
                                      "total_tokens": (len(prompt)+len(text or ""))//4}})

    def _handle_responses(self, body: bytes):
        try: req = json.loads(body)
        except Exception: self.send_json({"error": {"message": "invalid JSON"}}, 400); return

        model_name, model_id, think_mode, err = self._resolve_model(req.get("model", CONFIG["default_model"]))
        if err: self.send_json({"error": {"message": err}}, 400); return

        input_items = req.get("input", [])
        tools = req.get("tools")
        messages = []
        if req.get("instructions"):
            messages.append({"role": "system", "content": req["instructions"]})
        if isinstance(input_items, str):
            messages.append({"role": "user", "content": input_items})
        elif isinstance(input_items, list):
            for item in input_items:
                if isinstance(item, str):
                    messages.append({"role": "user", "content": item})
                elif isinstance(item, dict):
                    role    = item.get("role", "user")
                    content = item.get("content", "")
                    if isinstance(content, list):
                        content = " ".join(c.get("text","") for c in content if c.get("type") in ("text","input_text"))
                    messages.append({"role": role, "content": content})
        prompt = messages_to_prompt(messages, tools)
        if not prompt.strip(): self.send_json({"error": {"message": "empty input"}}, 400); return

        try: text = gemini_generate(prompt, model_id, think_mode)
        except Exception as e: self.send_json({"error": {"message": f"upstream error: {e}"}}, 502); return

        tool_calls = None
        if tools and text:
            text, tool_calls = parse_tool_calls(text)

        rid, mid = f"resp_{uuid.uuid4().hex[:16]}", f"msg_{uuid.uuid4().hex[:12]}"
        output = []
        if tool_calls:
            for tc in tool_calls:
                output.append({"type": "function_call", "id": tc["id"], "call_id": tc["id"],
                               "name": tc["function"]["name"], "arguments": tc["function"]["arguments"], "status": "completed"})
        if text or not tool_calls:
            output.append({"type": "message", "id": mid, "role": "assistant", "status": "completed",
                           "content": [{"type": "output_text", "text": text or "", "annotations": []}]})
        self.send_json({"id": rid, "object": "response", "created_at": int(time.time()), "status": "completed",
                        "model": model_name, "output": output,
                        "usage": {"input_tokens": len(prompt)//4, "output_tokens": len(text or "")//4,
                                  "total_tokens": (len(prompt)+len(text or ""))//4}})

    def _handle_google_generate(self, body: bytes, stream: bool):
        try: req = json.loads(body)
        except Exception: self.send_json({"error": {"message": "invalid JSON"}}, 400); return
        m = re.match(r'/v1beta/models/([^:?]+)', self.path)
        model_raw = m.group(1) if m else CONFIG["default_model"]
        model_name, model_id, think_mode, err = self._resolve_model(model_raw)
        if err: self.send_json({"error": {"message": err}}, 400); return

        parts, sys_inst = [], req.get("systemInstruction")
        if sys_inst:
            sys_text = " ".join(p.get("text","") for p in sys_inst.get("parts",[]) if p.get("text"))
            if sys_text: parts.append(f"[System instruction]: {sys_text}")
        for content in req.get("contents", []):
            role = content.get("role", "user")
            text = " ".join(p.get("text","") for p in content.get("parts",[]) if p.get("text"))
            parts.append(f"[Assistant]: {text}" if role == "model" else text)
        prompt = "\n\n".join(p for p in parts if p)
        if not prompt.strip(): self.send_json({"error": {"message": "empty content"}}, 400); return

        try: text = gemini_generate(prompt, model_id, think_mode)
        except Exception as e: self.send_json({"error": {"message": f"upstream error: {e}"}}, 502); return

        resp_obj = {
            "candidates": [{"content": {"parts": [{"text": text or ""}], "role": "model"}, "finishReason": "STOP", "index": 0}],
            "usageMetadata": {"promptTokenCount": len(prompt)//4, "candidatesTokenCount": len(text or "")//4,
                              "totalTokenCount": (len(prompt)+len(text or ""))//4},
            "modelVersion": model_name,
        }
        if stream:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(f"data: {json.dumps(resp_obj)}\n\n".encode())
            self.wfile.flush()
        else:
            self.send_json(resp_obj)

# ─── Main ─────────────────────────────────────────────────────────────────────

def load_config(path: str):
    if path and os.path.exists(path):
        with open(path) as f:
            CONFIG.update(json.load(f))
        log(f"Config loaded: {path}")


def main():
    parser = argparse.ArgumentParser(description="Gemini Web to OpenAI API (multi-account + auth)")
    parser.add_argument("--port",        type=int, default=None)
    parser.add_argument("--host",        type=str, default=None)
    parser.add_argument("--config",      type=str, default=None)
    parser.add_argument("--cookie-file", type=str, default=None)
    parser.add_argument("--proxy",       type=str, default=None)
    parser.add_argument("--version",     action="version", version=f"gemini-web2api {__version__}")
    args = parser.parse_args()

    config_path = args.config or os.environ.get("GEMINI_WEB2API_CONFIG")
    if not config_path:
        for p in ["./config.json", os.path.expanduser("~/.config/gemini-web2api/config.json")]:
            if os.path.exists(p): config_path = p; break
    load_config(config_path)

    if args.port:        CONFIG["port"]        = args.port
    if args.host:        CONFIG["host"]        = args.host
    if args.cookie_file: CONFIG["cookie_file"] = args.cookie_file
    if args.proxy:       CONFIG["proxy"]       = args.proxy

    POOL.load(CONFIG)

    if not CONFIG.get("api_keys"):
        print("ERROR: No api_keys configured. Add tokens to config.json:")
        print('  "api_keys": ["<your-secret-token>"]')
        print()
        print('  Generate: python -c "import secrets; print(secrets.token_urlsafe(32))"')
        sys.exit(1)

    class ThreadedServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    host, port = CONFIG["host"], CONFIG["port"]
    server = ThreadedServer((host, port), GeminiHandler)
    print(f"gemini-web2api v{__version__}")
    print(f"  Listening: http://{host}:{port}")
    print(f"  Base URL:  http://{host}:{port}/v1")
    print(f"  Models:    {', '.join(MODELS.keys())}")
    print(f"  Accounts:  {POOL.count()} ({', '.join(POOL.names())})")
    print(f"  API keys:  {len(CONFIG['api_keys'])} configured")
    print(f"  Proxy:     {CONFIG.get('proxy') or 'system env'}")
    print()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.shutdown()


if __name__ == "__main__":
    main()
