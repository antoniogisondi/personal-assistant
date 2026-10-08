# Collegare Gmail e Google Calendar

Serve un progetto Google Cloud tuo (gratuito) con credenziali OAuth. L'assistente non ha accesso
ai tuoi dati finché non completi il passo 6, e puoi revocarlo in qualsiasi momento.

## 1. Crea il progetto e abilita le API
1. Vai su <https://console.cloud.google.com/> e crea un progetto (es. "GSOI Assistant").
2. **API e servizi → Libreria**: abilita **Gmail API** e **Google Calendar API**.

## 2. Schermata di consenso OAuth
1. **API e servizi → Schermata consenso OAuth** (o "Google Auth Platform").
2. Tipo di utente: **Esterno** (account Gmail personale). Compila nome app e la tua email.
3. **Utenti di test**: aggiungi il tuo indirizzo Gmail.
4. Scope: non serve aggiungerli qui a mano, li richiede l'app (sola lettura Gmail, bozze/invio Gmail,
   lettura e modifica eventi Calendar).

> Con l'app in stato "Testing" Google fa scadere il refresh token dopo circa 7 giorni: dovrai
> ricollegare l'account ogni settimana. Per evitarlo puoi portare l'app in "In produzione" per uso
> personale (Google mostrerà l'avviso "app non verificata", che puoi superare perché l'app è tua).
> Verifica le regole attuali nella documentazione Google, perché cambiano.

## 3. Credenziali
1. **Credenziali → Crea credenziali → ID client OAuth**.
2. Tipo di applicazione: **Applicazione web**.
3. **URI di reindirizzamento autorizzati**: `http://127.0.0.1:8000/v1/connections/google/callback`
   (deve coincidere carattere per carattere con `GSOI_GOOGLE_REDIRECT_URI`).
4. Copia **ID client** e **Segreto client**.

## 4. Configura il `.env`
```
GSOI_GOOGLE_CLIENT_ID=xxxxxxxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=il-segreto-client
GSOI_MASTER_KEY=<chiave>
```
Genera `GSOI_MASTER_KEY` con `python scripts/generate_master_key.py` (Windows:
`.venv\Scripts\python scripts\generate_master_key.py`). Cifra i token salvati: **conservala**; se la
perdi basta ricollegare Google. Non committare mai il `.env`.

Poi: `alembic upgrade head` (migrazione 0003) e riavvia il server.

## 5. Collega l'account
Da `/docs` (dopo "Authorize"): `POST /v1/connections/google/start` → copia `auth_url`, aprilo nel
browser, accetta i permessi. Tornerai su una pagina "Google collegato".
`GET /v1/connections` mostra lo stato; `DELETE /v1/connections/google` revoca e cancella.

## 6. Provalo
- `POST /v1/chat`: "Quante email non lette ho oggi?", "Cosa ho in calendario domani?",
  "Trova un'ora libera giovedì pomeriggio".
- `POST /v1/briefing`: il riepilogo della giornata, scritto per essere letto ad alta voce.
- Inviare email e creare eventi richiedono sempre la tua approvazione (`/v1/approvals`).
