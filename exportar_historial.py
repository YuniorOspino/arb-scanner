"""
Exporta alert_history (alertas enviadas/descartadas) a Excel.

Uso:
  python exportar_historial.py
  python exportar_historial.py --desde 2026-08-01 --hasta 2026-08-31
  python exportar_historial.py --db data/arb_scanner.db --out historial.xlsx

Filtros de fecha en UTC (YYYY-MM-DD). Sin filtros = todo el historial.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font

from config import DB_PATH, get_config


def _parse_day(raw: str | None, *, end: bool = False) -> str | None:
    if not raw:
        return None
    day = datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if end:
        day = day.replace(hour=23, minute=59, second=59)
    return day.isoformat()


def fetch_rows(
    db_path: Path,
    *,
    desde: str | None,
    hasta: str | None,
) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    sql = "SELECT * FROM alert_history WHERE 1=1"
    params: list[object] = []
    if desde:
        sql += " AND ts >= ?"
        params.append(desde)
    if hasta:
        sql += " AND ts <= ?"
        params.append(hasta)
    sql += " ORDER BY ts ASC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return rows


def export_xlsx(rows: list[sqlite3.Row], out_path: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "historial"
    headers = [
        "fecha_hora_utc",
        "execution_id",
        "partido",
        "mercado",
        "market_type",
        "casas",
        "selecciones",
        "cuotas",
        "stakes",
        "roi",
        "total_stake",
        "status",
        "discard_reason",
    ]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    for row in rows:
        casas = json.loads(row["casas_json"] or "[]")
        ws.append(
            [
                row["ts"],
                row["execution_id"],
                row["event_name"],
                row["market_label"],
                row["market_type"],
                " | ".join(str(c.get("nombre") or "") for c in casas),
                " | ".join(str(c.get("seleccion") or "") for c in casas),
                " | ".join(str(c.get("cuota") or "") for c in casas),
                " | ".join(str(c.get("stake") or "") for c in casas),
                row["roi"],
                row["total_stake"],
                row["status"],
                row["discard_reason"] or "",
            ]
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Exportar historial de alertas a Excel")
    parser.add_argument("--db", default="", help="Ruta SQLite (default: config DB_PATH)")
    parser.add_argument("--out", default="data/historial_alertas.xlsx")
    parser.add_argument("--desde", default="", help="YYYY-MM-DD UTC inclusive")
    parser.add_argument("--hasta", default="", help="YYYY-MM-DD UTC inclusive")
    args = parser.parse_args()

    cfg = get_config()
    db_path = Path(args.db) if args.db else Path(cfg.database_path or DB_PATH)
    if not db_path.is_absolute():
        db_path = Path(__file__).resolve().parent / db_path

    if not db_path.exists():
        print(f"ERROR: no existe la base {db_path}")
        return 1

    rows = fetch_rows(
        db_path,
        desde=_parse_day(args.desde or None),
        hasta=_parse_day(args.hasta or None, end=True),
    )
    out = export_xlsx(rows, Path(args.out))
    print(f"OK: {len(rows)} filas → {out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
