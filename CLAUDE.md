# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

MBA challenge "A Ponte" (course on MCP and A2A). The full spec is [ENUNCIADO.md](ENUNCIADO.md), in Portuguese, and it is the source of truth. There are two **separate processes**: an MCP server (`servidor-mcp/`) that owns every domain rule, and an agent (`agente/`) that is an MCP client internally and an A2A v1.0 server externally. Both are Python, built on the MCP SDK `mcp==2.3.0` (spec revision `2026-07-28`) and managed with `uv`.

[README.md](README.md) is the delivery document (in Portuguese), and the grader follows only it: Como rodar, Onde a ponte acontece, Decisões técnicas and Saída do validador. Keep it in sync with the code (it cites line numbers in `agente/ponte.py`), and keep every command in it working from a clean clone.

**Never modify `dados/`, `validador/`, or `exemplos/`.** The grader runs the original validator. No LLM anywhere in the execution path: the agent parses fixed-format text by rule and is deterministic.

## Architecture: the bridge

```
A2A client ──SendMessage/GetTask──▶ agente (:7300) ──HTTP MCP──▶ servidor-mcp (:7301)
           ◀── Task states ───────                ◀── complete | input_required ──
```

```
servidor-mcp/
  servidor.py    MCPServer("central-de-salas"); reads REQUEST_STATE_SECRET (env or .env); registers the tools and politica://uso; wraps the app in the log
  tools.py       listar_salas, consultar_disponibilidade, reservar_sala and the MRTR resolver escolha_de_sala
  regras.py      validation (room, interval, window, duration, in that order), overlap, alternatives, reservar_se_livre (check + write under a lock)
  schemas.py     output models; class names become the outputSchema titles in tools/list (wire 01), so renaming one changes the wire
  dados_repo.py  loads dados/; RESERVAS is the in-memory store
  log_asgi.py    ASGI wrapper that logs every POST to stderr (method, id, name/uri, traceparent, retry=sim)
agente/
  agente.py      Starlette app: GET /.well-known/agent-card.json and POST /a2a; one ClienteMCP per process (lifespan)
  a2a.py         JSON-RPC dispatch (SendMessage, GetTask), error codes, one lock per Task; never leaves a Task in WORKING
  ponte.py       the bridge: tools/list → resources/read → tools/call; _tratar handles every tools/call result; _pausar and retomar
  tarefas.py     Task, Pausa (internal, never serialized), Store, ids, para_json (explicit public fields)
  pedido.py      parser for "reservar sala=... inicio=... fim=... responsavel=..." and "escolha=<sala>|recusar"
  card.py        Agent Card (URL from AGENTE_URL_PUBLICA, or AGENTE_HOST/AGENTE_PORT)
  cliente_mcp.py wrapper around the SDK Client (see "SDK quirks")
  traceparent.py keeps the incoming trace-id, new span-id per MCP request
```

## Running

```bash
uv sync --frozen
# Integrity key for requestState (>= 32 bytes). Read from the environment or from .env (git-ignored); an exported var wins.
python3 -c "import secrets; print('REQUEST_STATE_SECRET=' + secrets.token_hex(32))" > .env
uv run python servidor-mcp/servidor.py   # :7301, endpoint /mcp; refuses to start without the secret
uv run python agente/agente.py           # :7300
# Conformance validator (stdlib only): 36 checks, exit 0 only if all pass
python3 validador/validar.py --agente http://localhost:7300 --mcp http://localhost:7301
```

Env vars (all optional): `MCP_PORT`, `MCP_HOST`, `AGENTE_PORT`, `AGENTE_HOST`, `AGENTE_URL_PUBLICA`, `MCP_URL`. Never print or commit the `.env` contents.

## How to test

- The user often has their own servers on 7300/7301. Check first with `ss -ltnp | grep -E ':(7300|7301|7310|7311)\b'`. If they are taken, use 7311 for MCP and 7310 for the agent: `MCP_PORT=7311 .venv/bin/python servidor-mcp/servidor.py 2> <tmp>/mcp.log &` and `AGENTE_PORT=7310 MCP_URL=http://127.0.0.1:7311/mcp .venv/bin/python agente/agente.py 2> <tmp>/agente.log &`. Keep the PIDs.
- Readiness: poll `ss -ltn | grep -q '127.0.0.1:<port> '`. **Never `curl` GET `/mcp`**: it opens an SSE stream and hangs forever. Put `timeout` on every network command.
- Stop only the processes you started, by PID (`uv run` spawns a child python; kill that too). Never `pkill -f` or `killall`: the pattern matches your own shell and can kill the user's servers.
- Restart **both** processes before every validator run and before manual tests. Reservations are in memory, and the validator itself books `sala-fusca` and `sala-mirante` 14:00–15:00 (checks 30 and 33), so the wire-08 request run right after it fails with `Sem alternativas disponiveis no intervalo`.
- The validator has no single-check mode: checks 1–20 hit the MCP server and 21–36 hit the agent. To exercise only the MCP server, point `--agente` at an empty port.
- The validator's first line is the trace-id; `grep -a` it in the MCP stderr (`-a` because a log truncated while open gets NUL bytes). A Task that paused and resumed shows `tools/list` → `resources/read` → `tools/call` → `tools/call ... retry=sim`: different ids, same trace-id.
- Manual A2A tests: the "Teste manual com curl" block in the README runs as-is in bash and zsh.
- Direct MCP tests: replay the request body and headers of `exemplos/wire/*.json` (03 conflict → `input_required`, 04 retry, 06 no capability → `-32021`, 11 decline). Expected differences: the `inputRequests` key is `tools:escolha_de_sala` (the wire says `__main__:escolha_de_sala`), the `requestState` value, and reservation ids.
- The MCP Inspector speaks a revision older than `2026-07-28`. It works for `listar_salas`, `consultar_disponibilidade` and free reservations, but a conflict returns an `isError` that lists the alternatives instead of `input_required` (see "SDK quirks").

## Invariants the validator and the grader check

**MCP server** (owns all domain rules: window 08:00–20:00 at -03:00 with start and end on the same local date, max 2h, half-open overlap, alternatives):
- Tools `listar_salas`, `consultar_disponibilidade` and `reservar_sala`, all with `outputSchema`, returning `structuredContent` plus the same JSON as a text block. Resource `politica://uso` (`text/markdown`, contents of `dados/politica-de-uso.md`, first line `versao: ...`). Unknown tool → `-32602` or `isError`; unknown resource URI → `-32602`, never empty `contents`.
- Every request carries `_meta` with `io.modelcontextprotocol/protocolVersion` and `io.modelcontextprotocol/clientCapabilities`, or gets `-32602` with HTTP 400; mirror headers (`MCP-Protocol-Version`, `Mcp-Method`, `Mcp-Name`) must match or get `-32020`. The SDK enforces both.
- Conflict in `reservar_sala` → `resultType: "input_required"` with one `inputRequests` entry (`elicitation/create`, `mode: form`, flat schema with `sala` as enum, or const for a single option) and a `requestState`. Alternatives: rooms free in that interval with capacity ≥ the requested room's, excluding it, sorted by capacity then id, max 3. None → `isError` `Sem alternativas disponiveis no intervalo`.
- Client without `elicitation.form` on a conflict → `-32021`, HTTP 400, `data.requiredCapabilities`.
- `requestState` is sealed by the SDK with `REQUEST_STATE_SECRET`, TTL 600 s. The server stores nothing between pause and retry, and a retry works after a restart with the same secret. Tampered or expired state, another secret, or changed `arguments` → `-32602`. It is not single-use (the validator reuses it). `decline`/`cancel` → `complete` with `reservado: false` and `motivo: "recusado"`.

**Agent**:
- Per Task: `tools/list` (no hardcoded tool list; arguments built from the discovered `inputSchema`), then `politica://uso` (its version goes in the artifact's `politica`), then `tools/call`. Every MCP request propagates the trace-id of the incoming A2A `traceparent` header.
- Sees the raw `input_required` (no elicitation callback). The retry uses a **new JSON-RPC id**, the same `inputRequests` key, the original `arguments` and the `requestState` echoed unmodified. The `requestState` is opaque, stored per Task, and never appears in any A2A response or error message.
- Task states: SUBMITTED → WORKING → COMPLETED | FAILED | CANCELED | INPUT_REQUIRED. `SendMessage` is synchronous and returns `result: {task: ...}`. Terminal states are final: `SendMessage` to a terminal Task → `-32004`; unknown Task → `-32001`.
- Pause message is exactly `alternativas: id1, id2` (enum order). A choice outside the stored alternatives, or any other text on a paused Task, keeps it INPUT_REQUIRED, repeats the line and calls nothing. `escolha=recusar` → decline → CANCELED. Tool `isError` → FAILED with the exact tool message (SDK prefix stripped). A retry that returns `input_required` again is a new pause with the new list.
- Success → artifact `name: "reserva"` whose text part is the JSON `reserva, sala, inicio, fim, responsavel, politica`; status message `Reserva <id> confirmada na <sala>.`
- The card matches `exemplos/wire/07-a2a-agent-card.json` (`supportedInterfaces` with `protocolBinding: JSONRPC`, `protocolVersion: "1.0"`, skill `reservar-sala`).

Exact domain error messages (the validator compares text; the SDK prefix is accepted):
- `Sala inexistente: <id>`
- `Fora da janela de uso: a politica permite reservas entre 08:00 e 20:00`
- `Duracao acima do limite: a politica permite no maximo 2 horas`
- `Intervalo invalido: fim deve ser posterior a inicio`
- `Sem alternativas disponiveis no intervalo`

## SDK quirks (mcp 2.3.0) behind the code

- Domain errors use `ToolError(msg)`, which keeps the message; a plain `ValueError` becomes `Error executing tool X` without it.
- MRTR is the SDK's native mechanism: `escolha_de_sala` returns `Elicit`, bound with `Annotated[ElicitationResult[...], Resolve(escolha_de_sala)]`. Never a server→client elicitation callback: the stateless transport has no back-channel.
- `MCPServer` must get `request_state_security=RequestStateSecurity(keys=[secret], ttl=600)` explicitly. Without it the SDK uses an ephemeral per-process key, and a retry after a restart fails even though the validator passes.
- The `inputRequests` key is the resolver's `module:qualname`, hence `tools:escolha_de_sala`.
- An `accept` outside the enum becomes `isError` on the server, so the agent checks the choice against the stored alternatives before retrying.
- If the alternatives changed between pause and retry, the SDK asks again (a new `input_required`) instead of failing.
- Sync tools run in worker threads (`anyio.to_thread.run_sync`), hence the lock in `regras.reservar_se_livre`.
- Clients older than `2026-07-28` make the SDK fall back to a synchronous `elicitation/create`, which fails in stateless mode. The resolver detects this (`ctx.protocol_version`, `ctx.session.can_send_request`) and raises a `ToolError` listing the alternatives.
- `mcp.middleware` does not see requests rejected before dispatch, so logging lives in the ASGI wrapper.
- `agente/cliente_mcp.py` depends on private SDK APIs, which is why `mcp` is pinned:
  - `Client._build_session` is overridden to replace `ClientSession._build_capabilities`, announcing only `elicitation.form` without registering a callback (the SDK only announces elicitation with a callback, and then answers by itself).
  - `dispatcher._next_id` continues the id count when the `Client` is recreated after a connection failure, so a retry never reuses the pause's id.
  - The `Client` lives in an owner task (an anyio task group must be closed by the task that opened it) and opens lazily, so the agent starts without the MCP server up.
  - After a successful `tools/call` the SDK lists tools by itself, without `_meta`, to validate the `outputSchema`; `chamar_tool` lists first, with the Task's trace, on a fresh `Client`.
  - The fallback, if this breaks, is a thin JSON-RPC client with a process-wide id counter.
- The A2A side is written by hand: `a2a-sdk` 1.2.2 requires an `A2A-Version` header (the validator doesn't send it) and doesn't wrap `GetTask` in `result.task`.

## Dependencies

Pinned with `==` in `pyproject.toml` and locked in `uv.lock`. `[tool.uv] exclude-newer = "2026-10-10T00:00:00Z"` keeps every package, transitive ones included, at least two weeks older than the development date. To add one: pin it with `==`, run `uv lock`, and check its publish date.
