# Collegare Gmail e Google Calendar

Non serve modificare nessun file di configurazione. Tutto si fa dalla pagina
**http://127.0.0.1:8000/setup**.

Ci sono due ruoli diversi (oggi li ricopri entrambi tu):

| Ruolo | Cosa fa | Quante volte |
|---|---|---|
| **Chi mette a disposizione l'assistente** (sviluppatore / amministratore) | Registra l'applicazione presso Google e incolla Client ID e Client Secret in `/setup` | Una volta sola |
| **Chi lo usa** | Preme **Collega Google** e sceglie cosa consentire | Una volta per account |

La chiave che cifra i token viene creata da sola al primo avvio (file `data/master.key`, non
committato). Non devi generarla né copiarla.

## A. Registrare l'applicazione Google (una volta sola)

1. <https://console.cloud.google.com/> → crea un progetto (es. "GSOI Assistant").
2. **API e servizi → Libreria**: abilita **Gmail API** e **Google Calendar API**.
3. **Schermata consenso OAuth** → tipo **Esterno** → nome app e tua email → aggiungi il tuo
   indirizzo Gmail come **utente di test**.
4. **Credenziali → Crea credenziali → ID client OAuth → Applicazione web**.
   **URI di reindirizzamento autorizzati**: copia quello mostrato in `/setup` (di default
   `http://127.0.0.1:8000/v1/connections/google/callback`).
5. Copia **ID client** e **Segreto client**.

> Con l'app in stato "Testing" Google fa scadere il collegamento dopo circa 7 giorni e va rifatto
> (un clic su "Collega Google"). Per evitarlo puoi portare l'app "In produzione" per uso personale:
> Google mostra l'avviso "app non verificata", che puoi superare perché l'app è tua. Le regole di
> Google cambiano: verificale nella loro documentazione.

## B. In `/setup`

1. Inserisci la **chiave di accesso** (`GSOI_API_TOKEN`; resta solo nel tuo browser).
2. Nella scheda Google incolla **Client ID** e **Client Secret** → **Salva**. (Il segreto viene
   cifrato nel database e non viene mai mostrato di nuovo.)
3. Premi **Collega Google**, accetta i permessi nella scheda che si apre. La pagina passa a
   "collegato" da sola.

Per scollegare l'account: **Scollega** (revoca anche l'accesso presso Google).
**Cambia credenziali** sostituisce l'applicazione Google (gli account collegati con la vecchia
dovranno essere ricollegati).

## C. Distribuire l'assistente ad altre persone

Chi riceve l'assistente non deve toccare nulla di tutto questo se l'amministratore ha già inserito
le credenziali dell'applicazione: vede solo **Collega Google**. In un'installazione gestita si
possono impostare le credenziali nell'ambiente del server invece che da `/setup`
(`GSOI_GOOGLE_CLIENT_ID` e `GOOGLE_CLIENT_SECRET`); in quel caso hanno la precedenza e la pagina
le mostra come "gestite dal server".

## Provalo

- Chat: "Quante email non lette ho oggi?", "Cosa ho in calendario domani?", "Trova un'ora libera
  giovedì pomeriggio".
- `POST /v1/briefing` (da `/docs`): il riepilogo della giornata, scritto per essere letto ad alta voce.
- Inviare email e creare eventi richiedono sempre la tua approvazione.
