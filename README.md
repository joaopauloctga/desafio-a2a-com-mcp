# A Ponte: um agente A2A com MCP por dentro

## Como rodar

Pré-requisitos: Python 3.10 ou superior e o [uv](https://docs.astral.sh/uv/).

```bash
# 1. Dependências (versões travadas no uv.lock)
uv sync --frozen

# 2. Chave de integridade do requestState (mínimo 32 bytes), gravada no .env da raiz (ignorado pelo git).
#    O .env-example lista as variáveis aceitas.
python3 -c "import secrets; print('REQUEST_STATE_SECRET=' + secrets.token_hex(32))" > .env
#    Alternativa sem arquivo (uma variável exportada tem precedência sobre o .env):
#    export REQUEST_STATE_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")

# 3. Servidor MCP (porta 7301, endpoint /mcp); lê a REQUEST_STATE_SECRET do ambiente ou do .env
uv run python servidor-mcp/servidor.py

# 4. Agente A2A (porta 7300), em outro terminal (não usa a chave: o requestState é opaco para ele)
uv run python agente/agente.py
```

```bash
# 5. Validador de conformidade (stdlib, Python >= 3.10), com os dois processos no ar
python3 validador/validar.py --agente http://localhost:7300 --mcp http://localhost:7301
```

Reinicie **os dois processos** antes de cada execução do validador: as reservas ficam em memória e sobras de uma execução anterior geram falhas falsas. A primeira linha do validador é o trace-id; procure-o no stderr do servidor MCP.

Variáveis de ambiente (todas opcionais, no shell ou no `.env`): `MCP_PORT` (padrão `7301`), `MCP_HOST` (padrão `127.0.0.1`), `AGENTE_PORT` (padrão `7300`) e `MCP_URL` (padrão `http://127.0.0.1:7301/mcp`, usada pelo agente).

## Onde a ponte acontece

## Decisões técnicas

**Stack.** Python, gerenciado com `uv`. Versões travadas com `==` em `pyproject.toml` e `uv.lock`: `mcp==2.3.0` (SDK v2, revisão `2026-07-28`), `uvicorn==0.54.0`, `starlette==1.7.0` e `python-dotenv==1.2.4` (lê o `.env`). Nenhum pacote, nem as dependências transitivas, foi publicado a menos de duas semanas da data do desenvolvimento (2026-10-24): o `pyproject.toml` declara `exclude-newer = "2026-10-10T00:00:00Z"` em `[tool.uv]`, e o corte fica gravado no `uv.lock`. Por isso o `starlette` ficou na 1.7.0, e não na 1.10.0 que o `mcp` resolveria.

**Log do servidor MCP num wrapper ASGI.** Cada request vira uma linha no stderr (método, id, `name` ou `uri`, `traceparent`, e `retry=sim` quando traz `requestState`, nunca o valor). O log fica num wrapper ASGI na frente do app do SDK, e não no `mcp.middleware`, porque o middleware do SDK não vê os requests rejeitados antes do dispatch (`_meta` incompleto, headers espelhados divergentes). O wrapper lê o corpo do POST e o repassa intacto. A validação do `_meta` (`-32602`, HTTP 400) e dos headers espelhados (`-32020`) é a do próprio SDK.

**Regras de domínio no servidor MCP.** A janela de uso é checada em `-03:00` (os horários são convertidos antes), com `inicio` e `fim` na mesma data local. A sobreposição é semiaberta: reservas que só encostam não conflitam. A validação fica em `servidor-mcp/regras.py`, usada pelas duas tools.

**A2A à mão.** O lado A2A é um servidor JSON-RPC escrito com Starlette, sem `a2a-sdk`. Com o `a2a-sdk` 1.2.2 o card e o `SendMessage` saem na forma esperada, mas o SDK exige o header `A2A-Version: 1.0` (que o validador não envia) e devolve `GetTask` sem o embrulho `result.task` do exemplo de wire. Escrever à mão também dá controle total da máquina de estados.

**Cliente MCP do agente: limitação do SDK.** O agente usa o `Client` do SDK com `session.call_tool(..., allow_input_required=True)`, que devolve o `input_required` cru e deixa controlar `input_responses` e `request_state`. O SDK gera um id novo a cada chamada. A limitação está em `mcp/client/session.py`, em `ClientSession._build_capabilities`:

```python
elicitation = (
    types.ElicitationCapability(form=types.FormElicitationCapability(), url=types.UrlElicitationCapability())
    if self._elicitation_callback is not _default_elicitation_callback
    else None
)
```

O SDK só anuncia `elicitation` quando há um `elicitation_callback`, e então anuncia `form` e `url` e responde a elicitation sozinho. Este agente precisa declarar apenas `{"elicitation": {"form": {}}}` e ver o `input_required` sem resolvê-lo por callback. Sem o callback, o servidor responde `-32021` (testado). A solução é sobrescrever `_build_capabilities` numa subclasse de `ClientSession` para devolver só `form`, sem reescrever o protocolo. Confirmado num experimento: o `-32021` some e o ciclo de pausa e retry completa.

**MRTR nativo do SDK.** Em conflito, `reservar_sala` usa um resolver (`escolha_de_sala`, em `servidor-mcp/tools.py`) que devolve `Elicit` com até três alternativas, ligado à tool por `Annotated[ElicitationResult[...], Resolve(...)]`. O SDK responde `input_required`, sela o `requestState` e o verifica no retry; o servidor não guarda nada entre a pausa e o retry. O selo usa `RequestStateSecurity(keys=[REQUEST_STATE_SECRET], ttl=600)`, com TTL de 10 minutos e a chave passada explicitamente (sem ela o SDK usaria uma chave efêmera por processo e o retry falharia depois de um restart). Estado adulterado ou expirado, e `arguments` diferentes dos selados, voltam como `-32602` por causa do digest dos argumentos. Com o mesmo secret, um retry feito depois de reiniciar o servidor funciona; com outro secret, é rejeitado.

**Clientes MCP anteriores à revisão `2026-07-28` (ex.: MCP Inspector).** O SDK só responde `input_required` a partir da `2026-07-28`. Para revisões antigas ele tenta um `elicitation/create` síncrono do servidor para o cliente, que o transporte stateless não tem como entregar (`Cannot send 'elicitation/create': this transport context has no back-channel`). Nesse caso, e só nele, o resolver devolve um `isError` com as alternativas (`A sala pedida esta ocupada nesse intervalo. Alternativas: sala-fusca, sala-mirante. ...`), e o cliente pode reservar uma delas diretamente. Reservas livres, erros de política e "sem alternativas" funcionam igual em qualquer revisão. O ciclo de MRTR completo exige um cliente `2026-07-28`, como o agente e o validador.

## Saída do validador
