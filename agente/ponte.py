"""A ponte: uma Task A2A contra o servidor MCP.

Por Task: `tools/list` (descoberta e montagem dos argumentos), `resources/read` da política (versão
do artifact) e `tools/call`. `complete` vira COMPLETED com artifact; `isError`, FAILED com a
mensagem exata da tool. O `input_required` (pausa e retomada) é da fase 6: `_pausar` e `retomar`.
"""

import json

from mcp import types

from cliente_mcp import ClienteMCP, montar_arguments
from pedido import Escolha, Reserva
from tarefas import COMPLETED, FAILED, Task
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

    if isinstance(resultado, types.InputRequiredResult):
        _pausar(task, resultado, args)
        return
    if resultado.is_error:
        task.mudar(FAILED, _texto(resultado))
        return
    dados = _dados(resultado)
    if dados.get("reservado") is False:
        task.mudar(FAILED, f"Reserva nao realizada: {dados.get('motivo')}")
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


def _pausar(task: Task, resultado: types.InputRequiredResult, args: dict) -> None:
    """STUB da fase 6: guardar `Pausa` (requestState, chave, enum, args) e ir a INPUT_REQUIRED."""
    task.mudar(FAILED, "Pausa (input_required) ainda nao implementada")


async def retomar(task: Task, escolha: Escolha, mcp: ClienteMCP, traceparent: str | None) -> None:
    """STUB da fase 6: retry do tools/call com id novo, mesma chave e requestState devolvido."""
    task.mudar(FAILED, "Retomada ainda nao implementada")
