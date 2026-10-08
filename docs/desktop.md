# App desktop GSOI (Windows)

Un programma per ogni PC: si avvia con Windows, vive nella barra accanto all'orologio e ha come
volto una **rete neurale animata** che cambia aspetto in base a ciò che l'assistente sta facendo
(a riposo, ti ascolta, pensa, parla). Dentro c'è tutto: il "backend" gira nello stesso programma,
sulla porta locale `127.0.0.1` (non raggiungibile da altri computer).

```
GSOI.exe
 ├─ Interfaccia (PySide6): rete neurale, chat, impostazioni, approvazioni
 ├─ Servizio interno (FastAPI) su 127.0.0.1:<porta casuale>   ← stessa API di sempre
 │    ├─ Agente + strumenti (Gmail, Calendar, PC, note, attività)
 │    ├─ Database SQLite nella cartella dati dell'utente
 │    └─ Policy di sicurezza, approvazioni, audit
 └─ Segreti: Gestione credenziali di Windows (chiave API del modello, chiave di cifratura, token locale)
```

Cartella dati: `%LOCALAPPDATA%\GSOI` (database, log, impostazioni). Niente file `.env`, niente
configurazione a mano: al primo avvio si apre **Impostazioni** (provider, modello, chiave API).

## Avviarla dai sorgenti (sviluppo)

```powershell
git pull origin main
.venv\Scripts\python -m pip install -e ".[dev,desktop]"
.venv\Scripts\python -m gsoi_desktop            # apre l'app
.venv\Scripts\python -m gsoi_desktop --selftest # prova il servizio interno senza interfaccia
```

## Creare l'eseguibile

```powershell
.venv\Scripts\python -m PyInstaller packaging\gsoi.spec --noconfirm --clean
dist\GSOI\GSOI.exe
```

Produce la cartella `dist\GSOI` (si distribuisce zippata). Una cartella parte più in fretta di un
singolo `.exe` autoestraente e fa scattare molto meno gli antivirus. La CI
(`.github/workflows/build-windows.yml`) lo costruisce su Windows a ogni modifica, lo collauda con
`--selftest` e `--gui-selftest` e pubblica lo zip come artefatto.

Per includere l'applicazione Google (così gli utenti premono solo "Collega Google") metti
`bundled_google_app.json` prima di costruire, oppure `google_app.json` nella cartella dati
(vedi `docs/google-setup.md`).

## Controllo del PC: a livelli, di proposito

"Controllo totale" non è una funzione sola: più è ampio, più un errore (o un testo malevolo letto in
una email o in una pagina web) può fare danni. Quindi si procede per livelli, ciascuno con la sua
regola di sicurezza.

| Livello | Cosa può fare | Sicurezza | Stato |
|---|---|---|---|
| 1 | Aprire **programmi installati** (dal menu Start), aprire indirizzi web http/https, cartelle standard, tasti multimediali | Solo elenco fisso: nessun comando libero. Dopo aver letto contenuti esterni (email, web) chiede il tuo consenso | **Fatto** |
| 2 | Finestre (porta in primo piano, chiudi), volume, luminosità, screenshot | Consenso la prima volta per programma | Da fare |
| 3 | Scrivere da tastiera e cliccare ("compila questo modulo") | Consenso esplicito per ogni sessione, indicatore sempre visibile, interruzione con un tasto | Da fare |
| 4 | Eseguire comandi / script, modificare o cancellare file | Consenso forte **a ogni azione**, mostrando il comando esatto; mai dopo contenuti esterni | Da valutare |

Regole valide per tutti i livelli: ogni azione passa dal registro di audit, la modalità sola lettura
(`readonly`) le blocca tutte, e un'istruzione scritta dentro un'email o una pagina web non può mai
autorizzarle.

## Voce (prossimo passo)

L'interfaccia è già pensata per la voce (stati "ti ascolto"/"parlo", livello del microfono che anima
la rete, pulsante microfono). Manca il collegamento con l'audio:

1. **Parola di attivazione** sempre in ascolto, in locale (nessun audio lascia il PC prima).
2. **Riconoscimento vocale** in locale (faster-whisper).
3. **Sintesi vocale**: già attiva con le voci di Windows per il riepilogo; poi una voce più naturale.
