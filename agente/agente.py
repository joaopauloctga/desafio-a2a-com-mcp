"""Agente: servidor A2A v1.0 (JSON-RPC sobre HTTP) por fora, cliente MCP por dentro.

Rotas: `/.well-known/agent-card.json` e `/a2a`. Um `ClienteMCP` por processo.
"""

import os
from contextlib import asynccontextmanager

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from a2a import endpoint
from card import agent_card
from cliente_mcp import ClienteMCP

HOST = os.environ.get("AGENTE_HOST", "127.0.0.1")
PORT = int(os.environ.get("AGENTE_PORT", "7300"))


async def card(_request: Request) -> JSONResponse:
    return JSONResponse(agent_card())


@asynccontextmanager
async def ciclo_de_vida(app: Starlette):
    app.state.mcp = ClienteMCP()
    try:
        yield
    finally:
        await app.state.mcp.fechar()


app = Starlette(
    routes=[
        Route("/.well-known/agent-card.json", card),
        Route("/a2a", endpoint, methods=["POST"]),
    ],
    lifespan=ciclo_de_vida,
)

if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
