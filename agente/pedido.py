"""Parser do texto fixo do pedido (sem LLM, só regra).

    reservar sala=<id> inicio=<iso8601> fim=<iso8601> responsavel=<nome>
    escolha=<sala id> | escolha=recusar      (continuação, com taskId)
"""

from dataclasses import dataclass

CAMPOS_RESERVA = ("sala", "inicio", "fim", "responsavel")


class PedidoInvalido(ValueError):
    """Texto fora do formato fixo. É erro de protocolo, não de domínio."""


@dataclass(frozen=True)
class Reserva:
    sala: str
    inicio: str
    fim: str
    responsavel: str


@dataclass(frozen=True)
class Escolha:
    valor: str  # id da sala ou "recusar"

    @property
    def recusa(self) -> bool:
        return self.valor == "recusar"


def _pares(tokens: list[str]) -> dict[str, str]:
    """Agrupa `k=v`; um token sem `=` continua o valor anterior (nome com espaço)."""
    pares: dict[str, str] = {}
    chave = None
    for token in tokens:
        if "=" in token:
            chave, valor = token.split("=", 1)
            if not chave or chave in pares:
                raise PedidoInvalido(f"Campo repetido ou vazio: '{chave}'")
            pares[chave] = valor
        elif chave is None:
            raise PedidoInvalido(f"Trecho fora do formato chave=valor: '{token}'")
        else:
            pares[chave] += " " + token
    return pares


def interpretar(texto: str) -> Reserva | Escolha:
    tokens = (texto or "").split()
    if not tokens:
        raise PedidoInvalido("Pedido vazio")
    if tokens[0] == "reservar":
        pares = _pares(tokens[1:])
        faltando = [c for c in CAMPOS_RESERVA if not pares.get(c)]
        extras = [c for c in pares if c not in CAMPOS_RESERVA]
        if faltando or extras:
            detalhe = []
            if faltando:
                detalhe.append("faltando: " + ", ".join(faltando))
            if extras:
                detalhe.append("campos desconhecidos: " + ", ".join(extras))
            raise PedidoInvalido("Pedido invalido (" + "; ".join(detalhe) + ")")
        return Reserva(**{c: pares[c] for c in CAMPOS_RESERVA})
    if tokens[0].startswith("escolha="):
        pares = _pares(tokens)
        if set(pares) != {"escolha"} or not pares["escolha"]:
            raise PedidoInvalido("Continuacao invalida: use escolha=<sala id> ou escolha=recusar")
        return Escolha(pares["escolha"])
    raise PedidoInvalido(
        "Pedido fora do formato. Use: reservar sala=<id> inicio=<iso8601> fim=<iso8601> responsavel=<nome>"
    )
