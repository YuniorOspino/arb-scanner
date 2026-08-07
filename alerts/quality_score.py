"""
Score de proyección / recomendación (base simple, enriquecible).

Enganche en el flujo:
  EM active → alerta_from_execution → clasificar_alerta (VALIDA)
    → enrich_alerta_quality()  ← acá se marca tipo=proyeccion|arbitraje
    → buffer → Telegram (formato distinto si es_proyeccion_alta)
    → launcher HTML

Hoy no hay modelo probabilístico externo: aproximamos "alta viabilidad" con
señales observables del propio arb:

  score 0–100 =
      ROI (hasta 40 pts)
    + estabilidad de mercado (hasta 35 pts)   ← reutiliza filtro_roi
    + liquidez / stakes ejecutables (hasta 25 pts)

Flags (env):
  QUALITY_SCORE_MIN     default 70
  PROYECCION_ROI_MIN    default 1.8  (margen mín. para recomendar)
  RECOMENDACION_ENABLED default 1    (0 = nunca marcar proyección)

Hooks futuros: edge real, consenso de casas, liquidez API, CLV, etc.
  → sumar en `quality_extra_points` / `extra_signals` sin romper el contrato.
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
    """ROI mínimo para marcar recomendación / proyección alta."""
    return float(os.getenv("PROYECCION_ROI_MIN", "1.8"))


def recomendaciones_enabled() -> bool:
    raw = (os.getenv("RECOMENDACION_ENABLED") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


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
    """0 = balanced, up to 1 = very skewed."""
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
    return max(0.0, min(1.0, 1.0 - ratio))


def score_components(alerta: dict) -> dict[str, float]:
    """Desglose legible (para Telegram 'por qué es atractiva')."""
    roi = _roi(alerta)
    # 5% ROI → 40 pts (cap)
    roi_pts = min(max(roi, 0.0) / 5.0, 1.0) * 40.0

    est = estabilidad_mercado(alerta)  # 0–10
    est_pts = (est / 10.0) * 35.0

    min_leg = _min_leg_stake(alerta)
    total = _total_stake(alerta)
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
    liq -= _stake_balance_penalty(alerta) * 8.0
    liq_pts = max(0.0, min(25.0, liq))

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


def pick_recommended_leg(alerta: dict) -> dict[str, Any] | None:
    """
    Pierna principal a ejecutar primero: mayor stake (más capital en juego),
    desempate por cuota más alta (mejor precio aparente).
    """
    casas = list(alerta.get("casas") or [])
    if not casas:
        return None

    def _key(c: dict) -> tuple[float, float]:
        try:
            stake = float(c.get("stake") or 0)
        except (TypeError, ValueError):
            stake = 0.0
        try:
            cuota = float(c.get("cuota") or 0)
        except (TypeError, ValueError):
            cuota = 0.0
        return (stake, cuota)

    ordered = sorted(casas, key=_key, reverse=True)
    best = dict(ordered[0])
    best["_rank"] = 1
    return best


def other_legs(alerta: dict, primary: dict | None) -> list[dict]:
    casas = list(alerta.get("casas") or [])
    if not primary or not casas:
        return casas
    p_book = str(primary.get("bookmaker") or primary.get("nombre") or "")
    p_sel = str(primary.get("seleccion") or primary.get("outcome") or "")
    try:
        p_stake = float(primary.get("stake") or 0)
    except (TypeError, ValueError):
        p_stake = 0.0
    out: list[dict] = []
    skipped = False
    for c in casas:
        try:
            c_stake = float(c.get("stake") or 0)
        except (TypeError, ValueError):
            c_stake = 0.0
        same = (
            not skipped
            and str(c.get("bookmaker") or c.get("nombre") or "") == p_book
            and str(c.get("seleccion") or c.get("outcome") or "") == p_sel
            and abs(c_stake - p_stake) < 1e-6
        )
        if same:
            skipped = True
            continue
        out.append(c)
    if not skipped and len(casas) > 1:
        return casas[1:]
    return out


def is_proyeccion_alta(alerta: dict) -> bool:
    if not recomendaciones_enabled():
        return False
    comps = score_components(alerta)
    return comps["score"] >= get_quality_score_min() and comps["roi"] >= get_proyeccion_roi_min()


def enrich_alerta_quality(alerta: dict) -> dict:
    """
    Mutates alerta with quality + recomendación fields. Safe to call multiple times.
    """
    comps = score_components(alerta)
    alerta["quality_score"] = comps["score"]
    alerta["quality_components"] = comps
    alta = (
        recomendaciones_enabled()
        and comps["score"] >= get_quality_score_min()
        and comps["roi"] >= get_proyeccion_roi_min()
    )
    alerta["es_proyeccion_alta"] = alta
    alerta["tipo"] = "proyeccion" if alta else "arbitraje"

    primary = pick_recommended_leg(alerta)
    alerta["recomendacion"] = primary
    if primary:
        alerta["recomendacion_resumen"] = {
            "casa": primary.get("nombre") or primary.get("bookmaker"),
            "partido": primary.get("partido") or alerta.get("partido"),
            "mercado": primary.get("mercado") or alerta.get("mercado"),
            "seleccion": primary.get("seleccion") or primary.get("outcome"),
            "stake": primary.get("stake"),
            "cuota": primary.get("cuota"),
            "bookmaker": primary.get("bookmaker"),
        }

    if alta:
        logger.info(
            "PROYECCION_ALTA id=%s score=%.1f roi=%.2f%% casa=%s sel=%s "
            "(min_score=%.0f min_roi=%.1f)",
            alerta.get("ejecucion"),
            comps["score"],
            comps["roi"],
            (primary or {}).get("nombre"),
            (primary or {}).get("seleccion"),
            get_quality_score_min(),
            get_proyeccion_roi_min(),
        )
    return alerta


def why_good_bullets(alerta: dict) -> list[str]:
    """Líneas cortas: por qué es atractiva (celular)."""
    comps = alerta.get("quality_components") or score_components(alerta)
    bullets: list[str] = []
    roi = comps.get("roi") or _roi(alerta)
    bullets.append(
        f"Margen / ROI combinado {roi:.2f}% (buena viabilidad si ejecutás a estas cuotas)"
    )
    est = comps.get("estabilidad") or 0
    if est >= 7:
        bullets.append("Mercado estable (1X2 / O-U común): menos chance de que vuele la cuota")
    elif est >= 4:
        bullets.append("Mercado aceptable; ejecutá YA la pierna recomendada")
    else:
        bullets.append("Mercado sensible: prioridad velocidad al confirmar")

    primary = alerta.get("recomendacion") or pick_recommended_leg(alerta)
    if primary:
        try:
            cuota = float(primary.get("cuota") or 0)
        except (TypeError, ValueError):
            cuota = 0.0
        casa = primary.get("nombre") or "la casa"
        if cuota >= 2.5:
            bullets.append(f"Precio atractivo en {casa} (cuota {cuota:.2f})")
        elif cuota >= 1.7:
            bullets.append(f"Cuota sólida en {casa} ({cuota:.2f}) con stake ejecutable")

    liq = comps.get("liquidez_pts") or 0
    if liq >= 15:
        bullets.append("Stakes en rango cómodo (tamaño OK para las casas)")
    elif liq >= 8:
        bullets.append("Stakes ejecutables; revisá mínimos de la casa")
    else:
        bullets.append("Stakes justos: confirmá mínimos/límites antes de pegar")

    if comps.get("extra_pts"):
        bullets.append(f"Señal extra +{comps['extra_pts']:.0f} pts (modelo/edge)")
    bullets.append(f"Score viabilidad {comps.get('score', 0):.0f}/100")
    return bullets


__all__ = [
    "compute_quality_score",
    "enrich_alerta_quality",
    "get_proyeccion_roi_min",
    "get_quality_score_min",
    "is_proyeccion_alta",
    "other_legs",
    "pick_recommended_leg",
    "recomendaciones_enabled",
    "score_components",
    "why_good_bullets",
]
