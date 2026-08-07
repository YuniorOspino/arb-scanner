"""
Filtro de sensatez — separa arbitraje real de errores de cuota.

Cualquier alerta con ROI fuera de rango normal de arbitraje deportivo
se enruta a cola de revisión manual, NUNCA al flujo de notificación rápida.
"""

ROI_MIN = 0.5  # por debajo de esto no vale la pena por costos/fricción
ROI_MAX = 30  # por encima de esto, probable error de cuota
# (calibrado con ~300-400 alertas históricas: la mayoría
# de arbitrajes reales cae hasta ~30%; valores como 229%,
# 365%, 998% fueron confirmados como errores de cuota)


def clasificar_alerta(alerta: dict) -> str:
    """
    alerta: dict con al menos la clave 'roi' (float o string numérico).
    Devuelve una de: 'VALIDA', 'SOSPECHOSA_ERROR_CUOTA',
                      'DESCARTADA_BAJO_ROI', 'DESCARTADA_ROI_INVALIDO'
    """
    try:
        roi = float(alerta.get("roi"))
    except (TypeError, ValueError):
        return "DESCARTADA_ROI_INVALIDO"

    if roi > ROI_MAX:
        return "SOSPECHOSA_ERROR_CUOTA"

    if roi < ROI_MIN:
        return "DESCARTADA_BAJO_ROI"

    return "VALIDA"
