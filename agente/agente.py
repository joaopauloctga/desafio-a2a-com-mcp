"""Agente (esqueleto da fase 0): sobe um app vazio na porta AGENTE_PORT.

O cliente MCP (fase 4) e o servidor A2A (fases 5 e 6) chegam depois.
"""

import os

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

HOST = os.environ.get("AGENTE_HOST", "127.0.0.1")
PORT = int(os.environ.get("AGENTE_PORT", "7300"))
MCP_URL = os.environ.get("MCP_URL", "http://127.0.0.1:7301/mcp")


async def vivo(_request: Request) -> PlainTextResponse:
    return PlainTextResponse("agente (esqueleto)\n")


app = Starlette(routes=[Route("/", vivo)])

if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
