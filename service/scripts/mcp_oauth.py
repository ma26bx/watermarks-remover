"""Single-owner OAuth authorization-code + S256 PKCE adapter for the MCP service.

The owner authenticates on this server using WATERMARKS_SERVER_API_KEY. The key
is never returned to the OAuth client. Existing direct bearer-key clients work.
Pending consent, authorization codes and refresh tokens live in bounded memory;
after a restart users may need to reconnect. DCR client IDs and access tokens
are signed with a domain-separated key, so DCR registrations survive restarts.
"""

import base64
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import threading
import time
from http.cookies import CookieError, SimpleCookie
from urllib.parse import parse_qs, urlencode, urlparse


SCOPE = "files:manage"
CALLBACK = "https://chatgpt.com/connector_platform_oauth_redirect"


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


class OAuthState:
    def __init__(self, owner_key, public_url):
        url = urlparse(public_url)
        if url.scheme != "https" or not url.netloc or url.path not in ("", "/") or url.query or url.fragment or url.username:
            raise ValueError("WATERMARKS_PUBLIC_URL must be an HTTPS origin")
        self.base = public_url.rstrip("/")
        self.resource = self.base + "/mcp"
        self.owner_key = owner_key
        self.signing_key = hmac.digest(owner_key.encode(), b"watermarks-oauth-v1", "sha256")
        self.lock = threading.Lock()
        self.pending = {}
        self.failures = []

    def sign(self, kind, data):
        payload = _b64(json.dumps({"kind": kind, **data}, separators=(",", ":")).encode())
        return payload + "." + _b64(hmac.digest(self.signing_key, payload.encode(), "sha256"))

    def verify(self, token, kind):
        try:
            if not self.owner_key or len(token) > 8192:
                return None
            payload, sig = token.split(".")
            if not hmac.compare_digest(sig, _b64(hmac.digest(self.signing_key, payload.encode(), "sha256"))):
                return None
            data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
            if data.get("kind") != kind or data.get("exp", float("inf")) <= time.time():
                return None
            return data
        except (ValueError, TypeError, UnicodeError):
            return None

    def put(self, kind, data, ttl):
        with self.lock:
            now = time.time()
            self.pending = {k: v for k, v in self.pending.items() if v[2] > now}
            if len(self.pending) >= 2048:
                raise ValueError("Too many pending authorizations; try again later")
            token = secrets.token_urlsafe(32)
            self.pending[hashlib.sha256(token.encode()).digest()] = (kind, data, now + ttl)
            return token

    def get(self, token, kind, consume=False):
        with self.lock:
            key = hashlib.sha256(token.encode()).digest()
            record = self.pending.get(key)
            if not record or record[0] != kind or record[2] <= time.time():
                return None
            if consume:
                del self.pending[key]
            return record[1]

    def client(self, client_id):
        return self.verify(client_id, "client")

    def authorized(self, header):
        if not self.owner_key or not header.startswith("Bearer "):
            return False
        token = header[7:]
        if hmac.compare_digest(token.encode(), self.owner_key.encode()):
            return True
        data = self.verify(token, "access")
        return bool(data and data.get("aud") == self.resource and data.get("iss") == self.base and data.get("scope") == SCOPE)

    def issue(self, client_id):
        now = int(time.time())
        access = self.sign("access", {"aud": self.resource, "iss": self.base,
                                     "scope": SCOPE, "exp": now + 3600,
                                     "client_id": client_id, "jti": secrets.token_urlsafe(16)})
        refresh = self.put("refresh", {"client_id": client_id}, 7 * 86400)
        return {"access_token": access, "token_type": "Bearer", "expires_in": 3600,
                "scope": SCOPE, "refresh_token": refresh}


class OAuthMixin:
    """Place before the existing HTTP Handler in the method resolution order."""
    oauth = None

    def log_message(self, fmt, *args):
        # Authorization URLs contain state/PKCE; never log query strings or bodies.
        super().log_message("%s %s", self.command, urlparse(self.path).path)

    def _oauth_send(self, status, payload, headers=None, html_page=False):
        data = payload.encode("utf-8") if html_page else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8" if html_page else "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Pragma", "no-cache")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _oauth_error(self, message, status=400, error="invalid_request"):
        self._oauth_send(status, {"error": error, "error_description": message})

    def _oauth_unauthorized(self):
        self._oauth_send(401, {"error": "unauthorized"}, {
            "WWW-Authenticate": 'Bearer resource_metadata="' + self.oauth.base + '/.well-known/oauth-protected-resource/mcp", scope="' + SCOPE + '"'})

    def _authorized(self):
        return self.oauth.authorized(self.headers.get("Authorization", ""))

    def _oauth_body(self, content_type):
        if self.headers.get_content_type() != content_type or self.headers.get("Transfer-Encoding"):
            raise ValueError("Unsupported content type")
        size = int(self.headers.get("Content-Length", "0"))
        if size <= 0 or size > 16384:
            raise ValueError("Invalid request size")
        raw = self.rfile.read(size).decode("utf-8")
        if content_type == "application/json":
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("Expected a JSON object")
            return data
        return self._oauth_params(raw)

    @staticmethod
    def _oauth_params(raw):
        data = parse_qs(raw, keep_blank_values=True, max_num_fields=30)
        if any(len(v) != 1 for v in data.values()):
            raise ValueError("Repeated parameters are not allowed")
        return {k: v[0] for k, v in data.items()}

    @staticmethod
    def _oauth_redirect_allowed(uri):
        return isinstance(uri, str) and (uri == CALLBACK or bool(re.fullmatch(
            r"https://chatgpt\.com/connector/oauth/[A-Za-z0-9_-]+", uri)))

    def _oauth_get(self):
        path = urlparse(self.path).path
        state = self.oauth
        if path in ("/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"):
            self._oauth_send(200, {"resource": state.resource, "authorization_servers": [state.base],
                                   "scopes_supported": [SCOPE], "bearer_methods_supported": ["header"]})
        elif path == "/.well-known/oauth-authorization-server":
            self._oauth_send(200, {"issuer": state.base, "authorization_endpoint": state.base + "/oauth/authorize",
                                   "token_endpoint": state.base + "/oauth/token",
                                   "registration_endpoint": state.base + "/oauth/register",
                                   "response_types_supported": ["code"],
                                   "grant_types_supported": ["authorization_code", "refresh_token"],
                                   "token_endpoint_auth_methods_supported": ["none"],
                                   "code_challenge_methods_supported": ["S256"],
                                   "scopes_supported": [SCOPE],
                                   "authorization_response_iss_parameter_supported": True})
        elif path == "/oauth/authorize":
            try:
                if not state.owner_key:
                    self._oauth_error("Server owner key is not configured", 503)
                    return True
                p = self._oauth_params(urlparse(self.path).query)
                client = state.client(p.get("client_id", ""))
                if not client or p.get("redirect_uri") not in client["redirect_uris"]:
                    raise ValueError("Invalid client or redirect URI")
                if p.get("response_type") != "code" or p.get("code_challenge_method") != "S256" or not re.fullmatch(r"[A-Za-z0-9_-]{43}", p.get("code_challenge", "")):
                    raise ValueError("Authorization code with S256 PKCE is required")
                if p.get("resource") != state.resource or p.get("scope", SCOPE) != SCOPE:
                    raise ValueError("Invalid resource or scope")
                cookie = secrets.token_urlsafe(32)
                flow = state.put("flow", {"params": p, "cookie": hashlib.sha256(cookie.encode()).hexdigest()}, 600)
                page = ('<!doctype html><html lang="pt-BR"><meta charset="utf-8">'
                        '<meta name="viewport" content="width=device-width"><title>Conectar Watermarks Remover</title>'
                        '<h1>Conectar ao ChatGPT</h1><p>Autorize o ChatGPT a inspecionar e limpar os arquivos que você enviar.</p>'
                        '<p>Cole a chave WATERMARKS_SERVER_API_KEY do Render. Ela será verificada neste servidor e não será enviada ao ChatGPT.</p>'
                        '<form action="/oauth/authorize" method="post"><input type="hidden" name="flow" value="' + html.escape(flow, quote=True) + '">'
                        '<label>Chave de acesso <input name="owner_key" type="password" required autocomplete="off" maxlength="4096"></label>'
                        '<p><button type="submit">Autorizar conexão</button></p></form></html>')
                self._oauth_send(200, page, {"Set-Cookie": "__Host-wm_oauth=" + cookie + "; Secure; HttpOnly; SameSite=Lax; Path=/; Max-Age=600"}, True)
            except (ValueError, UnicodeError) as exc:
                self._oauth_error(str(exc))
        else:
            return False
        return True

    def _oauth_post(self):
        path = urlparse(self.path).path
        if path not in ("/oauth/register", "/oauth/authorize", "/oauth/token"):
            return False
        state = self.oauth
        try:
            if not state.owner_key:
                self._oauth_error("Server owner key is not configured", 503)
                return True
            if path == "/oauth/register":
                p = self._oauth_body("application/json")
                redirects = p.get("redirect_uris")
                if not isinstance(redirects, list) or not 1 <= len(redirects) <= 5 or not all(self._oauth_redirect_allowed(u) for u in redirects):
                    raise ValueError("Only ChatGPT OAuth redirect URIs are allowed")
                if p.get("token_endpoint_auth_method", "none") != "none" or p.get("grant_types", ["authorization_code"]) not in (["authorization_code"], ["authorization_code", "refresh_token"], ["refresh_token", "authorization_code"]):
                    raise ValueError("Public authorization-code clients are required")
                client = {"redirect_uris": redirects, "nonce": secrets.token_urlsafe(16)}
                self._oauth_send(201, {"client_id": state.sign("client", client), "redirect_uris": redirects,
                                      "token_endpoint_auth_method": "none", "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"]})
            elif path == "/oauth/authorize":
                if self.headers.get("Origin") != state.base:
                    raise ValueError("Invalid form origin")
                p = self._oauth_body("application/x-www-form-urlencoded")
                cookies = SimpleCookie(self.headers.get("Cookie", ""))
                cookie = cookies.get("__Host-wm_oauth")
                flow = state.get(p.get("flow", ""), "flow")
                if not flow or not cookie or not hmac.compare_digest(flow["cookie"], hashlib.sha256(cookie.value.encode()).hexdigest()):
                    raise ValueError("Authorization session expired; reconnect from ChatGPT")
                with state.lock:
                    state.failures = [t for t in state.failures if t > time.time() - 60]
                    if len(state.failures) >= 10:
                        self._oauth_error("Too many failed attempts; wait one minute", 429)
                        return True
                    if not hmac.compare_digest(p.get("owner_key", "").encode(), state.owner_key.encode()):
                        state.failures.append(time.time())
                        self._oauth_error("Incorrect owner key; reconnect from ChatGPT", 401, "access_denied")
                        return True
                flow = state.get(p.get("flow", ""), "flow", consume=True)
                if not flow:
                    raise ValueError("Authorization session already used")
                p = flow["params"]
                code = state.put("code", p, 120)
                location = p["redirect_uri"] + "?" + urlencode({"code": code, "state": p.get("state", ""), "iss": state.base})
                self._oauth_send(303, {}, {"Location": location, "Set-Cookie": "__Host-wm_oauth=; Secure; HttpOnly; SameSite=Lax; Path=/; Max-Age=0"})
            else:
                p = self._oauth_body("application/x-www-form-urlencoded")
                client_id = p.get("client_id", "")
                if not state.client(client_id):
                    self._oauth_error("Unknown client", 400, "invalid_client")
                    return True
                if p.get("resource") != state.resource:
                    raise ValueError("Invalid resource")
                grant = p.get("grant_type")
                if grant == "authorization_code":
                    code = state.get(p.get("code", ""), "code", consume=True)
                    verifier = p.get("code_verifier", "")
                    if not code or code["client_id"] != client_id or code["redirect_uri"] != p.get("redirect_uri") or not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", verifier) or not hmac.compare_digest(_b64(hashlib.sha256(verifier.encode()).digest()), code["code_challenge"]):
                        self._oauth_error("Invalid or expired authorization code / PKCE", 400, "invalid_grant")
                        return True
                elif grant == "refresh_token":
                    refresh = state.get(p.get("refresh_token", ""), "refresh", consume=True)
                    if not refresh or refresh["client_id"] != client_id or p.get("scope", SCOPE) != SCOPE:
                        self._oauth_error("Invalid or expired refresh token; reconnect", 400, "invalid_grant")
                        return True
                else:
                    self._oauth_error("Unsupported grant", 400, "unsupported_grant_type")
                    return True
                self._oauth_send(200, state.issue(client_id))
        except (ValueError, UnicodeError, CookieError) as exc:
            self._oauth_error(str(exc))
        return True


def configured_oauth(owner_key):
    return OAuthState(owner_key, os.environ.get("WATERMARKS_PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "https://watermarks-remover-cjct.onrender.com")
