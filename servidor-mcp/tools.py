"""Tools do servidor MCP. O servidor.py registra a lista TOOLS, na ordem do wire 01.

Fase 1: só listar_salas funciona. As regras de domínio (fase 2) e o MRTR (fase 3) ainda não
existem, então as outras duas tools são stubs com o inputSchema final.
"""

from mcp.server.mcpserver.exceptions import ToolError

import dados_repo
from schemas import Disponibilidade, ListaDeSalas, ReservaOut, SalaOut


def listar_salas() -> ListaDeSalas:
    """Lista todas as salas com capacidade e recursos."""
    return ListaDeSalas(salas=[SalaOut(**s) for s in dados_repo.SALAS])


def consultar_disponibilidade(sala: str, inicio: str, fim: str) -> Disponibilidade:
    """Diz se uma sala esta livre no intervalo, e quais reservas conflitam."""
    raise ToolError("Nao implementado nesta fase")


def reservar_sala(sala: str, inicio: str, fim: str, responsavel: str) -> ReservaOut:
    """Reserva uma sala. Se o intervalo estiver ocupado, pergunta qual alternativa usar."""
    raise ToolError("Nao implementado nesta fase")


TOOLS = [listar_salas, consultar_disponibilidade, reservar_sala]
