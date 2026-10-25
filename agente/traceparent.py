"""Traceparent do agente: mantém o trace-id de entrada e troca o span-id."""

import re
import secrets

_TRACEPARENT = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-[0-9a-f]{2}$")


def novo_traceparent(entrada: str | None = None) -> str:
    """Devolve `00-<trace-id>-<span-id novo>-01`; sem entrada válida, gera um trace-id novo."""
    m = _TRACEPARENT.match((entrada or "").strip().lower())
    trace_id = m.group(1) if m and set(m.group(1)) != {"0"} else secrets.token_hex(16)
    return f"00-{trace_id}-{secrets.token_hex(8)}-01"
