"""Tools do servidor MCP. O servidor.py registra a lista TOOLS, na ordem do wire 01.

As regras ficam em regras.py. Em conflito, reservar_sala usa o MRTR nativo do SDK: o resolver
escolha_de_sala devolve um Elicit com as alternativas, o SDK responde input_required com um
requestState selado (RequestStateSecurity, configurada em servidor.py) e, no retry, entrega a
resposta do cliente (accept, decline ou cancel) ao parâmetro `escolha`. O servidor não guarda
nada entre a pausa e o retry.
"""

from typing import Annotated, Literal

from mcp.server.mcpserver import AcceptedElicitation, Context, Elicit, ElicitationResult, Resolve
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types.version import is_version_at_least
from pydantic import Field, create_model

import dados_repo
import regras
from schemas import ConflitoOut, Disponibilidade, EscolhaDeSala, ListaDeSalas, ReservaOut, SalaOut

REVISAO_MRTR = "2026-07-28"


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


def escolha_de_sala(sala: str, inicio: str, fim: str, ctx: Context) -> Elicit[EscolhaDeSala] | None:
    """Resolver do MRTR. Roda antes do corpo da tool e de novo a cada retry.

    Sem conflito devolve None (a tool reserva a sala pedida). Com conflito, pergunta qual
    alternativa usar; sem alternativas, encerra com isError.
    """
    ini, fi = regras.validar(sala, inicio, fim)
    if not regras.conflitos(sala, ini, fi):
        return None
    alternativas = regras.alternativas(sala, ini, fi)
    if not alternativas:
        raise ToolError("Sem alternativas disponiveis no intervalo")
    if not _usa_input_required(ctx) and not ctx.session.can_send_request:
        # Cliente em revisão anterior à 2026-07-28 (ex.: MCP Inspector): o SDK tentaria um
        # elicitation/create síncrono, que o transporte stateless não tem como entregar.
        raise ToolError(
            f"A sala pedida esta ocupada nesse intervalo. Alternativas: {', '.join(alternativas)}. "
            f"Escolher pelo MRTR exige a revisao {REVISAO_MRTR} do MCP; este cliente usa {ctx.protocol_version}."
        )
    schema = create_model(
        "EscolhaDeSala",
        __base__=EscolhaDeSala,
        sala=(Literal[tuple(alternativas)], Field(description="Sala alternativa escolhida")),
    )
    return Elicit("A sala pedida esta ocupada nesse intervalo. Escolha uma alternativa.", schema)


def _usa_input_required(ctx: Context) -> bool:
    """Mesmo critério do SDK (resolve.py): input_required só a partir da revisão 2026-07-28."""
    return ctx.protocol_version is not None and is_version_at_least(ctx.protocol_version, REVISAO_MRTR)


def reservar_sala(
    sala: str,
    inicio: str,
    fim: str,
    responsavel: str,
    escolha: Annotated[ElicitationResult[EscolhaDeSala], Resolve(escolha_de_sala)],
) -> ReservaOut:
    """Reserva uma sala. Se o intervalo estiver ocupado, pergunta qual alternativa usar."""
    if not isinstance(escolha, AcceptedElicitation):
        # decline ou cancel: conclui sem reservar (não é erro).
        return ReservaOut(reservado=False, motivo="recusado")
    # data None: sem conflito, reserva a sala pedida; senão, a escolhida pelo cliente.
    destino = sala if escolha.data is None else escolha.data.sala
    r = regras.reservar_se_livre(destino, inicio, fim, responsavel)
    if r is None:
        # Corrida rara: a sala ficou ocupada entre a pergunta e a gravação.
        raise ToolError(f"A sala {destino} foi ocupada por outra reserva. Tente novamente")
    return ReservaOut(
        reserva=r["id"],
        sala=destino,
        inicio=inicio,
        fim=fim,
        responsavel=responsavel,
        politica=regras.versao_politica(),
    )


TOOLS = [listar_salas, consultar_disponibilidade, reservar_sala]
