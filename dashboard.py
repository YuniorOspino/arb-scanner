"""Streamlit dashboards for arbitrage and value-bet history."""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

import streamlit as st

from config import get_config, setup_logging

logger = logging.getLogger(__name__)


def show_arbitrage_dashboard(db_path: str = "arb_scanner.db") -> None:
    """
    Muestra en Streamlit las oportunidades de arbitraje registradas.

    db_path: ruta de la base de datos SQLite
    """
    st.subheader("Arbitraje")
    st.caption(f"Fuente: `{db_path}`")

    path = Path(db_path)
    if not path.exists():
        st.warning(f"No existe la base de datos: {db_path}")
        st.info("Ejecuta el scanner o guarda oportunidades primero.")
        return

    try:
        rows = _load_arbitrage_history(path)
    except sqlite3.Error:
        logger.exception("Arbitrage dashboard failed to read %s", path)
        st.error("Error leyendo la base de datos. Revisa los logs.")
        return

    if not rows:
        st.info("No hay oportunidades de arbitraje registradas todavia.")
        return

    profits = [
        float(
            r["profit_percent"]
            if r["profit_percent"] is not None
            else r["margen"] or 0
        )
        for r in rows
    ]
    c1, c2, c3 = st.columns(3)
    c1.metric("Oportunidades", len(rows))
    c2.metric("Mejor profit %", f"{max(profits):.2f}")
    c3.metric("Profit promedio %", f"{(sum(profits) / len(profits)):.2f}")

    st.divider()
    table = [
        {
            "Fecha": r["fecha"],
            "Evento": r["evento"],
            "Casas": r["casas"],
            "Margen %": r["margen"],
            "Profit %": (
                r["profit_percent"]
                if r["profit_percent"] is not None
                else r["margen"]
            ),
        }
        for r in rows
    ]
    st.dataframe(table, use_container_width=True, hide_index=True)

    st.divider()
    for r in rows:
        evento = r["evento"] or "Evento desconocido"
        profit = (
            r["profit_percent"] if r["profit_percent"] is not None else r["margen"]
        )
        with st.expander(f"{evento}  |  {profit}%  |  {r['fecha']}"):
            st.write(f"**Casas:** {r['casas']}")
            st.write("**Cuotas:**")
            st.code(_pretty_json(r["cuotas"]))
            st.write(f"**Margen:** {r['margen']}%")
            if r["profit_percent"] is not None:
                st.write(f"**Profit:** {r['profit_percent']}%")
            st.write("**Stakes:**")
            st.code(_pretty_json(r["stakes"]))


def show_value_bet_dashboard(db_path: str = "arb_scanner.db") -> None:
    """
    Muestra en Streamlit las apuestas de valor registradas.

    db_path: ruta de la base de datos SQLite
    """
    st.subheader("Value Bets")
    st.caption(f"Fuente: `{db_path}`")

    path = Path(db_path)
    if not path.exists():
        st.warning(f"No existe la base de datos: {db_path}")
        st.info("Guarda value bets primero con save_value_bet().")
        return

    try:
        rows = _load_value_bet_history(path)
    except sqlite3.Error:
        logger.exception("Value-bet dashboard failed to read %s", path)
        st.error("Error leyendo la base de datos. Revisa los logs.")
        return

    if not rows:
        st.info("No hay apuestas de valor registradas todavia.")
        return

    edges = [float(r["edge"] or 0) for r in rows]
    stakes = [float(r["stake"] or 0) for r in rows]
    c1, c2, c3 = st.columns(3)
    c1.metric("Value bets", len(rows))
    c2.metric("Mejor edge %", f"{max(edges):.2f}")
    c3.metric("Stake total", f"{sum(stakes):.2f}")

    st.divider()
    table = [
        {
            "Fecha": r["fecha"],
            "Evento": r["evento"] or "",
            "Casa": r["casa"] or "",
            "Cuota": r["cuota"],
            "Prob. mercado": r["prob_mercado"],
            "Prob. personal": r["prob_personal"],
            "Edge %": r["edge"],
            "Stake": r["stake"],
        }
        for r in rows
    ]
    st.dataframe(table, use_container_width=True, hide_index=True)

    st.divider()
    for r in rows:
        label = r["evento"] or f"Cuota {r['cuota']}"
        with st.expander(f"{label}  |  edge {r['edge']}%  |  {r['fecha']}"):
            if r["casa"]:
                st.write(f"**Casa:** {r['casa']}")
            st.write(f"**Cuota:** {r['cuota']}")
            st.write(f"**Prob. implicita:** {r['prob_implicita']}")
            st.write(f"**Prob. mercado:** {r['prob_mercado']}")
            st.write(f"**Prob. personal:** {r['prob_personal']}")
            st.write(f"**Edge:** {r['edge']}%")
            if r["expected_value"] is not None:
                st.write(f"**EV:** {r['expected_value']}")
            st.write(f"**Stake recomendado:** {r['stake']}")
            st.write(f"**Fecha:** {r['fecha']}")


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    )
    return cur.fetchone() is not None


def _load_arbitrage_history(db_path: Path) -> list[sqlite3.Row]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        if not _table_exists(conn, "arbitrage_history"):
            logger.warning("Table arbitrage_history not found in %s", db_path)
            return []
        cur = conn.execute(
            """
            SELECT evento, casas, cuotas, margen, profit_percent, stakes, fecha
            FROM arbitrage_history
            ORDER BY fecha DESC
            """
        )
        return list(cur.fetchall())


def _load_value_bet_history(db_path: Path) -> list[sqlite3.Row]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        if not _table_exists(conn, "value_bet_history"):
            logger.warning("Table value_bet_history not found in %s", db_path)
            return []
        cur = conn.execute(
            """
            SELECT evento, casa, cuota, prob_implicita, prob_mercado,
                   prob_personal, edge, expected_value, stake, fecha
            FROM value_bet_history
            ORDER BY fecha DESC
            """
        )
        return list(cur.fetchall())


def _pretty_json(raw: str | None) -> str:
    if not raw:
        return "{}"
    try:
        return json.dumps(json.loads(raw), indent=2, ensure_ascii=False)
    except (TypeError, json.JSONDecodeError):
        return str(raw)


def main() -> None:
    setup_logging()
    st.set_page_config(
        page_title="arb-scanner",
        page_icon=None,
        layout="wide",
    )
    st.title("arb-scanner")

    cfg = get_config()
    default_history = Path("data/arb_scanner.db")
    db = default_history if default_history.exists() else cfg.database_path
    logger.info("Starting Streamlit dashboard with db=%s", db)

    tab_arb, tab_vb = st.tabs(["Arbitraje", "Value Bets"])
    with tab_arb:
        show_arbitrage_dashboard(str(db))
    with tab_vb:
        show_value_bet_dashboard(str(db))


if __name__ == "__main__":
    main()
