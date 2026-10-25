"""A ponte: uma Task A2A contra o servidor MCP.

Por Task: `tools/list` (descoberta e montagem dos argumentos), `resources/read` da política (versão
do artifact) e `tools/call`. `complete` vira COMPLETED com artifact; `isError`, FAILED com a
mensagem exata da tool. O `input_required` vira a pausa da Task (`_pausar`) e `retomar` faz o retry
com id novo. Uma só função (`_tratar`) trata o resultado do `tools/call`, da primeira chamada e do retry.
"""

import json

from mcp import types

from cliente_mcp import ClienteMCP, montar_arguments
from pedido import Escolha, Reserva
from tarefas import CANCELED, COMPLETED, FAILED, INPUT_REQUIRED, Pausa, Task
from traceparent import novo_traceparent

TOOL = "reservar_sala"
PREFIXO_SDK = f"Error executing tool {TOOL}: "


def _texto(resultado: types.CallToolResult) -> str:
    texto = " ".join(c.text for c in resultado.content if isinstance(c, types.TextContent))
    return texto.removeprefix(PREFIXO_SDK)


def _dados(resultado: types.CallToolResult) -> dict:
    if resultado.structured_content:
        return dict(resultado.structured_content)
    return json.loads(_texto(resultado))


def linha_alternativas(opcoes: list[str]) -> str:
    return "alternativas: " + ", ".join(opcoes)


async def executar(task: Task, pedido: Reserva, mcp: ClienteMCP, traceparent: str | None) -> None:
    """Leva a Task do WORKING a um estado final (ou à pausa). Nunca propaga exceção do MCP."""

    def trace() -> str:
        return novo_traceparent(traceparent)  # mesmo trace-id, span-id novo a cada request

    try:
        tools = await mcp.listar_tools(trace())
        args = montar_arguments(tools, TOOL, pedido.__dict__)
        politica = await mcp.ler_politica(trace())
        resultado = await mcp.chamar_tool(TOOL, args, trace())
    except Exception as erro:  # MCPError, ConnectionError, LookupError, ValueError...
        task.mudar(FAILED, f"Falha ao falar com o servidor MCP: {erro}")
        return
    _tratar(task, resultado, args, politica, traceparent)


def _tratar(task: Task, resultado, args: dict, politica: str, traceparent: str | None) -> None:
    """Resultado do `tools/call` (primeira chamada ou retry) -> próximo estado da Task."""
    task.pausa = None
    if isinstance(resultado, types.InputRequiredResult):
        _pausar(task, resultado, args, politica, traceparent)
        return
    if resultado.is_error:
        task.mudar(FAILED, _texto(resultado))
        return
    dados = _dados(resultado)
    if dados.get("reservado") is False:  # decline/cancel na elicitation: complete, não é erro do MCP
        task.mudar(CANCELED, f"Reserva nao realizada: {dados.get('motivo')}")
        return
    task.artifact_reserva({
        "reserva": dados["reserva"],
        "sala": dados["sala"],
        "inicio": dados["inicio"],
        "fim": dados["fim"],
        "responsavel": dados["responsavel"],
        "politica": politica,
    })
    task.mudar(COMPLETED, f"Reserva {dados['reserva']} confirmada na {dados['sala']}.")


def _pausar(task: Task, resultado: types.InputRequiredResult, args: dict, politica: str, traceparent: str | None) -> None:
    """PONTE (a): o `input_required` do MCP vira TASK_STATE_INPUT_REQUIRED, com o requestState guardado."""
    pedidos = resultado.input_requests or {}
    if len(pedidos) != 1 or not resultado.request_state:
        task.mudar(FAILED, "Resposta input_required do servidor MCP fora do formato esperado")
        return
    chave, pedido = next(iter(pedidos.items()))
    try:
        propriedade = dict(pedido.params.requested_schema)["properties"]["sala"]
        opcoes = list(propriedade["enum"]) if "enum" in propriedade else [propriedade["const"]]
    except (AttributeError, KeyError, TypeError):
        task.mudar(FAILED, "Elicitation do servidor MCP sem alternativas de sala")
        return
    task.pausa = Pausa(
        request_state=resultado.request_state,  # opaco: só guardado e ecoado
        chave=chave,
        opcoes=opcoes,
        args=args,
        tool=TOOL,
        politica=politica,
        traceparent=traceparent,
    )
    task.mudar(INPUT_REQUIRED, linha_alternativas(opcoes))


async def retomar(task: Task, escolha: Escolha | None, mcp: ClienteMCP, traceparent: str | None) -> None:
    """Continuação de uma Task pausada. `escolha=None`: texto que não é escolha, repete a pergunta."""
    pausa = task.pausa
    if pausa is None:
        task.mudar(FAILED, "Task sem pausa pendente")
        return
    if escolha is None or (not escolha.recusa and escolha.valor not in pausa.opcoes):
        # Nenhuma chamada MCP: o servidor responderia isError a um accept fora do enum.
        task.mudar(INPUT_REQUIRED, linha_alternativas(pausa.opcoes))
        return
    resposta = {"action": "decline"} if escolha.recusa else {"action": "accept", "content": {"sala": escolha.valor}}
    entrada = traceparent or pausa.traceparent
    try:
        # PONTE (b): o requestState volta ao servidor, intacto, num tools/call de id JSON-RPC novo.
        resultado = await mcp.chamar_tool(
            pausa.tool,
            pausa.args,
            novo_traceparent(entrada),
            input_responses={pausa.chave: resposta},
            request_state=pausa.request_state,
        )
    except Exception as erro:  # nunca inclui o requestState
        task.pausa = None
        task.mudar(FAILED, f"Falha ao retomar a reserva no servidor MCP: {erro}")
        return
    _tratar(task, resultado, pausa.args, pausa.politica, entrada)
