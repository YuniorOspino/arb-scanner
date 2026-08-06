<<<<<<< HEAD
# arb-scanner

Scanner de arbitraje deportivo: scrapea cuotas de varias casas, calcula oportunidades, las guarda en SQLite y alerta por Telegram.

## Estructura

```
arb-scanner/
├── scrapers/          # Un módulo por casa de apuestas
├── core/              # Modelos + cálculo de arbitraje + orquestación
├── alerts/            # Notificaciones Telegram
├── storage/           # Persistencia SQLite
├── config.py          # Umbrales, casas activas, intervalos
├── main.py            # Loop principal
├── dashboard.py       # Dashboard Streamlit
├── .env.example       # Plantilla de credenciales (nunca hardcodear)
└── requirements.txt
```

## Setup (venv)

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env
# Edita .env con TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID
```

## Configuración

En `config.py`:

| Campo | Default | Descripción |
|-------|---------|-------------|
| `scan_interval_seconds` | `60` | Segundos entre ciclos |
| `min_profit_percent` | `1.0` | Umbral mínimo de profit % |
| `max_stake_total` | `1000.0` | Stake total para sizing |
| `active_bookmakers` | bet365, draftkings, fanduel | Casas activas |

Variables de entorno (`.env`):

- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` — alertas
- `LOG_LEVEL` — `DEBUG` \| `INFO` \| `WARNING` \| `ERROR`
- `DATABASE_PATH` — ruta SQLite (default `data/opportunities.db`)

## Uso

Loop continuo (scrapers):

```bash
python main.py
```

Pipeline demo (arbitraje + value bet → SQLite + Telegram):

```bash
python run_pipeline.py
```

Dashboard Streamlit (tabs: arbitraje + value bets):

```bash
streamlit run dashboard.py
```

Los scrapers actuales devuelven **datos demo** para validar el pipeline. Sustituye `fetch_odds()` en cada módulo bajo `scrapers/` por integración real (API/HTTP).

## Logging

Niveles activos desde el arranque:

- `INFO` — ciclos, scrapers, arbs encontrados, alertas
- `DEBUG` — detalle de cuotas, fingerprints, agrupación
- `ERROR` — fallos de scraper, API Telegram, SQLite
=======
# arb-scanner
>>>>>>> 47f22b9bca3d748526f2ddce722b155437c6dfa1
