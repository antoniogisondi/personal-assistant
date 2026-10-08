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

Chi usa l'assistente non deve registrare nulla su Google. La regola è: **un'applicazione Google per
prodotto, non una per utente**. Chi crea il prodotto la registra una volta (parte A) e la fa viaggiare
dentro l'assistente; l'utente vede solo **Collega Google**.

Come si fa, in ordine di preferenza:

1. **Credenziali incluse nella release** (consigliato per un'app installata sul PC dell'utente).
   Crea il client OAuth di tipo **Applicazione desktop** e, quando prepari una release, scrivi
   `src/gsoi_assistant/connectors/google/bundled_google_app.json`:
   ```json
   {"client_id": "xxxx.apps.googleusercontent.com", "client_secret": "..."}
   ```
   Il file è ignorato da git (non finisce nel repository). Per le app desktop Google non considera il
   segreto confidenziale, ed è quello che rende accettabile includerlo nel programma.
2. **Variabili d'ambiente del server** (installazione gestita da un amministratore):
   `GSOI_GOOGLE_CLIENT_ID` e `GOOGLE_CLIENT_SECRET`. Hanno la precedenza su tutto il resto.
3. **Da `/setup`** (sezione "Impostazioni per lo sviluppatore"): comodo mentre sviluppi.

Ordine di precedenza: ambiente > credenziali salvate da `/setup` > credenziali incluse nella release.

### Cosa chiede Google prima che altre persone possano usare la tua app

Questo non dipende dal nostro codice ma dalle regole di Google, e conviene saperlo subito
(verifica i dettagli aggiornati nella loro documentazione):

- **Solo tu e pochi amici (fino a 100 utenti di test):** nessuna verifica. Con l'app in "Testing" il
  collegamento scade dopo circa 7 giorni; in "In produzione" non verificata no, ma compare un avviso
  "app non verificata" che l'utente deve superare.
- **Pubblico generico:** Google richiede la **verifica dell'app**. Gli scope per leggere e comporre
  email di Gmail sono classificati come **riservati ("restricted")**, e per questi la verifica
  comprende una valutazione di sicurezza esterna, a pagamento e ripetuta ogni anno. Gli scope di
  Calendar sono "sensibili": richiedono verifica, ma più leggera.
- **Alternative se non vuoi affrontare la verifica:** collegare la posta tramite IMAP con password
  per app (nessuna verifica Google, ma meno comodo per l'utente), oppure limitare il prodotto a
  Calendar più invio email (scope meno restrittivi).

Per l'uso personale e per far provare l'assistente a poche persone, i punti 1-3 bastano.

## Provalo

- Chat: "Quante email non lette ho oggi?", "Cosa ho in calendario domani?", "Trova un'ora libera
  giovedì pomeriggio".
- `POST /v1/briefing` (da `/docs`): il riepilogo della giornata, scritto per essere letto ad alta voce.
- Inviare email e creare eventi richiedono sempre la tua approvazione.
