"""Task A2A: modelo, store em memória, ids e montagem de mensagens e artifacts."""

import asyncio
import json
import secrets
from dataclasses import dataclass, field
from typing import Any

SUBMITTED = "TASK_STATE_SUBMITTED"
WORKING = "TASK_STATE_WORKING"
COMPLETED = "TASK_STATE_COMPLETED"
FAILED = "TASK_STATE_FAILED"
CANCELED = "TASK_STATE_CANCELED"
INPUT_REQUIRED = "TASK_STATE_INPUT_REQUIRED"
TERMINAIS = {COMPLETED, FAILED, CANCELED}


def novo_id(prefixo: str) -> str:
    return f"{prefixo}-{secrets.token_hex(6)}"


@dataclass
class Pausa:
    """Estado interno de uma Task em INPUT_REQUIRED (fase 6). Nunca é serializado para o A2A."""

    request_state: str  # opaco: guardado e ecoado, nunca decodificado
    chave: str  # única chave de inputRequests
    opcoes: list[str]  # alternativas, na ordem do enum
    args: dict[str, Any]  # arguments originais do tools/call
    tool: str
    politica: str  # versão já lida na primeira chamada
    traceparent: str | None  # traceparent de entrada da Task


@dataclass
class Task:
    id: str
    contexto: str
    estado: str = SUBMITTED
    mensagem_status: dict | None = None
    historico: list[dict] = field(default_factory=list)
    artifacts: list[dict] = field(default_factory=list)
    pausa: Pausa | None = None
    trava: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def terminal(self) -> bool:
        return self.estado in TERMINAIS

    def mensagem_agente(self, texto: str) -> dict:
        return {
            "messageId": novo_id("msg"),
            "role": "ROLE_AGENT",
            "parts": [{"text": texto}],
            "taskId": self.id,
            "contextId": self.contexto,
        }

    def mudar(self, estado: str, texto: str | None = None) -> None:
        """Muda o estado; com `texto`, cria a mensagem de status e a acrescenta ao history."""
        self.estado = estado
        if texto is not None:
            self.mensagem_status = self.mensagem_agente(texto)
            self.historico.append(self.mensagem_status)

    def artifact_reserva(self, dados: dict) -> None:
        self.artifacts.append(
            {"artifactId": novo_id("art"), "name": "reserva", "parts": [{"text": json.dumps(dados)}]}
        )

    def para_json(self) -> dict:
        status: dict = {"state": self.estado}
        if self.mensagem_status:
            status["message"] = self.mensagem_status
        return {
            "id": self.id,
            "contextId": self.contexto,
            "status": status,
            "history": list(self.historico),
            "artifacts": list(self.artifacts),
        }


class Store:
    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}

    def criar(self) -> Task:
        task = Task(id=novo_id("task"), contexto=novo_id("ctx"))
        self._tasks[task.id] = task
        return task

    def obter(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)
