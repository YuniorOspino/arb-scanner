# Arbitrage Runtime Report

**Generated:** 2026-08-06T19:41:35Z  
**Scope:** Diagnosis only — no Telegram/algorithm fixes.

## Verdict

| Question | Answer |
|----------|--------|
| ¿Telegram de inicio funciona? | **SI** (`send_startup_message`) |
| ¿Llegan alertas de arbitraje después? | **NO** |
| ¿Hay datos REALES de casas? | **NO** (`any_real_book_data: false`) |
| ¿De dónde salía `Colombia vs Brasil \| 0.77%`? | **Mock** en scrapers CO cuando la API fallaba |

### Cadena causal (histórica, con mocks)

1. `requests.get(.../api/sport/football/fixtures)` → 404 o HTML (no JSON de fixtures).
2. Scrapers hacían fallback a `_mock_*_events()` con **Colombia vs Brasil**.
3. El motor calculaba un arb ~0.77% siempre igual.
4. Ciclo 1: `save_if_new=True` → `main.run_scan_cycle()` podía llamar `send_telegram_message()`.
5. Ciclos 2..N: mismo fingerprint → `save_if_new=False` → `ArbScanner.run_once()` **no retorna** la opp → **no se llama** al envío Telegram.

Eso explica: mensaje de arranque SI, alertas de ciclo NO (tras el primer mock), y el mismo partido eterno en logs/DB.

### Cadena causal (ahora, mocks eliminados)

1. APIs siguen sin devolver fixtures JSON.
2. Scrapers devuelven `[]` (mock removido).
3. `collect_quotes()` queda vacío → `run_once()` retorna `[]`.
4. `run_scan_cycle()` hace early-return → **send_telegram_message NO se llama**.

---

## Mock removal (producción)

Eliminado fallback mock de:

- `scrapers/betano.py`
- `scrapers/wplay.py`
- `scrapers/betplay.py`
- `scrapers/rushbet.py`
- `scrapers/zamba.py`

`codere.py` ya no usaba mock (devolvía `[]` en error).

Strings `Colombia vs Brasil` / `EquipoA vs EquipoB` ya no se inyectan en el flujo de scrape.

---

## HTTP probes (evidencia de “datos reales”)

| Casa | URL | HTTP | JSON fixtures | real_data |
|------|-----|------|---------------|-----------|
| Betano | `/api/sport/football/fixtures` | 404 | NO (HTML) | false |
| Wplay | idem | 200 | NO (HTML Playtech) | false |
| BetPlay | idem | 200 | NO (HTML SPA) | false |
| Codere | idem | 404 | NO | false |
| RushBet | idem | 404 | NO | false |
| Zamba | idem | 200 | NO (HTML SPA) | false |

**Conclusión:** el proceso consulta esas URLs cada ciclo, pero **ninguna entrega odds reales**. No hay scraping efectivo; solo shells HTML/404.

---

## SCAN #001 / #002 / #003

Contador ejecutado 3 veces consecutivas (mismo resultado = no hay “datos nuevos”, hay fallo estable de fuente).

### SCAN #001

```
CASAS CONSULTADAS
Betano:   partidos=0
Wplay:    partidos=0
BetPlay:  partidos=0
Codere:   partidos=0 (error 404)
RushBet:  partidos=0
Zamba:    partidos=0
Total partidos únicos: 0
Total mercados analizados: 0

ARBITRAJE
- oportunidades calculadas: 0
- oportunidades descartadas: 0
- motivo: no hay quotes
- >=0.5%: 0 | >=1.0%: 0 | >=1.5%: 0

TELEGRAM
- send_telegram_alert()/send_telegram_message(): NO
- bloqueo: opportunities == [] → early return en run_scan_cycle()
- payload: N/A
- HTTP Telegram: N/A
```

### SCAN #002

Idéntico a #001 (0 partidos, 0 opps, Telegram NO).

### SCAN #003

Idéntico a #001 (0 partidos, 0 opps, Telegram NO).

---

## Por qué una oportunidad llega o no a Telegram

Código real (nombres):

```
main.send_startup_message()     → SIEMPRE intenta (si hay token/chat)
main.run_scan_cycle()
  └─ opps = ArbScanner.run_once()   # SOLO newly_saved
  └─ si opps vacío → return         # ← bloqueo actual
  └─ por cada opp:
        format_alert(...)
        send_telegram_message(...)  # nombre real (no send_telegram_alert)
```

| Condición | ¿Se llama send? | Evidencia |
|-----------|-----------------|-----------|
| Startup | SI (mensaje fijo) | Observado en servidor |
| Opp nueva + quotes reales | SI (vía main) | No reproducible: 0 quotes |
| Opp duplicada (mismo fingerprint) | NO | `save_if_new=False` → no entra a la lista retornada |
| 0 quotes (APIs rotas / sin mock) | NO | SCAN #001–#003 |

---

## Qué NO se hizo (por instrucción)

- No se modificó Telegram.
- No se cambió el algoritmo de arbitraje ni probabilidades.
- No se implementaron APIs reales de casas.
- No se “corrigió” el envío: solo se demostró el bloqueo.

---

## Próximo paso (fuera de este reporte)

Conectar scrapers a endpoints/APIs reales que devuelvan JSON de fixtures/odds. Hasta entonces **no pueden existir alertas de arbitraje reales**, aunque el bot de Telegram funcione.
