# Local-Council

Eine schlanke Web-App für ein „LLM Council“ mit ausschließlich lokalen Modellen über [Ollama](https://ollama.com).
Sie läuft auf einem Rechner im Heimnetz und ist von allen Geräten unter `http://<IP>:<Port>` erreichbar.

**Drei Modi:**

| Modus | Was passiert |
|---|---|
| **Speed-Test** | Ein Prompt geht an mehrere Modelle, gleichzeitig oder nacheinander. Eine Tabelle vergleicht Tokens/s, Zeit bis zum ersten Token, Gesamtzeit, Prompt-Verarbeitung und Ladezeit. Jeder Lauf wird pro Modell als CSV gespeichert, und zwei Verlaufsdiagramme (nacheinander / gleichzeitig) zeigen die Entwicklung. |
| **Council** | Mehrere Modelle beantworten eine Frage. Danach sehen sie die (anonymisierten) Antworten der anderen, diskutieren über N Runden (Standard: 2) und überarbeiten ihre Antwort. Zum Schluss fasst ein wählbares Modell alles zu einer finalen Antwort zusammen. |
| **Chat** | Chat mit gespeicherten Unterhaltungen, Markdown- und Code-Darstellung, Stopp/Neu generieren und einem eigenen Modell pro Chat. |

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
(FastAPI, uvicorn, httpx, pypdf, numpy). Ändert sich `requirements.txt` (z. B. nach einem `git pull`), installiert
`run.sh` die Abhängigkeiten beim nächsten Start automatisch nach. Danach startet der Server auf `0.0.0.0:8080`.

- Auf dem Rechner selbst: <http://localhost:8080>
- Im Heimnetz: `http://<IP-des-Rechners>:8080` (IP z. B. mit `hostname -I` herausfinden)

Ob die neue Version läuft, zeigt die Seite selbst; Logs stehen im Terminal (bzw. im Journal, siehe Autostart).

Anderer Port oder Host: `./run.sh --port 9000` oder `./run.sh --host 127.0.0.1` (nur lokal erreichbar).

Manuell ohne Skript:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m app
```

Falls eine Firewall aktiv ist, muss der Port freigegeben werden, z. B. `sudo ufw allow 8080/tcp`.

## Neustart und Update

- **Im Terminal gestartet:** mit Strg+C beenden und `./run.sh` erneut starten.
- **Als Dienst (siehe Autostart):** `sudo systemctl restart local-council`
- **Update:** `git pull`, danach neu starten. `run.sh` installiert geänderte Abhängigkeiten selbst nach.
- Die meisten Änderungen an `config.json` wirken sofort (die Datei wird bei jeder Anfrage gelesen);
  nur `server.host` und `server.port` brauchen einen Neustart.

## Autostart (systemd)

Die Datei [`deploy/local-council.service`](deploy/local-council.service) startet die App nach dem Booten, sobald
Netzwerk und Ollama laufen, startet sie nach einem Absturz neu (nach 5 s) und läuft als normaler Benutzer.
Sie ist auf diesen Rechner eingestellt: Benutzer `fox`, Ordner `/home/fox/Local-Council`. Auf einem anderen
Rechner `User=`, `Group=`, `WorkingDirectory=` und `ExecStart=` anpassen.

Läuft die App gerade von Hand, zuerst beenden, sonst ist Port 8080 belegt (Strg+C im Terminal, oder
`pkill -f '.venv/bin/python -m app'`). Dann installieren und starten:

```bash
sudo cp /home/fox/Local-Council/deploy/local-council.service /etc/systemd/system/local-council.service
sudo systemctl daemon-reload
sudo systemctl enable --now local-council
systemctl status local-council
```

Nützlich danach:

```bash
journalctl -u local-council -f            # Log live ansehen
sudo systemctl restart local-council      # nach einem Update
sudo systemctl stop local-council         # anhalten (startet beim nächsten Booten wieder)
```

Nach Änderungen an der Service-Datei: erneut kopieren, `sudo systemctl daemon-reload`, dann `restart`.
Wieder entfernen:

```bash
sudo systemctl disable --now local-council
sudo rm /etc/systemd/system/local-council.service
sudo systemctl daemon-reload
```

## Chat

- **Gespeicherte Chats:** Jede Unterhaltung liegt als JSON-Datei in `data/chats/<id>.json` (nicht im Git) und ist
  von jedem Gerät im Heimnetz aus erreichbar. Die Liste links zeigt alle Chats, der zuletzt geöffnete wird nach F5
  wieder geöffnet. Ein Chat wird mit der ersten Nachricht gespeichert und bekommt deren Anfang als Titel.
- **Titel:** Nach der ersten Antwort schreibt ein Modell einen kurzen Titel (abschaltbar unter Einstellungen →
  Chat-Titel, dort auch ein eigenes, schnelles Modell dafür). Umbenennen per ✎ in der Liste, „Umbenennen“ oder
  Doppelklick auf den Titel; selbst vergebene Titel werden nicht überschrieben.
- **Suche:** Das Suchfeld über der Liste durchsucht Titel, alle Nachrichten und Dateinamen aller Chats. Treffer
  erscheinen mit Textausschnitt; ein Klick öffnet den Chat an der passenden Nachricht. Esc leert die Suche.
- **Export:** „Markdown“ / „JSON“ oben im Chat laden den Chat herunter (Markdown lesbar, inkl. Gedankengang;
  JSON vollständig). „Alle Chats exportieren (ZIP)“ unter der Liste packt alle Chats in beiden Formaten.
- **Modell pro Chat:** Das Modell oben gilt für die nächsten Antworten dieses Chats. Man kann es mitten im Chat
  wechseln; jede Antwort zeigt, von welchem Modell sie stammt. „Als Standard für neue Chats“ legt das Modell für
  neue Chats fest.
- **Stopp / Neu generieren:** „Stopp“ (oder Esc) bricht ab, die Teilantwort bleibt gespeichert. „Neu generieren“
  verwirft die letzte Antwort und erzeugt sie mit dem gerade gewählten Modell neu. Nach einem Fehler heißt der
  Button „Erneut versuchen“.
- **Markdown und Code:** Antworten werden als Markdown dargestellt, Codeblöcke mit Syntax-Hervorhebung und
  „Kopieren“-Button. Die Bibliotheken (marked, DOMPurify, highlight.js) liegen in `static/vendor/`; die App braucht
  also kein Internet. HTML in Antworten wird mit DOMPurify bereinigt.
- **System-Prompt, Vorlagen und Einstellungen** (aufklappbar über dem Chat): gelten pro Chat und werden mit ihm
  gespeichert. Vorlagen sind gespeicherte System-Prompts (in `config.json` unter `chat.presets`, drei Beispiele sind
  dabei). Generierungs-Einstellungen: Temperatur, top_p, top_k, repeat_penalty, Kontextlänge (`num_ctx`),
  maximale Antwortlänge (`num_predict`) und Seed. Leere Felder nehmen die Modell-Optionen aus den Einstellungen bzw.
  den Standard des Modells. „Denken“ schaltet bei Thinking-Modellen (qwen3, deepseek-r1 …) das Nachdenken an oder
  aus. „Modell & Einstellungen als Standard für neue Chats“ speichert alles als Vorgabe für neue Chats.
- **Gedankengang:** Bei Thinking-Modellen läuft der Gedankengang live in einem aufgeklappten Bereich mit und wird
  eingeklappt, sobald die Antwort beginnt. Danach steht dort, wie lange das Modell nachgedacht hat.
- **Geschwindigkeit:** Während der Antwort zeigt die Statuszeile Tokens und ~Tokens/s live. Unter jeder Antwort
  stehen Tokens/s, Anzahl Tokens, Zeit bis zum ersten Token, Gesamtzeit, belegter Kontext und ggf. die Ladezeit.
- **Modell laden/entladen:** Neben der Modellauswahl steht, ob das Modell im Speicher ist (Größe, GPU/CPU, bis
  wann). „Jetzt laden“ lädt es vorab, „Entladen“ gibt den Speicher sofort frei. Unter Einstellungen →
  „Im Speicher“ stehen alle geladenen Modelle.
- **Dateien anhängen:** über 📎, per Einfügen (Strg+V) oder durch Hineinziehen in den Chat.
  - **Textdateien** (txt, md, csv, json, Quellcode …) und **PDFs** werden beim Hochladen in Text umgewandelt
    (PDF mit `pypdf`) und vor der Frage an das Modell geschickt. Eingescannte PDFs ohne Textebene gehen nicht.
  - **Bilder** (PNG, JPEG, WebP, GIF) gehen an Vision-Modelle (z. B. gemma3, llava, qwen2.5vl). Große Fotos
    verkleinert der Browser vorher auf 1536 px. Kann das gewählte Modell keine Bilder, erscheint ein Hinweis
    und nichts wird gesendet.
  - Eingefügter Text über 8000 Zeichen wird automatisch zur Datei „Eingefügter Text.txt“.
  - Dateien liegen in `data/uploads/` (max. 20 MB pro Datei). Dateien, die zu keinem Chat mehr gehören, werden beim
    Löschen eines Chats mit aufgeräumt.
- **Kontext-Anzeige:** Neben der Statuszeile steht, wie viel vom Kontextfenster die nächste Anfrage etwa belegt
  (`Kontext ~3.1k / 4k`). Ab 80 % erscheint eine Warnung, über 100 % der Hinweis, dass Ollama den Anfang des Chats
  abschneidet, mit der Empfehlung, die Kontextlänge zu erhöhen (inkl. Maximum des Modells) oder neu zu beginnen.
  Achtung: Ohne eigene Einstellung nutzt Ollama nur 4096 Tokens, das reicht für längere PDFs nicht.
- **Wissensbasen (RAG):** siehe unten.
- **Werkzeuge:** Mit „Werkzeuge“ darf das Modell selbst einen Rechner (exakte Arithmetik), Datum/Uhrzeit und –
  falls Websuche eingerichtet ist – Websuche und „Webseite lesen“ aufrufen. Geht nur mit Modellen, die Tool-Calling
  können (z. B. qwen3, llama3.1, qwen2.5, mistral-nemo); bei anderen erscheint ein Hinweis. Die Aufrufe und ihre
  Ergebnisse stehen aufklappbar über der Antwort.
- **Websuche:** Mit „Websuche“ wird gesucht (SearXNG, siehe unten). Mit Werkzeugen entscheidet das Modell selbst,
  wann und wonach es sucht; ohne Werkzeuge wird einmal mit der Frage gesucht und die Treffer werden mitgeschickt.
  Die Treffer stehen als Quellen unter der Antwort.
- **Formeln:** LaTeX in Antworten wird mit KaTeX gesetzt: `$…$` und `\(…\)` im Text, `$$…$$` und `\[…\]` als
  eigene Zeile. KaTeX liegt mit Schriften in `static/vendor/katex/`, funktioniert also offline. Code bleibt
  unverändert, und Beträge wie „$5 bis $10“ bleiben Text. Während eine Formel noch gestreamt wird, steht sie grau
  als Rohtext da und wird gesetzt, sobald sie vollständig ist. Fehlerhafte Formeln erscheinen rot, der Rest der
  Antwort bleibt lesbar.
- **Status:** Unter dem Chat steht, was gerade passiert (Modell wird geladen, denkt nach, schreibt, fertig mit
  Tokens/s) oder welcher Fehler aufgetreten ist (Modell nicht installiert, Ollama oder Server nicht erreichbar).

## Wissensbasen (RAG)

Unter **Einstellungen → Wissensbasen** legt man Sammlungen von Dokumenten an (PDF, Text, Markdown, Quellcode …).
Jedes Dokument wird in Abschnitte von ca. 1200 Zeichen geteilt und mit einem lokalen **Embedding-Modell**
eingelesen. Im Chat wählt man unter „System-Prompt & Einstellungen“ eine Wissensbasis; zu jeder Frage werden dann
die passendsten Abschnitte (Standard: 5) vor die Frage gesetzt. Das Modell soll sie mit [1], [2] … zitieren, und
unter der Antwort stehen die Quellen mit Datei, Seite und Ähnlichkeit.

- Embedding-Modell: Standard ist **bge-m3** (mehrsprachig, gut für Deutsch): `ollama pull bge-m3`. Jede
  Wissensbasis merkt sich ihr Modell; ein anderes Modell gilt nur für neue Wissensbasen.
- Suche: Ähnlichkeit der Embeddings plus ein Bonus für exakt vorkommende seltene Wörter (Fehlercodes, Namen,
  Artikelnummern), die Embeddings allein oft verfehlen.
- Gespeichert in `data/rag/<id>/` (Abschnitte als JSON, Vektoren als `.npy`, dafür wird numpy gebraucht).
- Embedding-Modelle tauchen in den Modell-Listen von Speed-Test, Council und Chat nicht auf, da sie nicht chatten
  können.

## Websuche (optional, SearXNG)

Die Websuche nutzt eine eigene [SearXNG](https://docs.searxng.org/)-Instanz, z. B. per Docker:

```bash
mkdir -p ~/searxng && docker run -d --name searxng --restart unless-stopped \
  -p 8888:8080 -v ~/searxng:/etc/searxng searxng/searxng
```

In `~/searxng/settings.yml` muss das JSON-Format erlaubt sein (danach `docker restart searxng`):

```yaml
search:
  formats:
    - html
    - json
```

Dann unter **Einstellungen → Websuche** die URL eintragen (z. B. `http://127.0.0.1:8888`) und „Verbindung testen“.
Das Werkzeug „Webseite lesen“ ruft nur öffentliche Adressen ab, nie Geräte im Heimnetz.

## Darstellung: Hell/Dunkel und Handy

- Der Knopf oben rechts schaltet das Farbschema um: **Auto** (wie das System) → **Hell** → **Dunkel**. Die Wahl
  gilt pro Gerät/Browser.
- Auf schmalen Bildschirmen (Handy) liegt die Chatliste hinter „☰ Chats“, die Tabs lassen sich wischen und
  Eingabefelder zoomen auf iOS nicht ungewollt hinein.

## Konfiguration

Beim ersten Start entsteht aus `config.example.json` die Datei **`config.json`**. Sie ist in `.gitignore`
eingetragen, damit deine lokale Modellauswahl nicht im Repo landet. Fehlende Schlüssel werden automatisch aus
`config.example.json` ergänzt.

```jsonc
{
  "server": {
    "host": "0.0.0.0", "port": 8080,   // Neustart nötig
    "allowed_hosts": []                // zusätzliche Hostnamen, siehe „Sicherheit“
  },
  "ollama": {
    "url": "http://127.0.0.1:11434",   // Ollama darf auch auf einem anderen Rechner laufen
    "timeout": 600,                    // Sekunden ohne Daten, bevor eine Anfrage abbricht
    "keep_alive": "5m",                // wie lange Ollama Modelle im Speicher hält
    "options": {}                      // Ollama-Modelloptionen, z. B. {"temperature": 0.7, "num_ctx": 8192}
  },
  "speedtest": { "models": [], "parallel": true, "prompt": "…" },
  "council":   { "members": [], "synthesizer": "", "rounds": 2, "parallel": true },
  "chat": {
    "model": "", "system": "", "options": {}, "think": null,   // Vorgaben für neue Chats …
    "knowledge": null, "tools": false, "web": false,           // … inkl. Wissensbasis, Werkzeuge, Websuche
    "presets": [{ "name": "…", "system": "…" }],               // System-Prompt-Vorlagen
    "auto_title": true, "title_model": ""                      // automatische Titel
  },
  "rag": { "embed_model": "bge-m3", "top_k": 5 },              // Wissensbasen
  "web": { "searxng_url": "", "max_results": 5 },              // Websuche
  "auth": { "password_hash": "", "secret": "" }                // nur über Einstellungen/CLI ändern
}
```

**Modelle sind nirgends fest einprogrammiert.** Du kannst sie jederzeit wählen:

- direkt im jeweiligen Modus. Die Auswahl gilt für die nächste Anfrage.
- mit **„Auswahl als Standard speichern“**. Damit landet die Auswahl in `config.json` und ist beim nächsten Öffnen vorausgewählt.
- durch direktes Bearbeiten von `config.json` (wirkt sofort, die Datei wird bei jeder Anfrage neu gelesen).

Der Tab **Einstellungen** bearbeitet Ollama-URL, `keep_alive`, Timeout und Modelloptionen und zeigt die installierten Modelle an.
Ist `council.synthesizer` leer, fasst das erste Ratsmitglied zusammen.

Diese Felder werden beim Tippen automatisch in `config.json` gespeichert. Sie überstehen also F5 und einen
Neustart und gelten auf allen Geräten im Heimnetz: Speed-Test-Prompt, Council-Frage, Chat-System-Prompt,
`keep_alive`, Timeout und Modell-Optionen. Ungültige Eingaben (kaputtes JSON, Timeout unter 10 s) werden nicht
gespeichert, das Feld zeigt dann einen Hinweis.

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
zählt als „nacheinander“.

**Benchmark:** „Benchmark starten“ führt den Speed-Test mehrfach hintereinander aus (Anzahl einstellbar,
Standard 10), mit den angehakten Modellen, dem Prompt und der Einstellung „Gleichzeitig senden“. Jeder Lauf wird
gespeichert, und die Tabelle zeigt pro Modell Ø/Min/Max Tokens/s, Ø Zeit bis zum ersten Token, Ø Gesamtzeit und
Fehler. Optional ist der erste Lauf ein Aufwärmlauf: Er lädt die Modelle in den Speicher und wird nicht gespeichert,
damit die Ladezeit die Werte nicht verfälscht. „Stopp“ bricht den ganzen Benchmark ab, bereits fertige Läufe
bleiben gespeichert.

Wird der Verlauf zu voll, löscht der Button **„Verlauf kürzen“** alle älteren Messungen. Pro Modell bleiben die
letzten 3 Läufe je Modus erhalten (3 × nacheinander, 3 × gleichzeitig). Zum kompletten Zurücksetzen einfach die
CSV-Dateien löschen.

## Sicherheit im Heimnetz und Passwort

Ohne Passwort kann jeder im Netz, der den Port erreicht, die App benutzen, alle Chats lesen und die Konfiguration
ändern. Optional lässt sich ein **Passwort** setzen:

- unter **Einstellungen → Passwort**, oder auf dem Server mit `.venv/bin/python -m app --set-password`.
- Danach fragt jedes Gerät einmal nach dem Passwort und bleibt 30 Tage angemeldet („Abmelden“ oben rechts).
  Ein neues Passwort meldet alle anderen Geräte ab. Alle `/api/…`-Adressen verlangen dann die Anmeldung.
- Nach 10 falschen Versuchen von einer Adresse werden Anmeldungen von dort 15 Minuten lang abgelehnt.
- Gespeichert wird nur ein Hash (PBKDF2) in `config.json` unter `auth`; die API gibt ihn nie heraus.
- Passwort vergessen: `.venv/bin/python -m app --remove-password`.

Weitere Schutzmaßnahmen, die immer aktiv sind:

- **Erlaubte Adressen (Host-Prüfung):** Die App antwortet nur, wenn sie per IP-Adresse, `localhost` oder über den
  Namen dieses Rechners aufgerufen wird (auch mit `.local`, `.lan`, `.fritz.box` …). Das verhindert, dass eine
  fremde Webseite über „DNS-Rebinding“ aus deinem Browser heraus auf die App zugreift. Wer einen anderen Namen
  nutzt (z. B. über Tailscale oder einen Reverse-Proxy), trägt ihn in `config.json` ein:
  `"server": { "allowed_hosts": ["meinserver.tailnet.ts.net", ".example.org"] }` (`".…"` = ganze Domain,
  `"*"` = Prüfung aus).
- **Content-Security-Policy:** Die Seite lädt nur Dateien von diesem Server. Bilder von fremden Adressen in
  Antworten werden nicht geladen; so kann eine manipulierte Webseite oder ein Dokument das Modell nicht dazu
  bringen, Chat-Inhalte über eine Bild-URL nach außen zu schicken. Antworten werden zusätzlich mit DOMPurify
  bereinigt.
- **Dateien:** Uploads, Chats und Wissensbasen werden nur über geprüfte IDs angesprochen, nie über Dateinamen aus
  der Anfrage. Hochgeladene Textdateien werden als reiner Text ausgeliefert, nie als HTML.
- **„Webseite lesen“:** ruft nur öffentliche http(s)-Adressen ab, prüft jede Weiterleitung neu und lädt höchstens
  2 MB. Adressen im Heimnetz, `localhost`, Link-Local und ähnliche werden abgelehnt.

Die Verbindung ist unverschlüsselt (HTTP). Das Passwort schützt im Heimnetz vor Mitbenutzung, ersetzt aber kein
HTTPS. Den Port deshalb nicht ins Internet weiterleiten; für Zugriff von unterwegs besser ein VPN (z. B. WireGuard,
Tailscale) nutzen.

## Bekannte Einschränkungen

- **Kein HTTPS, keine Benutzerkonten:** Ein gemeinsames Passwort für alle; wer angemeldet ist, sieht alle Chats.
  Passwort und Cookie gehen im Heimnetz unverschlüsselt über die Leitung.
- **Chats speichert der Browser:** Wird ein Tab mitten in einer Antwort geschlossen, ist die Frage gespeichert, die
  Teilantwort nicht. Bearbeiten zwei Geräte gleichzeitig denselben Chat, gewinnt der zuletzt gespeicherte Stand.
- **Kontext-Anzeige ist geschätzt** (≈ 3,5 Zeichen pro Token). Ohne eigene Einstellung nutzt Ollama nur 4096
  Tokens, längere Chats und Dokumente werden dann vorne abgeschnitten.
- **PDFs:** nur mit Textebene, keine Texterkennung (OCR) für eingescannte Seiten; Tabellen und Spalten kommen als
  Fließtext an.
- **Wissensbasen:** einfache Abschnitte fester Größe, keine Layout-Erkennung. Eine Wissensbasis bleibt bei dem
  Embedding-Modell, mit dem sie angelegt wurde; zum Wechseln neu anlegen.
- **Werkzeuge:** nur mit Modellen, die Tool-Calling können, und kleine Modelle nutzen sie nicht immer (rechnen
  dann z. B. selbst). Höchstens 6 Werkzeug-Runden pro Antwort.
- **Websuche** braucht eine eigene SearXNG-Instanz.
- **„Webseite lesen“:** Zwischen Prüfung der Adresse und Abruf könnte ein präparierter DNS-Server theoretisch
  eine andere Adresse liefern (DNS-Rebinding); sehr unwahrscheinlich, aber nicht ausgeschlossen.
- **Formeln:** KaTeX kann nicht alles, was LaTeX kann (z. B. keine eigenen Pakete, kein TikZ). Die Thinking-Ausgabe
  wird als reiner Text gezeigt.
- **Speed-Test:** Gleichzeitige Läufe teilen sich GPU und CPU; Tokens/s sind dann nicht mit Einzelläufen vergleichbar.

## Council-Prompts anpassen

Die Prompts für Diskussion und Zusammenfassung stehen als Konstanten oben in `app/modes/council.py`
(`SYSTEM_PROMPT`, `DISCUSSION_PROMPT`, `SYNTHESIS_PROMPT`). Sie sind auf Englisch, weil das bei kleinen
Modellen meist zuverlässiger funktioniert. Die Modelle sollen aber in der Sprache der Frage antworten.

## Projektstruktur

```
Local-Council/
├── app/
│   ├── __main__.py      # Einstiegspunkt: python -m app [--host] [--port]
│   ├── main.py          # FastAPI: API-Routen + Auslieferung des Frontends
│   ├── config.py        # config.json laden/speichern
│   ├── ollama.py        # Ollama-Client (Modelle auflisten, Chat streamen, Messwerte)
│   ├── history.py       # Speed-Test-Verlauf als CSV pro Modell
│   ├── chats.py         # gespeicherte Chats als JSON pro Chat
│   ├── uploads.py       # Dateianhänge: speichern, Text aus PDFs ziehen, für Ollama aufbereiten
│   ├── titles.py        # automatische Chat-Titel
│   ├── export.py        # Export als Markdown/JSON/ZIP
│   ├── auth.py          # optionales Passwort (Hash, signiertes Cookie)
│   ├── rag.py           # Wissensbasen: Abschnitte, Embeddings, Suche
│   ├── tools.py         # Werkzeuge für Tool-Calling (Rechner, Datum, Web)
│   ├── websearch.py     # SearXNG-Suche, Webseiten als Text (nur öffentliche Adressen)
│   └── modes/
│       ├── runner.py    # gemeinsamer Baustein: Jobs parallel/seriell ausführen, Streams zusammenführen
│       ├── speedtest.py # Modus 1
│       ├── council.py   # Modus 2 (inkl. Prompts für Diskussion & Zusammenfassung)
│       └── chat.py      # Modus 3: Wissensbasis, Websuche und Werkzeug-Schleife um die Antwort
├── static/              # Frontend: reines HTML/CSS/JS, kein Build-Schritt
│   ├── index.html
│   ├── style.css
│   ├── core.js          # gemeinsame Helfer: API, NDJSON-Stream, Autosave, Kopieren
│   ├── theme-init.js    # Farbschema vor dem ersten Zeichnen setzen
│   ├── theme.js         # Hell/Dunkel/Auto
│   ├── auth.js          # Anmeldung, Passwort setzen
│   ├── chart.js         # kleines SVG-Liniendiagramm (keine externe Bibliothek)
│   ├── markdown.js      # Markdown → bereinigtes HTML mit Code-Hervorhebung
│   ├── math.js          # Formeln ($…$, $$…$$, \(…\), \[…\]) mit KaTeX
│   ├── chat.js          # Chat-Tab: Chatliste, Nachrichten, Streaming
│   ├── chat-settings.js # System-Prompt, Vorlagen, Generierungs-Einstellungen pro Chat
│   ├── models.js        # Modelle im Speicher: laden/entladen, Fähigkeiten
│   ├── attachments.js   # Dateien anhängen: Button, Einfügen, Ziehen
│   ├── context.js       # Kontext-Anzeige und Warnung
│   ├── chat-tools.js    # Suche, Umbenennen, Export, Auto-Titel, Chatliste auf dem Handy
│   ├── rag.js           # Wissensbasen und Websuche (Einstellungen)
│   ├── app.js           # Speed-Test, Council, Einstellungen, Start
│   └── vendor/          # marked, DOMPurify, highlight.js, KaTeX (unverändert, mit Lizenz-Hinweis)
├── data/                # Messdaten, Chats, Anhänge, Wissensbasen (rag/), nicht im Git
├── deploy/
│   └── local-council.service  # systemd-Dienst für den Autostart
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
| GET | `/api/models/loaded` | Modelle, die Ollama gerade im Speicher hat |
| POST | `/api/models/load`, `/api/models/unload` | `{model}` → Modell laden / sofort entladen |
| POST | `/api/models/show` | `{model}` → Fähigkeiten (`vision`, `thinking`, `tools`) und maximale Kontextlänge |
| GET / PUT | `/api/config` | Konfiguration lesen / speichern |
| POST | `/api/speedtest` | `{prompt, models?, parallel?}` → NDJSON-Stream |
| GET | `/api/speedtest/history` | alle gespeicherten Speed-Test-Messungen |
| POST | `/api/speedtest/history/prune` | `{keep: 3}` → behält pro Modell und Modus nur die neuesten `keep` Messungen |
| POST | `/api/council` | `{question, members?, synthesizer?, rounds?, parallel?}` → NDJSON-Stream |
| POST | `/api/chat` | `{messages: [{role, content, attachments?}], model?, system?, options?, think?, knowledge?, tools?, web?}` → NDJSON-Stream |
| POST | `/api/uploads?name=…` | Datei als roher Body → Metadaten mit `id` (für `attachments`) |
| GET | `/api/uploads/{id}` | die hochgeladene Datei |
| GET | `/api/chats` | Liste der gespeicherten Chats (id, title, model, created, updated, count) |
| GET / PUT / PATCH / DELETE | `/api/chats/{id}` | einen Chat lesen / speichern / einzelne Felder ändern / löschen |
| GET | `/api/chats/search?q=…` | Volltextsuche über alle Chats |
| GET | `/api/chats/{id}/export?format=md\|json` | einen Chat herunterladen |
| GET | `/api/chats/export` | alle Chats als ZIP |
| POST | `/api/chats/title` | `{model, question, answer}` → `{title}` |
| GET / POST | `/api/rag` | Wissensbasen auflisten / `{name}` anlegen |
| DELETE | `/api/rag/{id}` | Wissensbasis löschen |
| POST | `/api/rag/{id}/documents` | `{upload_id}` einlesen → NDJSON-Fortschritt |
| DELETE | `/api/rag/{id}/documents/{upload_id}` | Dokument entfernen |
| POST | `/api/web/test` | SearXNG-Verbindung prüfen |
| GET / POST | `/api/auth`, `/api/login`, `/api/logout` | Anmeldestatus, anmelden, abmelden |
| PUT | `/api/auth/password` | `{password}` setzen, leer = Passwortschutz aus |

Felder mit `?` fallen auf die Werte aus `config.json` zurück. Beispiel:

```bash
curl -N http://localhost:8080/api/speedtest -H 'Content-Type: application/json' \
  -d '{"prompt": "Hallo!", "models": ["llama3.2:3b", "qwen3:4b"], "parallel": false}'
```
