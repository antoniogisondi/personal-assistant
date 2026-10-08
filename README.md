# GSOI Personal Assistant

Assistente personale agentico, modulare e privacy-first (parte dell'ecosistema GSOI).
Il modello linguistico è il "cervello"; le azioni sono eseguite da tool e connettori controllati
da una policy di sicurezza.

- Progetto architetturale: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Decisioni: [docs/adr/](docs/adr/)

## Stato

- **Fasi 0-1:** backend FastAPI, profili modello, astrazione provider LLM (DeepSeek, Ollama, vLLM,
  llama.cpp, OpenAI via adapter compatibile), `LLMGateway` (egress policy, retry, fallback, costi),
  chat JSON e SSE, persistenza, migrazioni, CI.
- **Fase 2:** sistema di tool con livelli di rischio (lettura / scrittura locale / esterno /
  distruttivo), policy di autorizzazione, approvazioni legate agli argomenti (monouso, con scadenza),
  audit log con catena di hash, difese da prompt injection (contenuti non fidati marcati, "taint"),
  loop dell'agente con budget e sospensione/ripresa. Tool inclusi: `time.now`, `notes.*`, `tasks.*`.
  

- **Fase 4-5:** Gmail (cerca, leggi, bozze, invio con approvazione) e Google Calendar (eventi,
  slot liberi calcolati, creazione con approvazione), OAuth con PKCE e token cifrati, riepilogo
  della giornata (`POST /v1/briefing`, in stile parlato). Guida: [docs/google-setup.md](docs/google-setup.md).

API principali: `POST /v1/chat`, `POST /v1/chat/stream`, `GET /v1/approvals`,
`POST /v1/approvals/{id}/decision`, `POST /v1/briefing`, `GET /v1/connections`,
`GET /v1/runs/{id}`, `GET /v1/audit/verify`.

## Avvio rapido (sviluppo)

```bash
cp .env.example .env            # compila GSOI_API_TOKEN, i profili modello e le chiavi
make install                    # uv venv + dipendenze
docker compose up db -d         # PostgreSQL + pgvector
make migrate
make dev                        # http://127.0.0.1:8000/docs
```

```bash
curl -s localhost:8000/v1/chat -H "Authorization: Bearer $GSOI_API_TOKEN" \
  -H 'Content-Type: application/json' -d '{"message": "Ciao"}'
```

Per usare solo il modello locale: `"profile": "private"`. I dati marcati `"data_class": 2`
(SECRET) vengono accettati solo da profili locali; verso il cloud la richiesta è bloccata.

## Avvio su Windows (PowerShell, senza `make`)

Servono Python 3.11+ e [uv](https://docs.astral.sh/uv/) (`winget install astral-sh.uv`).
Lancia tutto dalla cartella del progetto (il `.env` viene letto da lì).

```powershell
copy .env.example .env          # poi compila il .env (vedi sopra)
uv venv
uv pip install -e ".[dev]"
.venv\Scripts\alembic upgrade head
.venv\Scripts\uvicorn gsoi_assistant.api.main:create_app --factory --reload
```

**Database senza Docker (solo per provare):** nel `.env` usa
`GSOI_DATABASE_URL=sqlite+aiosqlite:///./gsoi.db`. Per la memoria vettoriale (fase 6) servirà
PostgreSQL (Docker Desktop: `docker compose up db -d`).

Prova la chat (documentazione interattiva su http://127.0.0.1:8000/docs):

```powershell
$h = @{ Authorization = "Bearer <il tuo GSOI_API_TOKEN>" }
Invoke-RestMethod -Method Post http://127.0.0.1:8000/v1/chat -Headers $h -ContentType "application/json" -Body '{"message":"Ciao, chi sei?"}'
```

## Qualità

`make check` esegue ruff, mypy (strict), import-linter (architettura a livelli) e pytest.
Le chiavi API non stanno mai nel codice: i profili contengono solo il *nome* del segreto.
