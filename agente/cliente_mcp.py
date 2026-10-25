"""Cliente MCP do agente: Client do SDK de longa duração, sem callback de elicitation.

O `input_required` chega cru a quem chamou (`allow_input_required=True`). O retry é feito pelo
chamador com `input_responses` e `request_state`; o SDK gera um id JSON-RPC novo a cada chamada
e espelha os headers `MCP-Protocol-Version`, `Mcp-Method` e `Mcp-Name`.
"""

import asyncio
import os
from typing import Any

from mcp import Client, MCPError, types
from mcp_types import CONNECTION_CLOSED
from mcp.client.session import ClientSession

from traceparent import novo_traceparent  # noqa: F401  (reexportado para quem usa este módulo)

REVISAO = "2026-07-28"
POLITICA_URI = "politica://uso"
CAPACIDADES = types.ClientCapabilities(
    elicitation=types.ElicitationCapability(form=types.FormElicitationCapability())
)


class _ClientForm(Client):
    """Client que anuncia só `elicitation.form`, sem registrar callback.

    O SDK só anuncia elicitation com `elicitation_callback` (e então `form` e `url`, respondendo
    sozinho). `Client._build_session` é privado, por isso a troca é feita na instância da sessão.
    """

    async def _build_session(self, exit_stack) -> ClientSession:
        sessao = await super()._build_session(exit_stack)
        sessao._build_capabilities = lambda _versao: CAPACIDADES
        return sessao


class ClienteMCP:
    """Um por processo. Abre o Client na primeira chamada e o reabre depois de uma falha de transporte.

    O `Client` usa um task group do anyio, que só pode ser fechado na task que o abriu. Como cada
    Task A2A roda numa task diferente, uma task "dona" abre o Client, espera o pedido de fechamento
    e o fecha. Uma falha de conexão derruba o transporte inteiro (a task dona termina); aí o Client
    é descartado e a próxima chamada abre outro.
    """

    def __init__(self, url: str | None = None, timeout: float = 30.0) -> None:
        self.url = url or os.environ.get("MCP_URL", "http://127.0.0.1:7301/mcp")
        self.timeout = timeout
        self._client: Client | None = None
        self._dona: asyncio.Task | None = None
        self._parar = asyncio.Event()
        self._trava = asyncio.Lock()
        self._ids_usados = 0
        self._listou: Client | None = None

    async def _manter_aberto(self, pronto: asyncio.Future, parar: asyncio.Event) -> None:
        client = _ClientForm(
            self.url,
            mode=REVISAO,
            cache=None,
            client_info=types.Implementation(name="agente-central-de-salas", version="1.0.0"),
        )
        try:
            async with client:
                # Um Client novo recomeça os ids em 1; continua depois do último id já usado.
                # `_next_id` é atributo privado do dispatcher.
                client.session._dispatcher._next_id = self._ids_usados
                pronto.set_result(client)
                await parar.wait()
        except BaseException as exc:
            if not pronto.done():
                pronto.set_exception(exc if isinstance(exc, Exception) else ConnectionError("cliente MCP cancelado"))
            if not isinstance(exc, Exception):
                raise

    async def _abrir(self) -> tuple[Client, asyncio.Task]:
        async with self._trava:
            if self._client is None or self._dona is None or self._dona.done():
                self._parar = asyncio.Event()
                pronto = asyncio.get_running_loop().create_future()
                self._dona = asyncio.create_task(self._manter_aberto(pronto, self._parar))
                self._client = await pronto
            return self._client, self._dona

    async def _descartar(self, client: Client) -> None:
        async with self._trava:
            if self._client is client:
                self._client = None
                self._parar.set()

    async def _sessao(self, trace: str, operacao):
        """Roda `operacao(sessao, meta)`; falha de transporte descarta o Client e vira ConnectionError."""
        client, dona = await self._abrir()
        self._ids_usados += 1
        chamada = asyncio.ensure_future(operacao(client.session, {"traceparent": trace}))
        try:
            await asyncio.wait({chamada, dona}, timeout=self.timeout, return_when=asyncio.FIRST_COMPLETED)
            if chamada.done():
                return chamada.result()
            chamada.cancel()
            await self._descartar(client)
            raise ConnectionError("servidor MCP indisponivel (conexao encerrada ou sem resposta)")
        except MCPError as erro:
            if erro.code == CONNECTION_CLOSED:
                await self._descartar(client)
            raise
        except BaseException:
            chamada.cancel()
            raise

    async def listar_tools(self, trace: str) -> list[types.Tool]:
        resultado = await self._sessao(trace, lambda s, meta: s.list_tools(params=types.PaginatedRequestParams(_meta=meta)))
        self._listou = self._client
        return resultado.tools

    async def ler_politica(self, trace: str) -> str:
        """Lê `politica://uso` e devolve a versão da primeira linha (`versao: ...`)."""
        resultado = await self._sessao(trace, lambda s, meta: s.read_resource(POLITICA_URI, meta=meta))
        texto = next((c.text for c in resultado.contents if hasattr(c, "text")), "")
        primeira = texto.splitlines()[0] if texto else ""
        if not primeira.lower().startswith("versao:"):
            raise ValueError("politica://uso sem a linha 'versao: ...'")
        return primeira.split(":", 1)[1].strip()

    async def chamar_tool(
        self,
        nome: str,
        args: dict[str, Any],
        trace: str,
        input_responses: dict[str, Any] | None = None,
        request_state: str | None = None,
    ) -> types.CallToolResult | types.InputRequiredResult:
        """Resultado cru: `CallToolResult` (complete) ou `InputRequiredResult` (input_required)."""
        if self._client is None or self._listou is not self._client:
            # Client novo (ex.: reaberto entre a pausa e o retry): para validar o outputSchema, o SDK
            # faria um tools/list sozinho, sem o traceparent (session.py, validate_tool_result).
            await self.listar_tools(trace)
        return await self._sessao(
            trace,
            lambda s, meta: s.call_tool(
                nome,
                args,
                meta=meta,
                input_responses=input_responses,
                request_state=request_state,
                allow_input_required=True,
            ),
        )

    async def fechar(self) -> None:
        if self._client is not None:
            await self._descartar(self._client)
        if self._dona is not None:
            await asyncio.gather(self._dona, return_exceptions=True)


def montar_arguments(tools: list[types.Tool], nome: str, valores: dict[str, Any]) -> dict[str, Any]:
    """Monta os `arguments` pelas `properties` do `inputSchema` descoberto no tools/list.

    Falha se a tool não existe ou se falta algum campo de `required`; ignora chaves fora do schema.
    """
    tool = next((t for t in tools if t.name == nome), None)
    if tool is None:
        raise LookupError(f"Tool inexistente no servidor MCP: {nome}")
    schema = tool.input_schema or {}
    props = schema.get("properties", {})
    faltando = [c for c in schema.get("required", []) if c not in valores]
    if faltando:
        raise ValueError(f"Campos obrigatorios ausentes: {', '.join(faltando)}")
    return {k: v for k, v in valores.items() if k in props}
