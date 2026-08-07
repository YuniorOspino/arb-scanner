"""
Plan diario controlado de generación de ingresos.

Arquitectura (v1):
  EM → alerta VALIDA → enrich quality
    → daily_plan.classify()     → arbitraje | conservadora | combinada
    → daily_plan.should_send()  → freno = DAILY_RISK_CAP (no la ganancia)
    → Telegram formato por tipo + progreso a la meta mínima
    → daily_plan.record_sent()

DAILY_PROFIT_TARGET = meta MÍNIMA (piso), no techo.
Tras cumplirla: seguir ARBITRAJES; reducir conservadora/combinada (formato).
Freno real: DAILY_RISK_CAP.

Principios: maximizar el día con control de riesgo; arb siempre primero.
Persistencia: data/daily_plan_YYYY-MM-DD.json
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from alerts.quality_score import enrich_alerta_quality, get_quality_score_min

try:
    from alerts.quality_score import pick_recommended_leg as _pick_recommended_leg
except ImportError:  # Railway / deploy parcial: no tumbar main.py
    _pick_recommended_leg = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


def pick_recommended_leg(alerta: dict) -> dict[str, Any] | None:
    """Pierna principal (mayor stake). Fallback local si quality_score viejo."""
    if _pick_recommended_leg is not None:
        return _pick_recommended_leg(alerta)
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

    best = dict(sorted(casas, key=_key, reverse=True)[0])
    best["_rank"] = 1
    return best

TIPO_ARBITRAJE = "arbitraje"
TIPO_CONSERVADORA = "conservadora"
TIPO_COMBINADA = "combinada"

_VALID_TIPOS = frozenset({TIPO_ARBITRAJE, TIPO_CONSERVADORA, TIPO_COMBINADA})


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def get_daily_profit_target() -> float:
    return max(0.0, _env_float("DAILY_PROFIT_TARGET", 10_000.0))


def get_daily_risk_cap() -> float:
    """0 = sin tope de riesgo diario."""
    return max(0.0, _env_float("DAILY_RISK_CAP", 150_000.0))


def get_max_conservative_per_day() -> int:
    return max(0, _env_int("MAX_CONSERVATIVE_ALERTS_PER_DAY", 8))


def get_max_combo_per_day() -> int:
    return max(0, _env_int("MAX_COMBO_ALERTS_PER_DAY", 3))


def get_combo_score_min() -> float:
    """Score mínimo para presentar como COMBINADA (más estricto)."""
    return _env_float("COMBO_SCORE_MIN", max(get_quality_score_min(), 80.0))


def get_conservative_odds_range() -> tuple[float, float]:
    lo = _env_float("CONSERVATIVE_ODDS_MIN", 1.45)
    hi = _env_float("CONSERVATIVE_ODDS_MAX", 2.40)
    if hi < lo:
        lo, hi = hi, lo
    return lo, hi


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _plan_path(day: date | None = None) -> Path:
    d = day or datetime.now(timezone.utc).astimezone().date()
    root = _project_root() / "data"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"daily_plan_{d.isoformat()}.json"


@dataclass
class DailyState:
    day: str
    profit_target: float = 10_000.0
    risk_cap: float = 150_000.0
    profit_estimated: float = 0.0
    risk_used: float = 0.0
    count_arbitraje: int = 0
    count_conservadora: int = 0
    count_combinada: int = 0
    sent_ids: list[int] = field(default_factory=list)

    def remaining_profit(self) -> float:
        return max(0.0, self.profit_target - self.profit_estimated)

    def progress_pct(self) -> float:
        if self.profit_target <= 0:
            return 100.0
        return min(100.0, 100.0 * self.profit_estimated / self.profit_target)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


_lock = threading.Lock()
_state: DailyState | None = None


def _today_local() -> date:
    return datetime.now(timezone.utc).astimezone().date()


def _load_state() -> DailyState:
    global _state
    today = _today_local().isoformat()
    if _state is not None and _state.day == today:
        _state.profit_target = get_daily_profit_target()
        _state.risk_cap = get_daily_risk_cap()
        return _state

    path = _plan_path()
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if str(raw.get("day")) == today:
                _state = DailyState(
                    day=today,
                    profit_target=get_daily_profit_target(),
                    risk_cap=get_daily_risk_cap(),
                    profit_estimated=float(raw.get("profit_estimated") or 0),
                    risk_used=float(raw.get("risk_used") or 0),
                    count_arbitraje=int(raw.get("count_arbitraje") or 0),
                    count_conservadora=int(raw.get("count_conservadora") or 0),
                    count_combinada=int(raw.get("count_combinada") or 0),
                    sent_ids=[int(x) for x in (raw.get("sent_ids") or []) if str(x).isdigit() or isinstance(x, int)],
                )
                return _state
        except Exception:
            logger.exception("No pude leer daily plan %s", path)

    _state = DailyState(
        day=today,
        profit_target=get_daily_profit_target(),
        risk_cap=get_daily_risk_cap(),
    )
    return _state


def _save_state(state: DailyState) -> None:
    path = _plan_path()
    try:
        path.write_text(
            json.dumps(state.as_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        logger.exception("No pude guardar daily plan %s", path)


def get_daily_state() -> DailyState:
    with _lock:
        return _load_state()


def _alerta_profit(alerta: dict) -> float:
    try:
        v = float(alerta.get("beneficio_esperado") or 0)
        if v > 0:
            return v
    except (TypeError, ValueError):
        pass
    try:
        roi = float(alerta.get("roi") or 0)
        stake = float(alerta.get("total_stake") or 0)
        if roi > 0 and stake > 0:
            return stake * roi / 100.0
    except (TypeError, ValueError):
        pass
    return 0.0


def _alerta_risk(alerta: dict) -> float:
    try:
        t = float(alerta.get("total_stake") or 0)
        if t > 0:
            return t
    except (TypeError, ValueError):
        pass
    s = 0.0
    for c in alerta.get("casas") or []:
        try:
            s += float(c.get("stake") or 0)
        except (TypeError, ValueError):
            continue
    return s


def _primary_cuota(alerta: dict) -> float:
    primary = alerta.get("recomendacion") or pick_recommended_leg(alerta)
    if not primary:
        return 0.0
    try:
        return float(primary.get("cuota") or 0)
    except (TypeError, ValueError):
        return 0.0


def classify_alerta(alerta: dict) -> str:
    """
    Clasifica tipo de alerta para formato + cuotas diarias.

    - combinada: proyección alta, 2–3 piernas, score muy alto
    - conservadora: proyección alta + cuota moderada
    - arbitraje: default (prioridad 1)
    """
    enrich_alerta_quality(alerta)
    casas = list(alerta.get("casas") or [])
    n = len(casas)
    try:
        score = float(alerta.get("quality_score") or 0)
    except (TypeError, ValueError):
        score = 0.0
    proy = bool(alerta.get("es_proyeccion_alta"))
    cuota = _primary_cuota(alerta)
    lo, hi = get_conservative_odds_range()

    if proy and 2 <= n <= 3 and score >= get_combo_score_min():
        return TIPO_COMBINADA
    if proy and lo <= cuota <= hi:
        return TIPO_CONSERVADORA
    return TIPO_ARBITRAJE


def should_send(alerta: dict, tipo: str | None = None) -> tuple[bool, str, str]:
    """
    Decide si enviar.

    Returns: (ok, tipo_final, motivo)

    - Freno real: DAILY_RISK_CAP (ganancia NUNCA corta el envío).
    - Meta (DAILY_PROFIT_TARGET) = piso, no techo.
    - Arb ejecutable → siempre si cabe en riesgo.
    - Tras meta mínima: conservadora/combinada se degradan a formato arb
      (menos spam de esos tipos; la oportunidad sigue avisándose).
    """
    enrich_alerta_quality(alerta)
    tipo_raw = tipo or classify_alerta(alerta)
    if tipo_raw not in _VALID_TIPOS:
        tipo_raw = TIPO_ARBITRAJE

    with _lock:
        st = _load_state()
        risk = _alerta_risk(alerta)
        profit = _alerta_profit(alerta)
        risk_cap = get_daily_risk_cap()
        meta_ok = st.profit_estimated >= st.profit_target > 0

        # Único freno duro: riesgo diario.
        if risk_cap > 0 and st.risk_used + risk > risk_cap + 1e-6:
            return (
                False,
                tipo_raw,
                f"DAILY_RISK_CAP ({st.risk_used:.0f}+{risk:.0f}>{risk_cap:.0f})",
            )

        tipo_final = tipo_raw
        motivo_extra = "ok_arbitraje"

        if tipo_final == TIPO_CONSERVADORA:
            max_c = get_max_conservative_per_day()
            if max_c <= 0 or st.count_conservadora >= max_c:
                tipo_final = TIPO_ARBITRAJE
                motivo_extra = "cupo_conservadora→arb"
            elif meta_ok:
                # Meta mínima ya cumplida → menos conservadoras; sigue como arb.
                tipo_final = TIPO_ARBITRAJE
                motivo_extra = "meta_min_ok→arb (sigue buscando)"
            else:
                motivo_extra = "ok_conservadora"
        elif tipo_final == TIPO_COMBINADA:
            max_k = get_max_combo_per_day()
            if max_k <= 0 or st.count_combinada >= max_k:
                tipo_final = TIPO_ARBITRAJE
                motivo_extra = "cupo_combo→arb"
            elif meta_ok:
                tipo_final = TIPO_ARBITRAJE
                motivo_extra = "meta_min_ok→arb (sigue buscando)"
            else:
                motivo_extra = "ok_combinada"
        else:
            motivo_extra = (
                "ok_arbitraje_post_meta" if meta_ok else "ok_arbitraje_prioridad"
            )

        logger.info(
            "DailyPlan decision=ENVIAR tipo=%s→%s profit+%.0f risk+%.0f "
            "meta_min=%.0f progreso=%.0f (%.0f%%) risk_used=%.0f/%.0f "
            "counts=a%d/c%d/k%d | %s",
            tipo_raw,
            tipo_final,
            profit,
            risk,
            st.profit_target,
            st.profit_estimated,
            st.progress_pct(),
            st.risk_used,
            risk_cap,
            st.count_arbitraje,
            st.count_conservadora,
            st.count_combinada,
            motivo_extra,
        )
        return True, tipo_final, motivo_extra


def record_sent(alerta: dict, tipo: str) -> DailyState:
    """Acumula progreso diario tras envío exitoso."""
    with _lock:
        st = _load_state()
        profit = _alerta_profit(alerta)
        risk = _alerta_risk(alerta)
        st.profit_estimated += max(0.0, profit)
        st.risk_used += max(0.0, risk)
        if tipo == TIPO_CONSERVADORA:
            st.count_conservadora += 1
        elif tipo == TIPO_COMBINADA:
            st.count_combinada += 1
        else:
            st.count_arbitraje += 1
        try:
            eid = int(alerta.get("ejecucion"))
            if eid not in st.sent_ids:
                st.sent_ids.append(eid)
        except (TypeError, ValueError):
            pass
        _save_state(st)
        logger.info(
            "DailyPlan recorded tipo=%s +profit=%.0f +risk=%.0f → "
            "est=%.0f/%.0f risk=%.0f/%.0f",
            tipo,
            profit,
            risk,
            st.profit_estimated,
            st.profit_target,
            st.risk_used,
            st.risk_cap,
        )
        return st


def apply_daily_plan(alerta: dict) -> tuple[bool, str, str]:
    """
    Enriquece alerta con campos del plan diario.
    Returns (should_send, tipo_final, motivo).
    """
    ok, tipo, motivo = should_send(alerta)
    alerta["tipo_plan"] = tipo
    alerta["tipo"] = tipo  # unifica con formatter
    st = get_daily_state()
    profit = _alerta_profit(alerta)
    alerta["daily_plan"] = {
        "target": st.profit_target,
        "profit_estimated": st.profit_estimated,
        "remaining": st.remaining_profit(),
        "progress_pct": round(st.progress_pct(), 1),
        "risk_used": st.risk_used,
        "risk_cap": st.risk_cap,
        "this_profit": profit,
        "this_risk": _alerta_risk(alerta),
        "counts": {
            "arbitraje": st.count_arbitraje,
            "conservadora": st.count_conservadora,
            "combinada": st.count_combinada,
        },
        "decision": "ENVIAR" if ok else "OMITIR",
        "motivo": motivo,
    }
    return ok, tipo, motivo


def progress_telegram_lines(alerta: dict | None = None) -> list[str]:
    """
    Progreso vs meta MÍNIMA (no techo).
    Deja claro que se sigue buscando si hay oportunidades y queda riesgo.
    """
    st = get_daily_state()
    dp = (alerta or {}).get("daily_plan") if alerta else {}
    target = float((dp or {}).get("target") or st.profit_target)
    est = float((dp or {}).get("profit_estimated") or st.profit_estimated)
    this_p = float((dp or {}).get("this_profit") or 0)
    risk_used = float((dp or {}).get("risk_used") or st.risk_used)
    risk_cap = float((dp or {}).get("risk_cap") or st.risk_cap)
    after = est + this_p
    pct = (100.0 * after / target) if target > 0 else 0.0
    meta_ok = after >= target > 0

    def _cop(n: float) -> str:
        return f"${n:,.0f}".replace(",", ".")

    lines = [
        f"Meta mínima: {_cop(target)}",
        f"Progreso: ~{_cop(after)} ({pct:.0f}% de la meta mín.)",
    ]
    if this_p > 0:
        lines.append(f"Esta alerta aporta ~{_cop(this_p)}")
    if meta_ok:
        lines.append("Meta mínima cumplida — se sigue buscando más si hay buenas opps")
    else:
        faltan = max(0.0, target - after)
        lines.append(f"Faltan ~{_cop(faltan)} para la meta mínima")
    if risk_cap > 0:
        lines.append(f"Riesgo usado: ~{_cop(risk_used)} / {_cop(risk_cap)} (freno real)")
    else:
        lines.append("Riesgo: sin tope diario configurado")
    return lines


__all__ = [
    "TIPO_ARBITRAJE",
    "TIPO_COMBINADA",
    "TIPO_CONSERVADORA",
    "apply_daily_plan",
    "classify_alerta",
    "get_daily_profit_target",
    "get_daily_risk_cap",
    "get_daily_state",
    "progress_telegram_lines",
    "record_sent",
    "should_send",
]
