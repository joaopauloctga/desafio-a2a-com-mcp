"""Servidor MCP da central de salas: monta o MCPServer, registra tools e resource e sobe o app.

As tools ficam em tools.py, os modelos de saída em schemas.py, a carga de dados/ em dados_repo.py
e o log de cada request em log_asgi.py. A validação do _meta e dos headers espelhados é do SDK.
"""

import os

import uvicorn
from mcp.server.mcpserver import MCPServer

import dados_repo
from log_asgi import LogRequests
from tools import TOOLS

HOST = os.environ.get("MCP_HOST", "127.0.0.1")
PORT = int(os.environ.get("MCP_PORT", "7301"))

mcp = MCPServer("central-de-salas", version="1.0.0")

for tool in TOOLS:
    mcp.add_tool(tool)


@mcp.resource("politica://uso", mime_type="text/markdown")
def politica_de_uso() -> str:
    """Politica de uso das salas."""
    return dados_repo.POLITICA


def criar_app():
    mcp_app = mcp.streamable_http_app(json_response=True, stateless_http=True)
    # O app do SDK serve /mcp e traz o próprio lifespan; o wrapper só observa o corpo.
    return LogRequests(mcp_app)


app = criar_app()

if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
