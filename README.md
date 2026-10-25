# A Ponte: um agente A2A com MCP por dentro

## Como rodar

Pré-requisitos: Python 3.10 ou superior, o [uv](https://docs.astral.sh/uv/) e `curl` (para o teste manual). Os comandos abaixo partem da raiz do repositório clonado.

```bash
# 1. Dependências (versões travadas no uv.lock)
uv sync --frozen

# 2. Chave de integridade do requestState (mínimo 32 bytes), gravada no .env da raiz (ignorado pelo git).
#    Só o servidor MCP lê o .env; o .env-example lista as variáveis que ele aceita.
python3 -c "import secrets; print('REQUEST_STATE_SECRET=' + secrets.token_hex(32))" > .env
#    Alternativa sem arquivo (uma variável exportada tem precedência sobre o .env):
#    export REQUEST_STATE_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")

# 3. Terminal 1: servidor MCP (porta 7301, endpoint /mcp). O log de cada request sai no stderr.
uv run python servidor-mcp/servidor.py

# 4. Terminal 2: agente A2A (porta 7300). Não usa a chave: o requestState é opaco para ele.
uv run python agente/agente.py
```

```bash
# 5. Terminal 3: validador de conformidade (stdlib, Python >= 3.10), com os dois processos no ar
python3 validador/validar.py --agente http://localhost:7300 --mcp http://localhost:7301
```

**Reinicie os dois processos (Ctrl+C e subir de novo) antes de cada execução do validador e antes dos testes manuais abaixo.** As reservas ficam em memória, e o validador reserva a `sala-fusca` e a `sala-mirante` das 14h às 15h do dia 03/11/2026 (verificações 30 e 33). Sem reiniciar, o pedido do exemplo manual (`sala-garagem`, mesmo horário) não tem alternativa livre e a Task termina em `FAILED` com `Sem alternativas disponiveis no intervalo`.

A primeira linha do validador é o trace-id. Procure-o no stderr do servidor MCP: as linhas do agente (`tools/list`, `resources/read`, `tools/call`) trazem o mesmo trace-id.

Variáveis de ambiente, todas opcionais: `MCP_PORT` (padrão `7301`), `MCP_HOST` (padrão `127.0.0.1`), `AGENTE_PORT` (padrão `7300`), `AGENTE_HOST` (padrão `127.0.0.1`), `AGENTE_URL_PUBLICA` (padrão `http://AGENTE_HOST:AGENTE_PORT`, vai na URL do Agent Card) e `MCP_URL` (padrão `http://127.0.0.1:7301/mcp`, usada pelo agente). As do servidor MCP (`MCP_PORT`, `MCP_HOST` e `REQUEST_STATE_SECRET`) valem no shell ou no `.env`; as do agente (`AGENTE_*` e `MCP_URL`) vão no shell, porque o agente não lê o `.env`. Para outras portas, defina `MCP_PORT`, `AGENTE_PORT` e `MCP_URL` e passe as mesmas URLs ao validador.

### Teste manual com curl

Com os dois processos recém-iniciados (veja o aviso acima), em um terceiro terminal. O corpo do passo 1 é o de `exemplos/wire/08-a2a-send-message.json`.

```bash
a2a() {  # POST no agente; o corpo vem da entrada padrão
  curl -s -m 10 -X POST http://localhost:7300/a2a -H 'Content-Type: application/json' \
    -H 'traceparent: 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01' --data-binary @-
}
id_da_task() { python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["task"]["id"])'; }

# 1. Sala ocupada: a Task pausa em TASK_STATE_INPUT_REQUIRED, com "alternativas: sala-fusca, sala-mirante"
R=$(a2a <<'EOF'
{"jsonrpc":"2.0","id":1,"method":"SendMessage","params":{"message":{"messageId":"msg-m1","role":"ROLE_USER","parts":[{"text":"reservar sala=sala-garagem inicio=2026-11-03T14:00:00-03:00 fim=2026-11-03T15:00:00-03:00 responsavel=Marty"}]}}}
EOF
)
echo "$R" | python3 -m json.tool
TASK=$(echo "$R" | id_da_task)

# 2. Continuação com a escolha: a mesma Task termina em TASK_STATE_COMPLETED na sala-mirante
a2a <<EOF | python3 -m json.tool
{"jsonrpc":"2.0","id":2,"method":"SendMessage","params":{"message":{"messageId":"msg-m2","role":"ROLE_USER","parts":[{"text":"escolha=sala-mirante"}],"taskId":"$TASK"}}}
EOF

# 3. GetTask: estado COMPLETED e artifact "reserva" com o campo politica
a2a <<EOF | python3 -m json.tool
{"jsonrpc":"2.0","id":3,"method":"GetTask","params":{"id":"$TASK"}}
EOF
```

```bash
# 4. Recusa. A sala-mirante já foi reservada no passo 2, então restou só a sala-fusca ("alternativas: sala-fusca").
R=$(a2a <<'EOF'
{"jsonrpc":"2.0","id":4,"method":"SendMessage","params":{"message":{"messageId":"msg-m3","role":"ROLE_USER","parts":[{"text":"reservar sala=sala-garagem inicio=2026-11-03T14:00:00-03:00 fim=2026-11-03T15:00:00-03:00 responsavel=Marty"}]}}}
EOF
)
TASK=$(echo "$R" | id_da_task)
a2a <<EOF | python3 -m json.tool
{"jsonrpc":"2.0","id":5,"method":"SendMessage","params":{"message":{"messageId":"msg-m4","role":"ROLE_USER","parts":[{"text":"escolha=recusar"}],"taskId":"$TASK"}}}
EOF
# -> TASK_STATE_CANCELED

# 5. Sala inexistente: TASK_STATE_FAILED, com "Sala inexistente: sala-inexistente" no histórico
a2a <<'EOF' | python3 -m json.tool
{"jsonrpc":"2.0","id":6,"method":"SendMessage","params":{"message":{"messageId":"msg-m5","role":"ROLE_USER","parts":[{"text":"reservar sala=sala-inexistente inicio=2026-11-03T14:00:00-03:00 fim=2026-11-03T15:00:00-03:00 responsavel=Marty"}]}}}
EOF
```

No stderr do servidor MCP, cada Task do agente aparece como `tools/list`, `resources/read`, `tools/call` (a pausa) e, depois da continuação, `tools/call ... retry=sim`. Os dois `tools/call` de uma reserva pausada têm `id` diferentes, e todas as linhas da Task têm o mesmo trace-id (`4bf92f3577b34da6a3ce929d0e0e4736`, o do header `traceparent` do curl).

### Teste direto no servidor MCP (passos 11 a 13 do fluxo do avaliador)

**Reinicie o servidor MCP antes do passo 11**, mesmo que ele tenha acabado de ser reiniciado para os testes acima. O passo 12 reinicia o servidor de novo, e o restart apaga as reservas em memória. O retry só conclui se as alternativas recalculadas depois do restart forem as mesmas da pausa, porque o SDK sela a pergunta mostrada e, se ela mudou, pergunta de novo (veja "Retry depois de um restart" em Decisões técnicas). Depois do teste com curl acima, por exemplo, a `sala-mirante` está reservada das 14h às 15h: a pausa ofereceria só a `sala-fusca`, e o restart traria a `sala-mirante` de volta à lista.

O retry é o corpo do wire 04 trocando só o `requestState`. A chave do `inputRequests` que o servidor devolve é a mesma do wire (`__main__:escolha_de_sala`); um retry com outra chave não seria reconhecido, e o SDK perguntaria de novo.

```bash
mcp_post() {  # POST no servidor MCP com os headers espelhados; corpo pela entrada padrão, status HTTP no stderr
  curl -s -m 10 -X POST http://localhost:7301/mcp -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' -H 'MCP-Protocol-Version: 2026-07-28' \
    -H 'Mcp-Method: tools/call' -H 'Mcp-Name: reservar_sala' -w '%{stderr}HTTP %{http_code}\n' --data-binary @-
}
corpo_do_wire() { python3 -c 'import json,sys; print(json.dumps(json.load(open(sys.argv[1]))["request"]["body"]))' "$1"; }
retry() {  # corpo do wire 04 (id 4, accept da sala-fusca) com o requestState $1
  python3 -c 'import json,sys; c=json.load(open("exemplos/wire/04-tools-call-retry.json"))["request"]["body"]; c["params"]["requestState"]=sys.argv[1]; print(json.dumps(c))' "$1"
}

# 11. Conflito com o corpo do wire 03: input_required, com o requestState
R=$(corpo_do_wire exemplos/wire/03-tools-call-conflito-input-required.json | mcp_post)
printf '%s\n' "$R" | python3 -m json.tool
ESTADO=$(printf '%s' "$R" | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["requestState"])')
#     Retry com um caractere trocado no requestState: -32602, HTTP 400
ADULTERADO=$(python3 -c 'import sys; s=sys.argv[1]; i=len(s)//2; print(s[:i] + ("A" if s[i] != "A" else "B") + s[i+1:])' "$ESTADO")
retry "$ADULTERADO" | mcp_post | python3 -m json.tool
```

Agora reinicie o servidor MCP (Ctrl+C no terminal 1 e `uv run python servidor-mcp/servidor.py` de novo, com o mesmo `.env`) e continue no mesmo terminal:

```bash
# 12. Retry com o requestState intacto, depois do restart: resultType "complete", reserva na sala-fusca
retry "$ESTADO" | mcp_post | python3 -m json.tool

# 13. O mesmo conflito, de um cliente sem elicitation nas capabilities (corpo do wire 06): -32021, HTTP 400
corpo_do_wire exemplos/wire/06-erro-32021-sem-elicitation.json | mcp_post | python3 -m json.tool
```

No stderr do servidor MCP, o retry aparece como `tools/call id=4 ... retry=sim`, e a pausa como `tools/call id=3`.

## Onde a ponte acontece

**(a) O `input_required` vira `TASK_STATE_INPUT_REQUIRED`:** [`agente/ponte.py:100`](agente/ponte.py#L100), em `_pausar` (definida em [`agente/ponte.py:78`](agente/ponte.py#L78)). O agente recebe o `InputRequiredResult` cru do `tools/call` (sem callback de elicitation), lê a única chave de `inputRequests` e as alternativas de `requestedSchema.properties.sala`, guarda tudo em `Task.pausa` (`agente/tarefas.py`) e muda a Task para INPUT_REQUIRED com a mensagem `alternativas: id1, id2`.

**(b) O `requestState` volta ao servidor:** [`agente/ponte.py:122`](agente/ponte.py#L122) (`request_state=pausa.request_state`), no `tools/call` de `retomar` que começa na [linha 117](agente/ponte.py#L117). Com `escolha=<id>` ou `escolha=recusar`, o agente repete o `tools/call` com um id JSON-RPC novo, os mesmos `arguments`, `inputResponses` com a mesma chave e o `requestState` guardado, sem modificação. O resultado passa pela mesma função (`_tratar`) da primeira chamada.

O `requestState` é opaco: fica em `Task.pausa`, por Task (não há variável global de pedido pendente), e nunca é decodificado. `Task.para_json` tem campos explícitos, então a pausa não sai em nenhuma resposta A2A, nem em mensagens de erro.

## Decisões técnicas

**Como o `requestState` é protegido.** Uso o utilitário do próprio SDK (`mcp/server/request_state.py`), em `servidor-mcp/servidor.py`: `RequestStateSecurity(keys=[REQUEST_STATE_SECRET], ttl=600)`, passado explicitamente ao `MCPServer`. O SDK sela o estado com AES-256-GCM (chave derivada por HKDF do secret), no formato `v1.<base64url>`. O conteúdo selado inclui emissão e expiração, o método, o nome da tool, um digest dos `arguments`, a audiência (`central-de-salas`) e as respostas já dadas pelo resolver. O servidor não guarda nada entre a pausa e o retry, então um retry depois de reiniciar o processo funciona desde que o `REQUEST_STATE_SECRET` seja o mesmo. Sem o `request_state_security=` explícito, o SDK usaria uma chave efêmera por processo e esse retry falharia, mesmo com o validador passando. Estado adulterado ou expirado, outro secret e `arguments` diferentes dos selados voltam como `-32602` (`Invalid or expired requestState`); a rejeição dos `arguments` adulterados é o caminho que o enunciado aceita. O secret só vem do ambiente (ou do `.env`, ignorado pelo git), sem valor padrão, e o servidor recusa subir com menos de 32 bytes. O valor nunca é logado, e o log só marca `retry=sim`.

**Por quanto tempo vale.** 10 minutos (`ttl=600`), dentro da faixa de 5 a 30 minutos do enunciado. O SDK não impõe esse teto, então a faixa é uma regra nossa.

**Retry depois de um restart.** O `requestState` também sela o digest da pergunta mostrada na pausa. No retry, o resolver recalcula as alternativas e, se a pergunta não é mais a mesma, o SDK descarta a resposta e devolve um novo `input_required` com a lista atual, em vez de reservar a partir de uma lista que já não vale (`mcp/server/mcpserver/resolve.py`, em `_fulfil`):

```python
answer = res.answers.get(key)
# An answer counts only for the rendering recorded when it was asked; an answer to
# an unrecorded or differently-worded question re-asks instead of being consumed.
if answer is not None and res.asked.get(key) != q:
    logger.info("Discarding the answer for resolver %r: the question changed since it was asked", key)
    answer = None
```

Como as reservas ficam em memória, um restart apaga as que foram criadas depois da subida. Se uma delas ocupava uma alternativa quando a pausa foi emitida, a lista muda com o restart e o retry recebe a pergunta nova, não a reserva. Com o servidor recém-iniciado antes da pausa, a lista é a mesma depois do restart e o retry conclui, como no teste direto acima. O SDK tem outro caminho de MRTR que evitaria a pergunta nova: a própria tool devolve o `InputRequiredResult`, guarda no estado selado as alternativas oferecidas e lê a resposta em `ctx.input_responses`. Mantive o `Resolve` porque, com ele, o SDK cuida do `-32021`, da validação da resposta contra o schema e da pergunta nova quando a lista muda; no outro caminho, tudo isso seria código nosso. No agente, a pergunta nova vira uma nova pausa da Task, com a lista atual.

**Onde ficam as Tasks.** Na memória do processo do agente (`agente/tarefas.py`), uma estrutura por Task, com histórico, artifacts e, se estiver pausada, a `Pausa` com o `requestState` e as alternativas. Reiniciar o agente perde as Tasks; reiniciar o servidor MCP não perde a pausa, porque o estado da reserva está no `requestState`. As reservas do servidor MCP também ficam em memória, semeadas de `dados/`. A checagem de conflito e a gravação são atômicas sob uma trava (`servidor-mcp/regras.py`), porque o SDK roda tools síncronas em threads.

**MRTR nativo do SDK, no servidor.** Em conflito, `reservar_sala` usa um resolver (`escolha_de_sala`, em `servidor-mcp/servidor.py`) que devolve `Elicit` com até três alternativas, ligado à tool por `Annotated[ElicitationResult[...], Resolve(...)]`. O SDK responde `input_required` e verifica o retry. Não uso callback de elicitation do servidor para o cliente, porque o transporte stateless não tem canal de volta. `decline` e `cancel` concluem a chamada sem erro, com `reservado: false` e `motivo`. A chave do `inputRequests` é o `módulo:qualname` do resolver. Ele fica no `servidor.py`, junto das tools, porque esse arquivo roda como script: a chave sai `__main__:escolha_de_sala`, a mesma dos exemplos de wire, e os corpos de `exemplos/wire/` podem ser reenviados trocando só o `requestState`. Erros de domínio saem como `ToolError`, para a mensagem exata chegar ao cliente. A janela de uso é checada em `-03:00` (os horários são convertidos antes), com `inicio` e `fim` na mesma data local, e a sobreposição é semiaberta: reservas que só encostam não conflitam.

**Log do servidor MCP num wrapper ASGI.** Cada request vira uma linha no stderr (método, id, `name` ou `uri`, `traceparent` e `retry=sim`), feita por um wrapper ASGI na frente do app do SDK (`servidor-mcp/log_asgi.py`) e não pelo `mcp.middleware`, porque o middleware do SDK não vê os requests rejeitados antes do dispatch (`_meta` incompleto, headers espelhados divergentes). O wrapper lê o corpo do POST e o repassa intacto. A validação do `_meta` (`-32602`, HTTP 400) e dos headers espelhados (`-32020`) é a do próprio SDK.

**A2A à mão, sem SDK.** O lado A2A é um servidor JSON-RPC escrito com Starlette, sem `a2a-sdk`. Com o `a2a-sdk` 1.2.2 o card e o `SendMessage` saem na forma esperada, mas o SDK exige o header `A2A-Version: 1.0` (que o validador não envia) e devolve `GetTask` sem o embrulho `result.task` do exemplo de wire. Escrever à mão também dá controle total da máquina de estados. Só existem `SendMessage` e `GetTask`, sem streaming, push nem autenticação. A Task nasce em `SUBMITTED`, passa por `WORKING` e para em `COMPLETED`, `FAILED`, `CANCELED` ou `INPUT_REQUIRED`; o `SendMessage` é síncrono e devolve a Task no estado em que parou. Estado terminal é definitivo: um `SendMessage` com `taskId` de Task terminal devolve o erro JSON-RPC `-32004` (UnsupportedOperation) e a Task não muda; `taskId` inexistente devolve `-32001` (TaskNotFound). Texto fora do formato, `isError` da tool e falha de conexão com o MCP terminam em `FAILED`. A mensagem de `isError` vai sem o prefixo `Error executing tool ...` do SDK. Uma exceção inesperada vira `FAILED` com `Erro interno do agente`, e o detalhe vai só para o stderr.

**Cliente MCP do agente: SDK com API privada.** Aqui uso o SDK: `Client` com `session.call_tool(..., allow_input_required=True)` (`agente/cliente_mcp.py`), que devolve o `input_required` cru e deixa controlar `input_responses` e `request_state`, e gera um id novo a cada chamada. A limitação está em `mcp/client/session.py`, em `ClientSession._build_capabilities`:

```python
elicitation = (
    types.ElicitationCapability(form=types.FormElicitationCapability(), url=types.UrlElicitationCapability())
    if self._elicitation_callback is not _default_elicitation_callback
    else None
)
```

O SDK só anuncia `elicitation` quando há um `elicitation_callback`, e então anuncia `form` e `url` e responde a elicitation sozinho. O agente precisa declarar apenas `{"elicitation": {"form": {}}}` e ver o `input_required` sem resolvê-lo por callback. Sem o callback, o servidor responde `-32021` (testado). A solução é uma subclasse de `Client` cujo `_build_session` chama o `super()` e troca `_build_capabilities` na instância da sessão por uma função que devolve só `form`, sem reescrever o protocolo. O `-32021` some e o ciclo completa. Como isso depende de API privada (`Client._build_session`, `ClientSession._build_capabilities`), o `mcp` está travado em `2.3.0`; o plano B seria um cliente JSON-RPC fino.

O `Client` é único por processo e fica aberto numa task própria (o task group do anyio só pode ser fechado na task que o abriu, e cada Task A2A roda numa task diferente). Ele é aberto na primeira chamada, e o agente sobe mesmo sem o servidor MCP no ar. Uma queda de conexão inutiliza o `Client`: ele é descartado, a Task atual falha e a seguinte abre outro. O contador de ids do SDK é por `Client` e recomeçaria em 1, então o agente continua a contagem do último id usado (`dispatcher._next_id`, também privado), e o retry nunca reaproveita o id da pausa. Num `Client` recém-aberto, o agente faz `tools/list` antes do `tools/call`: sem isso, o SDK listaria as tools sozinho, sem o `traceparent`, para validar o `outputSchema`. O `Client` é usado sem cache (`cache=None`): `tools/list` e `resources/read` vão ao servidor a cada Task.

**A pausa e a retomada.** `escolha=recusar` faz o retry com `decline`, e a Task termina em `TASK_STATE_CANCELED`, não em `FAILED`: recusar é uma decisão do usuário, não um erro. Uma escolha fora das alternativas guardadas mantém a Task em INPUT_REQUIRED, repete a linha `alternativas: ...` e não chama o MCP (o servidor responderia `isError` a um `accept` fora do enum). O enunciado só define essa regra; usei a mesma para qualquer texto que não seja uma escolha numa Task pausada (`oi`, ou um novo `reservar ...`), para que um erro de digitação não destrua a reserva em andamento. Se o retry volta `input_required` de novo (as alternativas mudaram), o estado interno é trocado inteiro. Um erro no retry (`-32602` de estado expirado ou adulterado, `REQUEST_STATE_SECRET` diferente, queda de conexão) termina a Task em `FAILED` com mensagem descritiva, sem o `requestState`.

**Clientes MCP anteriores à revisão `2026-07-28` (ex.: MCP Inspector).** O SDK só responde `input_required` a partir da `2026-07-28`. Para revisões antigas ele tenta um `elicitation/create` síncrono do servidor para o cliente, que o transporte stateless não tem como entregar (`Cannot send 'elicitation/create': this transport context has no back-channel`). Nesse caso, e só nele, o resolver devolve um `isError` com as alternativas (`A sala pedida esta ocupada nesse intervalo. Alternativas: sala-fusca, sala-mirante. ...`), e o cliente pode reservar uma delas diretamente. Reservas livres, erros de política e "sem alternativas" funcionam igual em qualquer revisão. O ciclo de MRTR completo exige um cliente `2026-07-28`, como o agente e o validador.

**Stack e versões.** Python, gerenciado com `uv`. Versões travadas com `==` em `pyproject.toml` e `uv.lock`: `mcp==2.3.0` (SDK v2, revisão `2026-07-28`), `uvicorn==0.54.0`, `starlette==1.7.0` e `python-dotenv==1.2.4` (lê o `.env`). Nenhum pacote, nem as transitivas, foi publicado a menos de duas semanas da data do desenvolvimento (2026-10-24): o `pyproject.toml` declara `exclude-newer = "2026-10-10T00:00:00Z"` em `[tool.uv]`, e o corte fica gravado no `uv.lock`. Por isso o `starlette` ficou na 1.7.0, e não na 1.10.0 que o `mcp` resolveria. Não há LLM em nenhum ponto do caminho de execução: o agente interpreta o texto por regras fixas e é determinístico.

## Saída do validador

Última execução, num clone limpo (`uv sync --frozen`, `.env` gerado pelo README), com os dois processos recém-iniciados. Terminou com código de saída 0.

```
trace-id desta execucao: df0d0756b642ceb53398f3c6b8f835d4
procure esse valor no stderr do servidor MCP para conferir a propagacao do traceparent.

PASS 01 tools/list traz as tres tools
PASS 02 toda tool tem inputSchema de objeto
PASS 03 listar_salas devolve structuredContent e o mesmo JSON em texto
PASS 04 _meta sem protocolVersion devolve -32602 e HTTP 400
PASS 05 _meta sem clientCapabilities devolve -32602 e HTTP 400
PASS 06 tool inexistente e recusada, por -32602 ou por isError
PASS 07 resources/read de politica://uso devolve a politica
PASS 08 resources/read de URI inexistente devolve -32602
PASS 09 sala inexistente devolve isError com a mensagem exata
PASS 10 fora da janela devolve isError com a mensagem exata
PASS 11 duracao acima de 2h devolve isError com a mensagem exata
PASS 12 intervalo invertido devolve isError com a mensagem exata
PASS 13 conflito devolve input_required com inputRequests e requestState
PASS 14 a elicitation e form mode e oferece as alternativas na ordem certa
PASS 15 conflito sem a capability elicitation devolve -32021 e HTTP 400
PASS 16 retry com inputResponses e requestState conclui a reserva
PASS 17 requestState adulterado e rejeitado com -32602
PASS 18 argumentos adulterados no retry nao tomam efeito
PASS 19 recusa conclui sem reservar e sem isError
PASS 20 conflito sem alternativa possivel devolve isError com a mensagem exata

PASS 21 agent card responde 200 no well-known com JSON
PASS 22 o card declara a interface JSON-RPC com url e versao 1.0
PASS 23 o card declara a skill reservar-sala
PASS 24 SendMessage com sala livre conclui a Task
PASS 25 o artifact chama reserva e traz a versao da politica
PASS 26 GetTask devolve id, contextId e estado corrente
PASS 27 SendMessage com sala ocupada pausa a Task
PASS 28 a Task pausada lista as alternativas na ordem certa
PASS 29 escolha fora do enum mantem a Task pausada
PASS 30 a continuacao conclui a Task na sala escolhida
PASS 31 SendMessage em Task terminal e recusado
PASS 32 a recusa termina a Task em CANCELED
PASS 33 duas Tasks pausadas ao mesmo tempo concluem cada uma com a sua reserva
PASS 34 nenhuma resposta A2A carrega o requestState
PASS 35 sala inexistente termina a Task em FAILED com a mensagem da tool
PASS 36 o agente e deterministico: o mesmo pedido produz a mesma pausa

resumo: 36 passaram, 0 falharam, de 36 verificacoes
```
