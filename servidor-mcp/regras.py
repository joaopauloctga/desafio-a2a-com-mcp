"""Regras de domínio: validação de sala e política, sobreposição e criação de reservas.

A validação fica separada do corpo das tools para o resolver MRTR da fase 3 reaproveitá-la.
"""

import re
import threading
from datetime import datetime, time, timedelta, timezone

from mcp.server.mcpserver.exceptions import ToolError

import dados_repo

FUSO = timezone(timedelta(hours=-3))
ABERTURA = time(8, 0)
FECHAMENTO = time(20, 0)
DURACAO_MAXIMA = timedelta(hours=2)

MSG_INTERVALO = "Intervalo invalido: fim deve ser posterior a inicio"
MSG_JANELA = "Fora da janela de uso: a politica permite reservas entre 08:00 e 20:00"
MSG_DURACAO = "Duracao acima do limite: a politica permite no maximo 2 horas"

_TRAVA = threading.Lock()


def sala_existe(sala_id: str) -> bool:
    return any(s["id"] == sala_id for s in dados_repo.SALAS)


def parse_data(texto: str) -> datetime:
    """ISO 8601 com offset. Texto inválido ou sem offset vira 'Intervalo invalido'."""
    try:
        dt = datetime.fromisoformat(texto)
    except (ValueError, TypeError):
        raise ToolError(MSG_INTERVALO)
    if dt.tzinfo is None:
        raise ToolError(MSG_INTERVALO)
    return dt


def validar(sala: str, inicio: str, fim: str) -> tuple[datetime, datetime]:
    """Valida sala e política, nesta ordem. Levanta ToolError; devolve (inicio, fim) já com offset."""
    if not sala_existe(sala):
        raise ToolError(f"Sala inexistente: {sala}")
    ini, fi = parse_data(inicio), parse_data(fim)
    if fi <= ini:
        raise ToolError(MSG_INTERVALO)
    ini_l, fim_l = ini.astimezone(FUSO), fi.astimezone(FUSO)
    if ini_l.date() != fim_l.date() or ini_l.time() < ABERTURA or fim_l.time() > FECHAMENTO:
        raise ToolError(MSG_JANELA)
    if fi - ini > DURACAO_MAXIMA:
        raise ToolError(MSG_DURACAO)
    return ini, fi


def conflitos(sala: str, ini: datetime, fi: datetime) -> list[dict]:
    """Reservas da sala que se sobrepõem a [ini, fi). Intervalo semiaberto: encostar não conflita."""
    return [
        r
        for r in dados_repo.RESERVAS
        if r["sala"] == sala
        and ini < datetime.fromisoformat(r["fim"])
        and datetime.fromisoformat(r["inicio"]) < fi
    ]


def versao_politica() -> str:
    primeira = dados_repo.POLITICA.splitlines()[0]
    return re.sub(r"^versao:\s*", "", primeira).strip()


def proximo_id() -> str:
    maior = max((int(r["id"].split("-")[1]) for r in dados_repo.RESERVAS), default=0)
    return f"res-{maior + 1:04d}"


def reservar_se_livre(sala: str, inicio: str, fim: str, responsavel: str) -> dict | None:
    """Grava a reserva em memória (strings originais de inicio/fim) se o intervalo estiver livre.

    Devolve None se houver conflito. O SDK roda tools síncronas em threads, então a checagem
    e a gravação ficam sob a mesma trava: duas chamadas simultâneas não ocupam o mesmo horário.
    """
    with _TRAVA:
        if conflitos(sala, datetime.fromisoformat(inicio), datetime.fromisoformat(fim)):
            return None
        reserva = {"id": proximo_id(), "sala": sala, "inicio": inicio, "fim": fim, "responsavel": responsavel}
        dados_repo.RESERVAS.append(reserva)
        return reserva
