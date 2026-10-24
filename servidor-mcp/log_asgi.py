"""Wrapper ASGI que loga cada request MCP no stderr e repassa o corpo intacto ao app do SDK.

Fica na frente do app porque o mcp.middleware não vê os requests rejeitados antes do dispatch
(_meta incompleto, headers espelhados divergentes). Nunca loga o valor do requestState.
"""

import json
import logging
import sys

log = logging.getLogger("servidor-mcp")
if not log.handlers:
    _h = logging.StreamHandler(sys.stderr)
    _h.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    log.addHandler(_h)
    log.setLevel(logging.INFO)
    log.propagate = False


def _linha(corpo: bytes) -> str:
    try:
        msg = json.loads(corpo)
        if not isinstance(msg, dict):
            return "mcp request corpo=json-nao-objeto"
        params = msg.get("params")
        params = params if isinstance(params, dict) else {}
        meta = params.get("_meta")
        meta = meta if isinstance(meta, dict) else {}
        partes = [f"method={msg.get('method')}", f"id={msg.get('id')}"]
        if msg.get("method") == "tools/call":
            partes.append(f"name={params.get('name')}")
        elif msg.get("method") == "resources/read":
            partes.append(f"uri={params.get('uri')}")
        partes.append(f"traceparent={meta.get('traceparent')}")
        if params.get("requestState"):
            partes.append("retry=sim")
        return "mcp request " + " ".join(partes)
    except Exception:
        return "mcp request corpo=invalido"


class LogRequests:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") != "POST":
            return await self.app(scope, receive, send)
        partes: list[bytes] = []
        mais = True
        while mais:
            msg = await receive()
            if msg["type"] == "http.disconnect":
                return
            partes.append(msg.get("body", b""))
            mais = msg.get("more_body", False)
        corpo = b"".join(partes)
        log.info(_linha(corpo))
        entregue = False

        async def receive_replay():
            nonlocal entregue
            if not entregue:
                entregue = True
                return {"type": "http.request", "body": corpo, "more_body": False}
            return await receive()

        await self.app(scope, receive_replay, send)
