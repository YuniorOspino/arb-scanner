"""
Rutas FastAPI del launcher — se montan en la app FastAPI de main.py.

No crea una segunda instancia FastAPI: exporta `router` + `guardar_alerta_activa`.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from alerts.generar_launcher import generar_html

router = APIRouter()

# Almacén en memoria: ejecucion_id -> alerta (vida corta).
ALERTAS_ACTIVAS: dict[int, dict] = {}

TTL_SEGUNDOS = 600


def guardar_alerta_activa(alerta: dict) -> None:
    """Llamar justo antes de mandar el botón a Telegram."""
    ejecucion_id = alerta.get("ejecucion")
    if ejecucion_id is None:
        return
    ALERTAS_ACTIVAS[int(ejecucion_id)] = alerta


@router.get("/alerta/{ejecucion_id}", response_class=HTMLResponse)
def servir_launcher(ejecucion_id: int):
    alerta = ALERTAS_ACTIVAS.get(ejecucion_id)
    if not alerta:
        raise HTTPException(
            status_code=404,
            detail="Alerta no encontrada o ya expiró.",
        )
    return generar_html(alerta)


@router.get("/status")
def status():
    return {
        "bot": "online",
        "arbitrage": "running",
        "alertas_activas": len(ALERTAS_ACTIVAS),
    }
