"""Carrega os dados de dados/ na memória, com caminho resolvido pela localização do arquivo."""

import json
from pathlib import Path

DADOS = Path(__file__).resolve().parent.parent / "dados"

SALAS: list[dict] = json.loads((DADOS / "salas.json").read_text(encoding="utf-8"))
# Cópia mutável: as reservas novas são acrescentadas aqui (regras.reservar_se_livre).
RESERVAS: list[dict] = json.loads((DADOS / "reservas.json").read_text(encoding="utf-8"))
POLITICA: str = (DADOS / "politica-de-uso.md").read_text(encoding="utf-8")
