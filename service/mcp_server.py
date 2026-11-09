
#!/usr/bin/env python3
"""Conexao MCP para o watermarks-remover."""

import json
import os
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import server as core
from mcp_oauth import OAuthMixin, configured_oauth
from text_analysis import analyze_text, normalize_text


TOOLS = [
    {
        "name": "analyze_text",
        "description": "Analisa texto colado: legibilidade descritiva, repetições, Unicode e estilometria. Sem modelos ou diagnóstico de autoria. Até 100 mil caracteres por análise.",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    },
    {
        "name": "normalize_text",
        "description": "Retorna uma cópia do texto com limpeza Unicode e espaços normalizados, sem reescrever. Preserva sequências emoji legítimas. Até 100 mil caracteres. Normalização NFKC e homoglifos são opcionais.",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "inputSchema": {"type": "object", "properties": {
            "text": {"type": "string", "maxLength": 100000},
            "options": {"type": "object", "additionalProperties": False, "properties": {
                "nfkc": {"type": "boolean", "default": False},
                "aggressive_homoglyphs": {"type": "boolean", "default": False},
                "normalize_spaces": {"type": "boolean", "default": True}}}}, "required": ["text"]},
    },
    {
        "name": "capabilities",
        "description": "Consulta os recursos disponiveis no servidor.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "inspect_file",
        "description": "Inspeciona um arquivo em busca de metadados e marcas.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "file": {
                    "type": "string",
                    "description": "Conteudo do arquivo em base64",
                },
            },
            "required": ["name", "file"],
        },
    },
    {
        "name": "detect_file",
        "description": "Executa os detectores disponiveis para um arquivo.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "file": {"type": "string"},
            },
            "required": ["name", "file"],
        },
    },
    {
        "name": "clean_file",
        "description": "Limpa um arquivo e retorna o resultado em base64.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "file": {"type": "string"},
                "options": {
                    "type": "object",
                    "additionalProperties": True,
                },
            },
            "required": ["name", "file"],
        },
    },
]


class MCPHandler(OAuthMixin, core.Handler):
    oauth = configured_oauth(core.API_KEY)

    def do_GET(self):
        if self._oauth_get():
            return
        if urlparse(self.path).path == "/mcp":
            if not self._authorized():
                self._oauth_unauthorized()
            else:
                self._respond(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "Use POST"})
            return
        super().do_GET()

    def do_POST(self):
        if self._oauth_post():
            return
        if urlparse(self.path).path != "/mcp":
            return super().do_POST()

        if not self._authorized():
            return self._oauth_unauthorized()

        request = self._read_json()
        if not isinstance(request, dict):
            return self._respond(
                HTTPStatus.BAD_REQUEST, {"error": "invalid JSON"}
            )

        method = request.get("method")
        request_id = request.get("id")

        if request_id is None:
            self.send_response(HTTPStatus.ACCEPTED)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        response = {"jsonrpc": "2.0", "id": request_id}

        try:
            if method == "initialize":
                response["result"] = {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": "watermarks-remover",
                        "version": "1.2.0",
                    },
                }
            elif method == "ping":
                response["result"] = {}
            elif method == "tools/list":
                response["result"] = {"tools": TOOLS}
            elif method == "tools/call":
                params = request.get("params") or {}
                name = params.get("name")
                args = params.get("arguments") or {}

                if not isinstance(args, dict):
                    raise ValueError("arguments deve ser um objeto")

                if name == "capabilities":
                    result = {"ok": True, **core.capabilities()}
                    result["text_features"] = {"readability": True, "repetitions": True, "unicode_details": True,
                                               "inline_analysis": True, "unicode_cleanup_without_rewrite": True,
                                               "max_analysis_characters": 100000}
                elif name == "analyze_text":
                    result = analyze_text(args.get("text"))
                elif name == "normalize_text":
                    result = normalize_text(args.get("text"), args.get("options"))
                elif name in ("inspect_file", "detect_file", "clean_file"):
                    data, filename = core._decode_input(args)

                    if name == "inspect_file":
                        result = core._inspect_payload(data, filename, False)
                        if result.get("kind") == "text":
                            result["report"]["text_analysis"] = analyze_text(data.decode("utf-8", errors="surrogateescape"))
                    elif name == "detect_file":
                        result = core._detect_payload(data, filename)
                    else:
                        options = core._parse_clean_options(
                            args.get("options")
                        )
                        result = core._clean_payload(
                            data, filename, options
                        )
                else:
                    raise ValueError("Ferramenta desconhecida")

                response["result"] = {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(result, ensure_ascii=False),
                        }
                    ],
                    "isError": False,
                }
            else:
                response["error"] = {
                    "code": -32601,
                    "message": "Metodo desconhecido",
                }

        except ValueError as exc:
            response["result"] = {
                "content": [{"type": "text", "text": str(exc)}],
                "isError": True,
            }
        except Exception:
            self.log_error("MCP tool execution failed")
            response["result"] = {
                "content": [
                    {"type": "text", "text": "Erro interno no servidor"}
                ],
                "isError": True,
            }

        self._respond(HTTPStatus.OK, response)


def configure_clean_strategy():
    """Load the rewrite strategy for MCP as well as the standalone HTTP server."""
    configured = os.environ.get("WATERMARKS_CLEAN_STRATEGY_FILE")
    path = Path(configured) if configured else Path(core.__file__).with_name("clean_strategy.json")
    core._DEFAULT_STRATEGY = core._load_default_strategy(path)


if __name__ == "__main__":
    configure_clean_strategy()
    host = os.environ.get("WATERMARKS_SERVER_HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8765"))

    httpd = ThreadingHTTPServer((host, port), MCPHandler)
    print(f"Watermarks MCP iniciado na porta {port}", flush=True)
    httpd.serve_forever()

