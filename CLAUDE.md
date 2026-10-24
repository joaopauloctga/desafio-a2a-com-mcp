# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

MBA challenge "A Ponte" (course on MCP and A2A). The full spec is [ENUNCIADO.md](ENUNCIADO.md) (the original starter README, renamed), in Portuguese, and it is the source of truth. Build two **separate processes**: an MCP server (`servidor-mcp/`) and an agent (`agente/`). The agent is an MCP host/client internally and an A2A v1.0 server externally.

[README.md](README.md) is the delivery's documentation, and the grader follows only it. It has the four required sections: Como rodar, Onde a ponte acontece, Decisões técnicas and Saída do validador. Fill it in as each phase lands, without leaving it all for the end. The commands in it must work from a clean clone.

For a short summary of the deliverables and pitfalls (in Portuguese), see [RESUMO-DESAFIO.md](RESUMO-DESAFIO.md).

**Development plan:** [plan/INDEX.md](plan/INDEX.md) lists the phases (one detailed file per phase in `plan/`) and holds the progress status. Before starting work, check the index for the current phase. While working, tick task checkboxes in the phase file. When a phase starts or finishes, update the status table, the progress log, the decisions list and the validator scoreboard in the index.

**Never modify `dados/`, `validador/`, or `exemplos/`.** The grader runs the validator from the original repo.

## Commands

```bash
# Integrity key for requestState (required, >= 32 bytes, env only, never hardcoded or committed)
export REQUEST_STATE_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")

# Conformance validator (stdlib only, Python >= 3.10): 36 checks, exit 0 only if all pass
python3 validador/validar.py --agente http://localhost:7300 --mcp http://localhost:7301
```

Restart **both** processes before every validator run. Reservations are in memory, and leftovers from an earlier run cause false failures. The first line the validator prints is the trace-id; grep the MCP server's stderr for it. The validator has no single-check mode: checks 1–20 hit the MCP server directly and checks 21–36 hit the agent via A2A.

## Required stack and constraints

- MCP SDK v2, spec revision `2026-07-28`: either Python `mcp` (3.10+) or `@modelcontextprotocol/server` (Node 20+, ESM). The wire examples were captured with the Python SDK (FastMCP-style schemas, inputRequests key `__main__:escolha_de_sala`). Pin versions in `pyproject.toml` / `package.json`.
- A2A v1.0, JSON-RPC 2.0 binding over HTTP. Only `SendMessage` and `GetTask`. No streaming, push, or auth.
- No LLM anywhere in the execution path. The agent must be deterministic and parse fixed-format text by rule.
- Domain is deliberately trivial: in-memory reservations seeded from `dados/`. No ORM or DB.
- Ports and paths (env-overridable, these are the defaults): agent `7300`, with `/a2a` and `/.well-known/agent-card.json`; MCP `7301`, with `/mcp` (Streamable HTTP, stateless).

## Architecture: the bridge

```
A2A client ──SendMessage/GetTask──▶ agente (:7300) ──HTTP MCP──▶ servidor-mcp (:7301)
           ◀── Task states ───────                ◀── complete | input_required ──
```

**MCP server** (owns *all* domain rules: policy window 08:00–20:00 -03:00, max 2h, overlap, alternatives):
- Tools `listar_salas` (with `outputSchema`), `consultar_disponibilidade`, and `reservar_sala`. All of them return `structuredContent` plus a JSON text block. Resource `politica://uso` (`text/markdown`, content of `dados/politica-de-uso.md`; first line `versao: ...`).
- Every request must carry `_meta` with `io.modelcontextprotocol/protocolVersion` and `io.modelcontextprotocol/clientCapabilities`. If either is missing, return `-32602` with HTTP 400. Nothing gets inferred from earlier requests.
- Log method, id, and `_meta.traceparent` to **stderr** for every request.
- Conflict in `reservar_sala` → MRTR: `resultType: "input_required"` with one `inputRequests` entry (`elicitation/create`, `mode: form`, flat schema `sala` enum/const of alternatives) and a `requestState`. Alternatives: free rooms in that interval with capacity ≥ the requested room's, max 3, sorted by capacity asc then id asc. If there are none, return the `isError` "Sem alternativas disponiveis no intervalo".
- If the client didn't declare `elicitation.form`, return `-32021` with `data.requiredCapabilities` and HTTP 400.
- `requestState` is HMAC/AEAD-signed with `REQUEST_STATE_SECRET`, expires in 5–30 min, and carries everything needed to rebuild the request. The server stores nothing between the pause and the retry, and a retry must still work after a server restart. If it is tampered or expired, return `-32602`. Resent args on the retry must not take effect: the SDK's `RequestStateSecurity` binds a digest of the args and rejects a mismatch with `-32602`, which the spec accepts. `decline`/`cancel` → `complete`, not an error, `reservado: false` plus `motivo`.
- Use the SDK's native MRTR: a resolver returning `Elicit`, bound to the tool through `Annotated[ElicitationResult[T], Resolve(fn)]`, plus `RequestStateSecurity(keys=[secret])`. Always pass `request_state_security=` explicitly. Without it, `MCPServer` seals with an ephemeral per-process key, and a retry after a restart fails even though the validator still passes. Do **not** use a server→client elicitation callback: stateless transport has no back-channel. Details are in the notes of [plan/fase-00-setup.md](plan/fase-00-setup.md).

**Agent**:
- Calls `tools/list` before the first `tools/call` (no hardcoded tool list). Reads `politica://uso` and extracts the version, which goes in the artifact's `politica` field.
- Every MCP request: required `_meta` fields plus `clientCapabilities: {"elicitation": {"form": {}}}`, and the mirror headers `MCP-Protocol-Version` and `Mcp-Method` (plus `Mcp-Name` on `tools/call`/`resources/read`). A mismatch gets `-32020`.
- Propagate the trace-id from the incoming A2A `traceparent` header into `_meta.traceparent` of every MCP request for that Task. The span-id may change.
- Must see the raw `input_required`. Don't register an SDK elicitation callback that auto-answers. The retry must use a **new JSON-RPC id**, the same inputRequests key, and echo `requestState` unmodified.
- `requestState` is opaque to the agent: store it per Task, never parse it, and never expose it in any A2A response.

**Task state machine**: SUBMITTED → WORKING → COMPLETED | FAILED | CANCELED | INPUT_REQUIRED. Terminal states are final, and a `SendMessage` to a terminal Task returns an error.
- Input: `reservar sala=<id> inicio=<iso8601> fim=<iso8601> responsavel=<nome>`. Continuation: `escolha=<sala id>` or `escolha=recusar` (with `taskId`).
- Pause message text must be exactly `alternativas: id1, id2` (enum order, no prefix).
- An out-of-enum choice stays INPUT_REQUIRED and repeats the list. `recusar` → `decline` → CANCELED. Tool `isError` → FAILED, with the exact tool message in the history.
- Success → artifact `name: "reserva"`, text part = reservation JSON (`reserva, sala, inicio, fim, responsavel, politica`).
- The card shape must match `exemplos/wire/07-a2a-agent-card.json` (`supportedInterfaces` with `protocolBinding: JSONRPC`, `protocolVersion: "1.0"`, skill id `reservar-sala`).

## Exact error messages (validator compares text; SDK prefixes are OK)

- `Sala inexistente: <id>`
- `Fora da janela de uso: a politica permite reservas entre 08:00 e 20:00`
- `Duracao acima do limite: a politica permite no maximo 2 horas`
- `Intervalo invalido: fim deve ser posterior a inicio`
- `Sem alternativas disponiveis no intervalo`

Protocol errors: unknown tool → `-32602` (or `isError`); unknown resource URI → `-32602` (never empty `contents`).

`exemplos/wire/*.json` holds the exact request/response pairs the validator sends and expects. Consult them before implementing any endpoint.
