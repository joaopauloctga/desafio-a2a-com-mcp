"""Endpoint JSON-RPC 2.0 do A2A v1.0: `SendMessage` e `GetTask`."""

import json
import logging

from starlette.requests import Request
from starlette.responses import JSONResponse

import ponte
from pedido import Escolha, PedidoInvalido, Reserva, interpretar
from tarefas import INPUT_REQUIRED, SUBMITTED, WORKING, FAILED, Store, Task

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
TASK_NOT_FOUND = -32001
UNSUPPORTED_OPERATION = -32004  # A2A v1.0: operação não suportada (SendMessage em Task terminal)

store = Store()
log = logging.getLogger("agente")


class ErroRpc(Exception):
    def __init__(self, codigo: int, mensagem: str) -> None:
        self.codigo, self.mensagem = codigo, mensagem


def _erro(id_, codigo: int, mensagem: str) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": id_, "error": {"code": codigo, "message": mensagem}})


def _mensagem_valida(params) -> dict:
    mensagem = params.get("message") if isinstance(params, dict) else None
    if not isinstance(mensagem, dict):
        raise ErroRpc(INVALID_PARAMS, "params.message ausente ou invalido")
    partes = mensagem.get("parts")
    if not isinstance(partes, list) or not partes or not all(isinstance(p, dict) and isinstance(p.get("text"), str) for p in partes):
        raise ErroRpc(INVALID_PARAMS, "params.message.parts deve ter ao menos uma parte {text}")
    if "taskId" in mensagem and not isinstance(mensagem["taskId"], str):
        raise ErroRpc(INVALID_PARAMS, "params.message.taskId deve ser string")
    return mensagem


async def send_message(params, mcp, traceparent: str | None) -> dict:
    mensagem = _mensagem_valida(params)
    texto = " ".join(p["text"] for p in mensagem["parts"])
    task_id = mensagem.get("taskId")

    if task_id is None:
        task = store.criar()
        async with task.trava:
            task.historico.append(mensagem)
            await _processar(task, texto, mcp, traceparent, continuacao=False)
        return task.para_json()

    task = store.obter(task_id)
    if task is None:
        raise ErroRpc(TASK_NOT_FOUND, f"Task nao encontrada: {task_id}")
    async with task.trava:
        if task.terminal:
            raise ErroRpc(UNSUPPORTED_OPERATION, f"Task {task_id} ja terminou em {task.estado}")
        if task.estado != INPUT_REQUIRED:
            raise ErroRpc(UNSUPPORTED_OPERATION, f"Task {task_id} nao aguarda continuacao (estado {task.estado})")
        task.historico.append(mensagem)
        await _processar(task, texto, mcp, traceparent, continuacao=True)
    return task.para_json()


async def _processar(task: Task, texto: str, mcp, traceparent: str | None, continuacao: bool) -> None:
    task.mudar(WORKING)
    try:
        pedido = interpretar(texto)
    except PedidoInvalido as erro:
        if continuacao:  # Task pausada: texto fora do formato não a derruba, repete a pergunta
            await ponte.retomar(task, None, mcp, traceparent)
        else:
            task.mudar(FAILED, str(erro))
        return
    try:
        if continuacao:
            # Qualquer coisa que não seja uma escolha (ex.: um novo `reservar ...`) repete a pergunta.
            await ponte.retomar(task, pedido if isinstance(pedido, Escolha) else None, mcp, traceparent)
        elif isinstance(pedido, Reserva):
            await ponte.executar(task, pedido, mcp, traceparent)
        else:
            task.mudar(FAILED, "Pedido incompativel com o estado da Task: use reservar sala=... para abrir uma Task")
    except Exception:
        # Nenhuma Task fica presa em WORKING. O detalhe vai só para o stderr, nunca para a resposta A2A.
        log.exception("Task %s: erro inesperado", task.id)
        task.mudar(FAILED, "Erro interno do agente")


def get_task(params) -> dict:
    task_id = params.get("id") if isinstance(params, dict) else None
    if not isinstance(task_id, str) or not task_id:
        raise ErroRpc(INVALID_PARAMS, "params.id ausente ou invalido")
    task = store.obter(task_id)
    if task is None:
        raise ErroRpc(TASK_NOT_FOUND, f"Task nao encontrada: {task_id}")
    return task.para_json()


async def endpoint(request: Request) -> JSONResponse:
    try:
        corpo = json.loads(await request.body())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _erro(None, PARSE_ERROR, "JSON invalido")
    if not isinstance(corpo, dict) or corpo.get("jsonrpc") != "2.0" or not isinstance(corpo.get("method"), str):
        return _erro(corpo.get("id") if isinstance(corpo, dict) else None, INVALID_REQUEST, "Requisicao JSON-RPC invalida")
    id_, metodo, params = corpo.get("id"), corpo["method"], corpo.get("params")
    try:
        if metodo == "SendMessage":
            tarefa = await send_message(params, request.app.state.mcp, request.headers.get("traceparent"))
        elif metodo == "GetTask":
            tarefa = get_task(params)
        else:
            raise ErroRpc(METHOD_NOT_FOUND, f"Metodo nao suportado: {metodo}")
    except ErroRpc as erro:
        return _erro(id_, erro.codigo, erro.mensagem)
    return JSONResponse({"jsonrpc": "2.0", "id": id_, "result": {"task": tarefa}})
