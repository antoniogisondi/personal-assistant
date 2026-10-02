# GSOI Personal Assistant

Assistente personale agentico, modulare e privacy-first (parte dell'ecosistema GSOI).
Il modello linguistico è il "cervello"; le azioni sono eseguite da tool e connettori controllati
da una policy di sicurezza.

- Progetto architetturale: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Decisioni: [docs/adr/](docs/adr/)

## Stato

Fasi 0 e 1 completate: backend FastAPI, configurazione per profili, astrazione provider LLM
(adapter OpenAI-compatibile: DeepSeek, Ollama, vLLM, llama.cpp, OpenAI), `LLMGateway`
(egress policy, retry, fallback, costi), endpoint chat (JSON e SSE), persistenza di
conversazioni/run/model call, migrazioni Alembic, CI. Nessun tool/connettore ancora (Fase 2+).

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

## Qualità

`make check` esegue ruff, mypy (strict), import-linter (architettura a livelli) e pytest.
Le chiavi API non stanno mai nel codice: i profili contengono solo il *nome* del segreto.
