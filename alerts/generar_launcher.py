"""Genera la página launcher HTML para una alerta (tap → abrir pestañas)."""

from __future__ import annotations

import json
from pathlib import Path

PLANTILLA_PATH = Path(__file__).parent / "plantilla_launcher.html"


def generar_html(alerta: dict) -> str:
    plantilla = PLANTILLA_PATH.read_text(encoding="utf-8")

    casas = alerta.get("casas") or []
    casas_ordenadas = sorted(
        casas, key=lambda c: c.get("volatilidad", 0), reverse=True
    )

    html = plantilla
    html = html.replace("{{EJECUCION}}", str(alerta.get("ejecucion", "")))
    html = html.replace("{{ROI}}", str(alerta.get("roi", "")))
    html = html.replace("{{BENEFICIO}}", str(alerta.get("beneficio_esperado", "")))
    html = html.replace("{{PARTIDO}}", str(alerta.get("partido", "")))
    html = html.replace(
        "{{CASAS_JSON}}", json.dumps(casas_ordenadas, ensure_ascii=False)
    )

    return html
