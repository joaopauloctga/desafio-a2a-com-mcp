"""Tools do servidor MCP. O servidor.py registra a lista TOOLS, na ordem do wire 01.

Fase 2: as três tools funcionam no caminho livre. As regras ficam em regras.py. Em conflito,
reservar_sala devolve um ToolError provisório; a fase 3 troca isso pelo MRTR.
"""

from mcp.server.mcpserver.exceptions import ToolError

import dados_repo
import regras
from schemas import ConflitoOut, Disponibilidade, ListaDeSalas, ReservaOut, SalaOut


def listar_salas() -> ListaDeSalas:
    """Lista todas as salas com capacidade e recursos."""
    return ListaDeSalas(salas=[SalaOut(**s) for s in dados_repo.SALAS])


def consultar_disponibilidade(sala: str, inicio: str, fim: str) -> Disponibilidade:
    """Diz se uma sala esta livre no intervalo, e quais reservas conflitam."""
    ini, fi = regras.validar(sala, inicio, fim)
    ocupadas = regras.conflitos(sala, ini, fi)
    return Disponibilidade(
        sala=sala, livre=not ocupadas, conflitos=[ConflitoOut(**r) for r in ocupadas]
    )


def reservar_sala(sala: str, inicio: str, fim: str, responsavel: str) -> ReservaOut:
    """Reserva uma sala. Se o intervalo estiver ocupado, pergunta qual alternativa usar."""
    regras.validar(sala, inicio, fim)
    r = regras.reservar_se_livre(sala, inicio, fim, responsavel)
    if r is None:
        # Provisório (fase 2): a fase 3 troca por MRTR com alternativas.
        raise ToolError("Sala ocupada no intervalo")
    return ReservaOut(
        reserva=r["id"],
        sala=sala,
        inicio=inicio,
        fim=fim,
        responsavel=responsavel,
        politica=regras.versao_politica(),
    )


TOOLS = [listar_salas, consultar_disponibilidade, reservar_sala]
