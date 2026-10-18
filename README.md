# Local-Council

Eine schlanke Web-App für ein „LLM Council“ mit ausschließlich lokalen Modellen über [Ollama](https://ollama.com).
Sie läuft auf einem Rechner im Heimnetz und ist von allen Geräten unter `http://<IP>:<Port>` erreichbar.

**Drei Modi:**

| Modus | Was passiert |
|---|---|
| **Speed-Test** | Ein Prompt geht an mehrere Modelle, gleichzeitig oder nacheinander. Eine Tabelle vergleicht Tokens/s, Zeit bis zum ersten Token, Gesamtzeit, Prompt-Verarbeitung und Ladezeit. Jeder Lauf wird pro Modell als CSV gespeichert, und zwei Verlaufsdiagramme (nacheinander / gleichzeitig) zeigen die Entwicklung. |
| **Council** | Mehrere Modelle beantworten eine Frage. Danach sehen sie die (anonymisierten) Antworten der anderen, diskutieren über N Runden (Standard: 2) und überarbeiten ihre Antwort. Zum Schluss fasst ein wählbares Modell alles zu einer finalen Antwort zusammen. |
| **Chat** | Normaler Chat mit einem frei wählbaren Modell, optional mit System-Prompt. |

Alle Ausgaben werden live gestreamt. Bei „Thinking“-Modellen (z. B. qwen3, deepseek-r1) erscheint der Gedankengang einklappbar über der Antwort.

## Voraussetzungen

- Linux, macOS oder Windows mit **Python ≥ 3.10**
- Laufendes **Ollama** mit mindestens einem Modell, z. B.:
  ```bash
  ollama pull llama3.2:3b
  ollama pull qwen3:4b
  ollama pull gemma3:4b
  ```
- Debian/Ubuntu: Für virtuelle Umgebungen wird `python3-venv` gebraucht:
  ```bash
  sudo apt install python3-venv
  ```

## Setup & Start

```bash
git clone https://github.com/FlipTrick720/Local-Council.git
cd Local-Council
./run.sh
```

Beim ersten Start legt `run.sh` die virtuelle Umgebung `.venv` an und installiert die Abhängigkeiten
(FastAPI, uvicorn, httpx). Danach startet der Server auf `0.0.0.0:8080`.

- Auf dem Rechner selbst: <http://localhost:8080>
- Im Heimnetz: `http://<IP-des-Rechners>:8080` (IP z. B. mit `hostname -I` herausfinden)

Anderer Port oder Host: `./run.sh --port 9000` oder `./run.sh --host 127.0.0.1` (nur lokal erreichbar).

Manuell ohne Skript:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m app
```

Falls eine Firewall aktiv ist, muss der Port freigegeben werden, z. B. `sudo ufw allow 8080/tcp`.

## Konfiguration

Beim ersten Start entsteht aus `config.example.json` die Datei **`config.json`**. Sie ist in `.gitignore`
eingetragen, damit deine lokale Modellauswahl nicht im Repo landet. Fehlende Schlüssel werden automatisch aus
`config.example.json` ergänzt.

```jsonc
{
  "server":    { "host": "0.0.0.0", "port": 8080 },          // Neustart nötig
  "ollama": {
    "url": "http://127.0.0.1:11434",   // Ollama darf auch auf einem anderen Rechner laufen
    "timeout": 600,                    // Sekunden ohne Daten, bevor eine Anfrage abbricht
    "keep_alive": "5m",                // wie lange Ollama Modelle im Speicher hält
    "options": {}                      // Ollama-Modelloptionen, z. B. {"temperature": 0.7, "num_ctx": 8192}
  },
  "speedtest": { "models": [], "parallel": true, "prompt": "…" },
  "council":   { "members": [], "synthesizer": "", "rounds": 2, "parallel": true },
  "chat":      { "model": "", "system": "" }
}
```

**Modelle sind nirgends fest einprogrammiert.** Du kannst sie jederzeit wählen:

- direkt im jeweiligen Modus. Die Auswahl gilt für die nächste Anfrage.
- mit **„Auswahl als Standard speichern“**. Damit landet die Auswahl in `config.json` und ist beim nächsten Öffnen vorausgewählt.
- durch direktes Bearbeiten von `config.json` (wirkt sofort, die Datei wird bei jeder Anfrage neu gelesen).

Der Tab **Einstellungen** bearbeitet Ollama-URL, `keep_alive`, Timeout und Modelloptionen und zeigt die installierten Modelle an.
Ist `council.synthesizer` leer, fasst das erste Ratsmitglied zusammen.

### Hinweise zu Parallelität und Messwerten

- Ollama verarbeitet gleichzeitige Anfragen nur so weit parallel, wie Speicher und die Variablen
  `OLLAMA_MAX_LOADED_MODELS` und `OLLAMA_NUM_PARALLEL` es zulassen. Alles darüber stellt es in eine Warteschlange.
- Im Speed-Test teilen sich gleichzeitig laufende Modelle GPU und CPU. Für **faire Tokens/s-Vergleiche** deshalb
  „Gleichzeitig senden“ ausschalten. Die *Ladezeit*-Spalte zeigt, ob ein Modell erst in den Speicher geladen werden musste.
- Tokens/s und Prompt-Tokens/s stammen direkt aus Ollamas Zeitmessung (`eval_count / eval_duration`).
  *Erstes Token* und *Gesamtzeit* misst der Server selbst, inklusive Ladezeit und Warteschlange.

### Speed-Test-Verlauf (CSV)

Jedes erfolgreich gemessene Modell bekommt pro Lauf eine Zeile in `data/speedtest/<modell>.csv`,
z. B. `data/speedtest/llama3.1_8b.csv`. Der Ordner steht in `.gitignore`.

| Spalte | Bedeutung |
|---|---|
| `timestamp`, `run_id` | Zeitpunkt der Messung / Start des Laufs (alle Modelle eines Laufs teilen die `run_id`) |
| `mode`, `concurrency` | `sequential` oder `parallel`, Anzahl gleichzeitig angefragter Modelle |
| `model`, `version` | Modellname und Anfang des Ollama-Digests (ändert sich bei `ollama pull`-Updates) |
| `tps`, `ttft`, `wall_time` | Tokens/s, Sekunden bis zum ersten Token, Gesamtzeit |
| `tokens`, `prompt_tokens`, `prompt_tps`, `load_time` | Antwort-Tokens, Prompt-Tokens, Prompt-Tokens/s, Ladezeit |

Neue Modelle bekommen automatisch eine eigene Datei und Farbe. Wird ein Modell aktualisiert, beginnt
seine Linie im Diagramm neu, und der Tooltip zeigt „neue Version“. Ein „Gleichzeitig“-Lauf mit nur einem Modell
zählt als „nacheinander“. Zum Zurücksetzen einfach die CSV-Dateien löschen.

## Projektstruktur

```
Local-Council/
├── app/
│   ├── __main__.py      # Einstiegspunkt: python -m app [--host] [--port]
│   ├── main.py          # FastAPI: API-Routen + Auslieferung des Frontends
│   ├── config.py        # config.json laden/speichern
│   ├── ollama.py        # Ollama-Client (Modelle auflisten, Chat streamen, Messwerte)
│   ├── history.py       # Speed-Test-Verlauf als CSV pro Modell
│   └── modes/
│       ├── runner.py    # gemeinsamer Baustein: Jobs parallel/seriell ausführen, Streams zusammenführen
│       ├── speedtest.py # Modus 1
│       ├── council.py   # Modus 2 (inkl. Prompts für Diskussion & Zusammenfassung)
│       └── chat.py      # Modus 3
├── static/              # Frontend: reines HTML/CSS/JS, kein Build-Schritt
│   ├── index.html
│   ├── style.css
│   ├── chart.js         # kleines SVG-Liniendiagramm (keine externe Bibliothek)
│   └── app.js
├── data/speedtest/      # CSV-Messdaten (wird automatisch angelegt, nicht im Git)
├── config.example.json  # Standardkonfiguration (Vorlage für config.json)
├── requirements.txt
└── run.sh
```

**Architektur in einem Satz:** Jeder Modus ist ein einfacher async-Generator, der Ereignisse
(`token`, `done`, `error`, …) erzeugt. `main.py` streamt sie als NDJSON an den Browser, `app.js` zeichnet sie.
Ein neuer Modus braucht also nur eine Datei in `app/modes/`, eine Route in `main.py` und einen Tab im Frontend.

### API

| Methode | Pfad | Body / Antwort |
|---|---|---|
| GET | `/api/models` | installierte Ollama-Modelle |
| GET / PUT | `/api/config` | Konfiguration lesen / speichern |
| POST | `/api/speedtest` | `{prompt, models?, parallel?}` → NDJSON-Stream |
| GET | `/api/speedtest/history` | alle gespeicherten Speed-Test-Messungen |
| POST | `/api/council` | `{question, members?, synthesizer?, rounds?, parallel?}` → NDJSON-Stream |
| POST | `/api/chat` | `{messages: [{role, content}], model?, system?}` → NDJSON-Stream |

Felder mit `?` fallen auf die Werte aus `config.json` zurück. Beispiel:

```bash
curl -N http://localhost:8080/api/speedtest -H 'Content-Type: application/json' \
  -d '{"prompt": "Hallo!", "models": ["llama3.2:3b", "qwen3:4b"], "parallel": false}'
```

## Council-Prompts anpassen

Die Prompts für Diskussion und Zusammenfassung stehen als Konstanten oben in `app/modes/council.py`
(`SYSTEM_PROMPT`, `DISCUSSION_PROMPT`, `SYNTHESIS_PROMPT`). Sie sind auf Englisch, weil das bei kleinen
Modellen meist zuverlässiger funktioniert. Die Modelle sollen aber in der Sprache der Frage antworten.

## Als Dienst starten (optional, systemd)

`/etc/systemd/system/local-council.service`:

```ini
[Unit]
Description=Local Council
After=network-online.target ollama.service

[Service]
User=<dein-benutzer>
WorkingDirectory=/pfad/zu/Local-Council
ExecStart=/pfad/zu/Local-Council/.venv/bin/python -m app
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now local-council
```

## Sicherheit

Die App hat **keine Anmeldung**. Jeder im Netz, der den Port erreicht, kann die Modelle nutzen und die
Konfiguration ändern. Sie ist fürs Heimnetz gedacht. Den Port deshalb nicht ins Internet weiterleiten.
