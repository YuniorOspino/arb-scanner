"""
Score de proyección estadística (base simple, enriquecible).

Hoy no hay modelo probabilístico externo: aproximamos "alta calidad" con
señales observables del propio arb:

  score 0–100 =
      ROI (hasta 40 pts)
    + estabilidad de mercado (hasta 35 pts)   ← reutiliza filtro_roi
    + liquidez / stakes ejecutables (hasta 25 pts)

Flags:
  es_proyeccion_alta  si score >= QUALITY_SCORE_MIN y ROI >= PROYECCION_ROI_MIN

Hooks futuros: edge real, consenso de casas, liquidez API, CLV, etc.
  → sumar en `extra_signals` sin romper el contrato.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from alerts.filtro_roi import _min_leg_stake, estabilidad_mercado

logger = logging.getLogger(__name__)


def get_quality_score_min() -> float:
    return float(os.getenv("QUALITY_SCORE_MIN", "70"))


def get_proyeccion_roi_min() -> float:
    """ROI mínimo adicional para marcar proyección alta (default 2.5%)."""
    return float(os.getenv("PROYECCION_ROI_MIN", "2.5"))


def _roi(alerta: dict) -> float:
    try:
        return float(alerta.get("roi") or 0)
    except (TypeError, ValueError):
        return 0.0


def _total_stake(alerta: dict) -> float:
    try:
        t = float(alerta.get("total_stake") or 0)
        if t > 0:
            return t
    except (TypeError, ValueError):
        pass
    casas = alerta.get("casas") or []
    s = 0.0
    for c in casas:
        try:
            s += float(c.get("stake") or 0)
        except (TypeError, ValueError):
            continue
    return s


def _stake_balance_penalty(alerta: dict) -> float:
    """
    0 = balanced, up to 1 = very skewed (harder to execute both legs).
    """
    stakes: list[float] = []
    for c in alerta.get("casas") or []:
        try:
            v = float(c.get("stake") or 0)
        except (TypeError, ValueError):
            continue
        if v > 0:
            stakes.append(v)
    if len(stakes) < 2:
        return 0.3
    lo, hi = min(stakes), max(stakes)
    if hi <= 0:
        return 1.0
    ratio = lo / hi
    # ratio 1.0 → 0 penalty; ratio 0.2 → high penalty
    return max(0.0, min(1.0, 1.0 - ratio))


def score_components(alerta: dict) -> dict[str, float]:
    """Desglose legible (para Telegram 'por qué es buena')."""
    roi = _roi(alerta)
    # 5% ROI → 40 pts (cap)
    roi_pts = min(max(roi, 0.0) / 5.0, 1.0) * 40.0

    est = estabilidad_mercado(alerta)  # 0–10
    est_pts = (est / 10.0) * 35.0

    min_leg = _min_leg_stake(alerta)
    total = _total_stake(alerta)
    # Liquidez: pierna mínima usable + total razonable
    liq = 0.0
    if min_leg is not None:
        if min_leg >= 20_000:
            liq += 12.0
        elif min_leg >= 10_000:
            liq += 9.0
        elif min_leg >= 5_000:
            liq += 5.0
    if total >= 50_000:
        liq += 8.0
    elif total >= 20_000:
        liq += 5.0
    elif total >= 10_000:
        liq += 3.0
    # Penalizar desbalance extremo
    liq -= _stake_balance_penalty(alerta) * 8.0
    liq_pts = max(0.0, min(25.0, liq))

    # Hook futuro: señales externas (edge modelo, CLV, etc.)
    extra = float(alerta.get("quality_extra_points") or 0.0)
    extra = max(0.0, min(20.0, extra))

    total_score = min(100.0, roi_pts + est_pts + liq_pts + extra)
    return {
        "roi": roi,
        "roi_pts": round(roi_pts, 1),
        "estabilidad": round(est, 1),
        "estabilidad_pts": round(est_pts, 1),
        "liquidez_pts": round(liq_pts, 1),
        "extra_pts": round(extra, 1),
        "score": round(total_score, 1),
    }


def compute_quality_score(alerta: dict) -> float:
    return float(score_components(alerta)["score"])


def is_proyeccion_alta(alerta: dict) -> bool:
    comps = score_components(alerta)
    score = comps["score"]
    roi = comps["roi"]
    ok = score >= get_quality_score_min() and roi >= get_proyeccion_roi_min()
    return ok


def enrich_alerta_quality(alerta: dict) -> dict:
    """
    Mutates alerta with quality fields. Safe to call multiple times.
    """
    comps = score_components(alerta)
    alerta["quality_score"] = comps["score"]
    alerta["quality_components"] = comps
    alta = (
        comps["score"] >= get_quality_score_min()
        and comps["roi"] >= get_proyeccion_roi_min()
    )
    alerta["es_proyeccion_alta"] = alta
    alerta["tipo"] = "proyeccion" if alta else "arbitraje"
    if alta:
        logger.info(
            "PROYECCION_ALTA id=%s score=%.1f roi=%.2f%% (min_score=%.0f min_roi=%.1f)",
            alerta.get("ejecucion"),
            comps["score"],
            comps["roi"],
            get_quality_score_min(),
            get_proyeccion_roi_min(),
        )
    return alerta


def why_good_bullets(alerta: dict) -> list[str]:
    """Líneas cortas para el mensaje Telegram."""
    comps = alerta.get("quality_components") or score_components(alerta)
    bullets: list[str] = []
    roi = comps.get("roi") or _roi(alerta)
    bullets.append(f"ROI de arbitraje {roi:.2f}% (margen bloqueada si ambas piernas salen)")
    est = comps.get("estabilidad") or 0
    if est >= 7:
        bullets.append("Mercado estable / fácil de ejecutar (1X2, BTTS u O/U común)")
    elif est >= 4:
        bullets.append("Mercado aceptable; ejecutá primero la pierna de mayor stake")
    else:
        bullets.append("Mercado más sensible: priorizá velocidad al confirmar")
    liq = comps.get("liquidez_pts") or 0
    if liq >= 15:
        bullets.append("Stakes en rango cómodo (liquidez / tamaño OK)")
    elif liq >= 8:
        bullets.append("Stakes ejecutables; revisá mínimos de cada casa")
    else:
        bullets.append("Stakes justos: confirmá mínimos y límites de la casa")
    if comps.get("extra_pts"):
        bullets.append(f"Señal extra +{comps['extra_pts']:.0f} pts (modelo/edge externo)")
    bullets.append(f"Score proyección {comps.get('score', 0):.0f}/100")
    return bullets


__all__ = [
    "compute_quality_score",
    "enrich_alerta_quality",
    "get_proyeccion_roi_min",
    "get_quality_score_min",
    "is_proyeccion_alta",
    "score_components",
    "why_good_bullets",
]
