"""Servidor MCP (esqueleto da fase 0): sobe um app vazio na porta MCP_PORT.

As tools, o resource, a validação de _meta e o log chegam nas fases 1 a 3.
"""

import os

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

HOST = os.environ.get("MCP_HOST", "127.0.0.1")
PORT = int(os.environ.get("MCP_PORT", "7301"))


async def vivo(_request: Request) -> PlainTextResponse:
    return PlainTextResponse("servidor-mcp (esqueleto)\n")


app = Starlette(routes=[Route("/", vivo)])

if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
