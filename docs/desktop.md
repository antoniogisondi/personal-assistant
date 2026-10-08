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

## Voce

Si attiva da **Impostazioni → Comando vocale**. Tutto gira sul tuo PC: l'audio non viene mai
inviato da nessuna parte; all'assistente arriva solo il **testo** del comando, come se lo avessi
scritto.

```
microfono ─▶ parola di attivazione "Hey Jarvis" (sempre in ascolto, leggerissima)
              │ scatta
              ▼
          ascolta il comando ─▶ si ferma dopo una pausa (~1 s)
              │
              ▼
          riconoscimento vocale locale (Whisper) ─▶ testo ─▶ assistente (strumenti, approvazioni)
              │
              ▼
          risposta a voce ─▶ finita la voce, torna in ascolto della parola di attivazione
```

- **Parola di attivazione**: [openWakeWord](https://github.com/dscripka/openWakeWord), modello
  "hey jarvis" (pochi MB, inglese: dì «Hey Jarvis»; il comando dopo puoi dirlo in italiano).
  Una parola personalizzata ("GSOI") richiede di addestrare un modello dedicato: è possibile ma
  non è ancora fatto.
- **Riconoscimento**: faster-whisper in locale, modello *Preciso* (~480 MB, consigliato per
  l'italiano) o *Leggero* (~145 MB). Si scarica una volta, al primo utilizzo, con la percentuale
  nella finestra. Conviene il modello Preciso su un PC con qualche anno e almeno 8 GB di RAM.
- **Mentre l'assistente risponde il microfono è ignorato**, così non si ascolta da solo.
  Per interromperlo clicca sulla rete neurale.
- **Pulsante microfono** (🎤): parla senza dire la parola di attivazione; ripremilo per annullare.
- **Conferme a voce: non esistono, di proposito.** Inviare email o creare eventi apre sempre la
  finestra di approvazione, che si conferma con il mouse: una frase detta (o sentita da un video,
  o dalla TV) non può autorizzare azioni.

### Cosa è verificato e cosa no

Verificato con test automatici: il rilevamento ("Hey Jarvis" ≥ 0,99 su registrazioni sintetiche;
altre frasi < 0,07), la cattura del comando e la pausa finale (anche con rumore di fondo), il
riconoscimento su audio registrato, il comportamento della macchina a stati (timeout, errori,
spegnimento durante il riconoscimento).

**Da provare sul tuo PC**, perché dipende dal microfono, dalla stanza e dalla tua pronuncia: la
soglia di attivazione (`wake_threshold` in `%LOCALAPPDATA%\GSOI\config.json`, 0,5 di partenza:
abbassala se non ti sente, alzala se scatta da sola), il tempo di risposta del modello vocale sul
tuo processore e la qualità del riconoscimento con la tua voce.

### La voce dell'assistente

Oggi usa le voci installate in Windows. Il motore è intercambiabile. Voci cloni di persone reali
(ad esempio doppiatori) non vengono integrate: senza il loro consenso e una licenza non possono
essere distribuite in un prodotto. Strade corrette: una voce open source (es. Piper), la tua voce
clonata con il tuo consenso, o una voce con licenza.

## «Hey Jarvis» sulla tua voce

Il modello standard di «Hey Jarvis» è addestrato su pronunce inglesi: con un accento italiano
può richiedere di scandire le parole. In **Impostazioni → Insegna «Hey Jarvis» alla tua voce**
una procedura guidata (circa un minuto) registra 12 volte la frase, 15 s di parlato normale e 5 s di
silenzio, e addestra sul PC un piccolo classificatore sulle caratteristiche audio del modello
standard. Mostra quanto ti riconosce (verifica incrociata sulle tue registrazioni) e quanti falsi
allarmi ha avuto, e si attiva solo se il risultato è migliore del modello standard. Le
registrazioni non vengono salvate: resta solo `models/personal_wake.npz`. Per tornare al modello
standard basta eliminare quel file.
