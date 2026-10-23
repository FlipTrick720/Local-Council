# Local-Council

Eine schlanke Web-App für ein „LLM Council“ mit ausschließlich lokalen Modellen über [Ollama](https://ollama.com).
Sie läuft auf einem Rechner im Heimnetz und ist von allen Geräten unter `http://<IP>:<Port>` erreichbar.

**Drei Modi:**

| Modus | Was passiert |
|---|---|
| **Speed-Test** (Admins) | Ein Prompt geht an mehrere Modelle, gleichzeitig oder nacheinander. Eine Tabelle vergleicht Tokens/s, Zeit bis zum ersten Token, Gesamtzeit, Prompt-Verarbeitung und Ladezeit. Jeder Lauf wird pro Modell als CSV gespeichert, und zwei Verlaufsdiagramme (nacheinander / gleichzeitig) zeigen die Entwicklung. |
| **Chat** | Chat mit gespeicherten Unterhaltungen, Markdown- und Code-Darstellung, Stopp/Neu generieren und einem eigenen Modell pro Chat. Optional mit Wissensbasis, Websuche, Werkzeugen, Erinnerungen und Code-Modus. |
| **Council** (im Chat: „+ Neuer Council“) | Mehrere Modelle mit Rollen beantworten eine Frage, diskutieren die (anonymisierten) Antworten der anderen über N Runden und ein wählbares Modell schreibt die finale Antwort. Eine Council-Sitzung ist ein Gespräch mit Nachfragen, siehe [Council](#council). |
| **Benchmark** (Admins) | Ein Fragenkatalog läuft durch ein Einzelmodell und einen Council; ein Richter-Modell vergleicht die Antworten. Zeigt, ob der Council besser antwortet und was es an Zeit kostet, siehe [Benchmark](#benchmark-council-gegen-einzelmodell). |

Mehrere Personen können gleichzeitig arbeiten, jede mit eigenem Konto; eine Warteschlange verteilt die GPU
(siehe [Benutzer](#benutzer-anmeldung-und-sicherheit) und [Warteschlange](#warteschlange-für-die-gpu)).

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

- **Gespeicherte Chats:** Jede Unterhaltung liegt als JSON-Datei in `data/chats/<benutzer>/<id>.json` (nicht im Git) und ist
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
  wann und wonach es sucht, und arbeitet in bis zu 5 Schritten pro Antwort (z. B. suchen, dann die offizielle Seite
  lesen, ggf. neu suchen); danach muss es mit dem Gefundenen antworten. Ohne Werkzeuge wird einmal mit der Frage
  gesucht und die Treffer werden mitgeschickt. Siehe „Websuche“ unten für Details.
- **Formeln:** LaTeX in Antworten wird mit KaTeX gesetzt: `$…$` und `\(…\)` im Text, `$$…$$` und `\[…\]` als
  eigene Zeile. KaTeX liegt mit Schriften in `static/vendor/katex/`, funktioniert also offline. Code bleibt
  unverändert, und Beträge wie „$5 bis $10“ bleiben Text. Während eine Formel noch gestreamt wird, steht sie grau
  als Rohtext da und wird gesetzt, sobald sie vollständig ist. Fehlerhafte Formeln erscheinen rot, der Rest der
  Antwort bleibt lesbar.
- **Status:** Unter dem Chat steht, was gerade passiert (Modell wird geladen, denkt nach, schreibt, fertig mit
  Tokens/s) oder welcher Fehler aufgetreten ist (Modell nicht installiert, Ollama oder Server nicht erreichbar).

## Council

Ein Council ist ein Chat-Typ: In der Chatliste **„+ Neuer Council“**. Er steht wie ein Chat in der Liste (🏛),
lässt sich umbenennen, durchsuchen und exportieren, und man kann **Nachfragen** stellen: Die Mitglieder bekommen
dann die früheren Fragen, ihre letzten Positionen und die finalen Antworten als Kontext (gekürzt).

- **Mitglieder und Rollen:** 2 bis 6 Mitglieder, jedes mit eigenem Modell und optional einer Rolle (Vorlagen:
  Kritiker, Skeptiker, Faktenprüfer, Praktiker, Zusammenfasser, oder „Eigene Rolle …“ mit eigener Anweisung).
  Dasselbe Modell darf mehrfach mit verschiedenen Rollen dabei sein.
- **Ablauf:** erste Antworten → N Diskussionsrunden (0–5) → finale Antwort durch das gewählte Modell. Die Runden
  stehen einklappbar über der finalen Antwort, pro Mitglied mit Gedankengang, Werkzeug-Aufrufen, Tokens/s und Zeit.
  **Stopp** bricht alles ab; **⏭ Finale Antwort** beendet die Diskussion sofort und lässt mit dem bisherigen Stand
  die finale Antwort schreiben.
- **Werkzeuge und Websuche** (unter „System-Prompt & Einstellungen“): Mitglieder, deren Modell Tool-Calling kann,
  suchen selbst, höchstens „Werkzeug-Schritte“ Aufrufe pro Mitglied und Runde. Suchergebnisse und gelesene Seiten
  sind geteilt: Dieselbe Suche läuft nur einmal, und die nächste Runde bekommt eine Liste dessen, was schon
  recherchiert wurde.
- **Dateien und Wissensbasis:** Anhänge und Wissensbasis-Ausschnitte bekommen alle Mitglieder gleich. Bilder sehen
  nur Mitglieder mit Vision-Modell (sonst ein Hinweis). Pro Mitglied wird der Kontext gegen die Kontextlänge
  seines Modells geprüft: Bei Bedarf wird sie bis `council.max_ctx` (Standard 16384) erhöht, und wenn es trotzdem
  nicht passt, erscheint eine Warnung über der Antwort.
- **Erinnerungen:** Ist „Erinnerungen“ an, bekommen alle Mitglieder deine gemerkten Fakten.
- **Bewertung (optional):** Unter „Bewertung durch“ ein Modell wählen. Nach der finalen Antwort bewertet es die
  *ersten* Antworten der Mitglieder (jedes Modell allein, noch ohne Diskussion) und die finale Antwort nach
  Korrektheit, Vollständigkeit, Klarheit und Nützlichkeit (1–10). Die Antworten bekommt es in zufälliger
  Reihenfolge und ohne Namen. Die Tabelle steht unter der Antwort, alle Bewertungen zusätzlich in
  `data/judge/<benutzer>.jsonl` (auch über `GET /api/judge`), um später auszuwerten, ob der Council ein einzelnes
  Modell schlägt. Ein kleines Modell als Richter ist allerdings selbst kein verlässlicher Maßstab.
- **„Als Standard für neue Councils“** speichert Mitglieder, Rollen, Runden und Bewertungsmodell für dich.
- **Darstellung:** Jede Runde ist einzeln einklappbar (live öffnet sich die neue, die vorige klappt zu). Jedes
  Mitglied hat eine eigene Farbe (Etikett und Kartenrand, in allen Runden gleich); wer gerade schreibt, ist
  hervorgehoben und steht in der Statuszeile („schreibt: Mitglied A (Kritiker)“). Gedankengang und
  Werkzeug-Aufrufe sind zugeklappt, die finale Antwort ist farbig abgesetzt.

## Benchmark: Council gegen Einzelmodell

Tab **Benchmark** (nur Admins): Bringt der Council wirklich bessere Antworten als ein einzelnes Modell, und was
kostet das an Zeit?

- **Fragen:** `data/benchmarks/questions.json`, beim ersten Öffnen mit 20 Startfragen auf Deutsch und Englisch
  (Logik, Wissen, Mathe, Code, Schreiben), wo möglich mit kurzer Referenzantwort. Im Tab unter „Fragen“
  bearbeitbar. Jeder Lauf speichert eine Kopie der Fragen, spätere Änderungen betreffen nur neue Läufe.
- **Lauf:** Einzelmodell, Council (Mitglieder, Rollen, Runden, Modell für die finale Antwort) und Richter wählen,
  „Benchmark starten“. Jede Frage geht nacheinander an das Einzelmodell, den Council und den Richter, jeweils
  über die GPU-Warteschlange (andere können dazwischen arbeiten). Der Lauf arbeitet im Hintergrund auf dem Server
  weiter, auch wenn die Seite geschlossen wird; der Stand („Frage 7/20: Council“) aktualisiert sich von selbst.
  **Stoppen** behält alles Fertige, **Fortsetzen** macht beim ersten fehlenden Schritt weiter (auch nach einem
  Neustart des Servers). Es läuft immer nur ein Benchmark gleichzeitig.
- **Richter:** Er sieht beide Antworten in zufälliger Reihenfolge als „A“ und „B“, ohne Modellnamen; gespeichert
  wird, welche welche war. Er wählt einen Sieger (oder unentschieden), vergibt 1–10 Punkte und prüft bei Fragen
  mit Referenz die Korrektheit. Ist der Richter selbst im Council oder das Einzelmodell, erscheint eine Warnung.
- **Selbst bewerten:** Unter dem Ergebnis die Antworten derselben Frage anonym als A/B sehen und „A ist besser /
  B ist besser / Gleich gut“ wählen. Die Reihenfolge der Fragen ist zufällig, aber fest, so dass auch eine kleine
  Stichprobe aussagekräftig ist. Angezeigt wird, wie oft deine Stimme mit dem Richter übereinstimmt.
- **Ergebnis:** Siege (Einzelmodell / Council / unentschieden) mit Balken, Ø Punkte pro Kategorie, Ø Zeit und Ø
  erzeugte Tokens pro Frage für beide, Anteil richtiger Antworten laut Referenz und der **Zeitfaktor** des
  Councils („braucht im Schnitt 7× so lange“). Alle Läufe bleiben in `data/benchmarks/runs/<id>.json` und stehen
  in der Liste zum Vergleichen.
- **Ehrlich gesagt:** Das Urteil eines kleinen Richter-Modells ist ein Anhaltspunkt, kein Beweis. Es schwankt,
  bevorzugt mitunter längere Antworten und irrt bei Fakten. Deshalb die eigenen Stimmen: Stimmt der Richter selten
  mit dir überein, sind seine Siegquoten wenig wert. 20 Fragen sind außerdem eine kleine Stichprobe.

## Erinnerungen

Unter **Einstellungen → Erinnerungen** stehen Fakten über dich, die Chat und Council kennen sollen (z. B. „Nutzt
Arch Linux“, „mag kurze Antworten“). Sie liegen in `data/memory/<benutzer>.json` und gehören nur dir.

- Bittest du das Modell, sich etwas zu merken, schlägt es den Fakt unter der Antwort vor („💡 Merken?“).
  **Gespeichert wird nur, wenn du auf „Merken“ klickst**; automatisch wird nie etwas gespeichert. Von Hand
  hinzufügen, bearbeiten und löschen geht auf der Einstellungsseite.
- Schalter: global auf der Einstellungsseite, pro Chat unter „System-Prompt & Einstellungen → Erinnerungen“.
- Die aktiven Fakten kommen in den System-Prompt, höchstens 2000 Zeichen (die neuesten zuerst). Die Kontext-Anzeige
  zeigt, wie viele Tokens sie belegen (🧠).
- Vorschläge macht das Modell mit einer Zeile `<<remember: …>>` am Ende der Antwort; der Server nimmt sie aus der
  Antwort heraus. Eine Webseite oder ein Dokument könnte das Modell dazu bringen, einen Fakt vorzuschlagen, deshalb
  braucht jeder Vorschlag deine Bestätigung.

## Code-Modus

Im Chat unter „System-Prompt & Einstellungen“ **„Code-Modus“** anhaken. Das Modell schreibt dann ausführbaren
Python-Code; jeder Python-Block bekommt **„▶ Ausführen“**. Ausgabe, Fehlermeldungen und matplotlib-Diagramme
erscheinen direkt unter dem Block und werden mit dem Chat gespeichert; **„In den Chat“** übernimmt die Ausgabe ins
Eingabefeld, damit das Modell den Code korrigieren oder weiterführen kann.

**Modell:** Für 12 GB VRAM passt `qwen2.5-coder:14b` (Q4, etwa 9 GB) oder schneller `qwen2.5-coder:7b`:
`ollama pull qwen2.5-coder:14b`. Andere Modelle gehen auch, schreiben aber meist schlechteren Code.

**Sandbox:** Jede Ausführung läuft in einem neuen Docker-Container (`app/sandbox.py`) mit:
kein Netzwerk (`--network none`), schreibgeschütztes Dateisystem, 1 CPU und 512 MB RAM (einstellbar), höchstens
64 Prozesse, keine Linux-Capabilities, Benutzer `nobody`, Zeitlimit 30 s (danach wird der Container beendet). Der
Code kommt über die Standardeingabe hinein; gearbeitet wird in einem kleinen RAM-Dateisystem im Container, das mit
dem Container verschwindet. Auf dem Rechner wird nichts geschrieben. Vorhanden: Python 3.12 mit numpy, pandas,
scipy, sympy, matplotlib.

**Wer darf:** standardmäßig nur Admins. Unter **Einstellungen → Code** kann ein Admin es für alle freigeben und
Zeit- und Speicherlimit ändern; dort zeigt „Sandbox prüfen“, ob alles bereit ist.

**Einrichtung (einmalig):** Die App muss Docker benutzen dürfen und das Image muss gebaut sein.

1. **Empfohlen: rootless Docker** für den Benutzer, unter dem die App läuft. Dann laufen auch die Container ohne
   Root-Rechte auf dem Rechner:
   ```bash
   sudo apt install uidmap rootlesskit
   /usr/share/docker.io/contrib/dockerd-rootless-setuptool.sh install     # als fox, ohne sudo
   sudo loginctl enable-linger fox                                          # Docker-Dienst läuft ohne Anmeldung
   ```
   Danach in `deploy/local-council.service` unter `[Service]` die Zeile
   `Environment=DOCKER_HOST=unix:///run/user/1000/docker.sock` ergänzen (1000 = `id -u fox`), den Dienst neu
   installieren (siehe Autostart) und neu starten.
2. **Einfacher, aber riskanter:** `sudo usermod -aG docker fox`. Wer Zugriff auf den Docker-Socket hat, hat
   praktisch Root-Rechte auf dem Rechner, also auch jede Lücke in der App.
3. Image bauen (braucht einmal Internet), als derselbe Benutzer:
   ```bash
   docker build -t local-council-sandbox:1 deploy/sandbox
   ```

**Risiken, ehrlich:** Der Code kommt von einem Sprachmodell, und das Modell kann über Webseiten, Dokumente oder
Nachrichten manipuliert werden. Die Sandbox begrenzt, was der Code tun kann, aber Container sind keine perfekte
Grenze: Eine Lücke im Linux-Kernel oder in Docker könnte einen Ausbruch erlauben (mit rootless Docker landet man
dann „nur“ als Benutzer fox, mit der docker-Gruppe als root). Darum: nur auf Code klicken, den du lesen kannst,
Docker und das System aktuell halten, „für alle freigeben“ nur für Leute, denen du vertraust, und den Server nicht
ins Internet stellen. Rechenintensiver Code bremst während der Laufzeit die anderen Benutzer (CPU), höchstens 2
Ausführungen laufen gleichzeitig.

### Fragen an eine Codebasis

Wissensbasen nehmen auch Quelltext: eine **ZIP-Datei** mit dem Projekt über „Dokumente hinzufügen …“ (alle
Benutzer), oder für Admins ein **Ordner auf dem Server** über „Code-Ordner …“. Ordner müssen unter Einstellungen →
Code freigegeben sein (`code.roots`); symbolische Links werden nicht verfolgt. Der Code wird nach Funktionen und
Klassen aufgeteilt (Python mit seinem Parser, andere Sprachen anhand typischer Zeilenanfänge), `node_modules`,
`.git`, `venv` usw. werden übersprungen. Die Quellen unter der Antwort zeigen Datei und Zeilen, z. B.
`gpu.py:45–83`. Denselben Ordner nochmal einlesen ersetzt die alte Fassung.

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
- Gespeichert in `data/rag/<benutzer>/<id>/` (Abschnitte als JSON, Vektoren als `.npy`, dafür wird numpy gebraucht).
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

So wird die Suche genutzt:

- Eine Suche liefert bis zu 10 Treffer (`web.max_results`) mit Titel, URL, Ausschnitt und Datum, wenn SearXNG eins
  kennt. Die Reihenfolge ist die von SearXNG; zusätzlich sieht das Modell, welche datierten Treffer die neuesten sind.
  Optional kann es den Zeitraum eingrenzen (`time_range`: Tag, Woche, Monat, Jahr).
- Bei eingeschalteter Websuche bekommt das Modell das heutige Datum und die Anweisung: bei Fragen nach
  „neueste/aktuelle“ Dingen auf das Datum der Quellen achten, bei Widersprüchen die neueste verlässliche Quelle
  nehmen, Primärquellen (offizielle Seite, GitHub Releases) bevorzugen und diese mit „Webseite lesen“ prüfen,
  statt sich auf Ausschnitte zu verlassen. Stabile Versionen und Vorabversionen soll es unterscheiden.
- Mit Websuche läuft der Chat mit 16k Tokens Kontext (`web.num_ctx`), weil Suchergebnisse und gelesene Seiten nicht
  in Ollamas Standard von 4096 passen; Ollama würde sonst den Anfang samt Anweisungen abschneiden. Das braucht etwas
  mehr Grafikspeicher (qwen3:8b: ca. 7,5 GB statt 5,6 GB). Eine eigene Kontextlänge im Chat oder unter
  Modell-Optionen hat Vorrang; `0` schaltet das ab.
- Die Antwort endet mit „Quellen:“ und den verwendeten URLs. Alle gefundenen und gelesenen Seiten stehen außerdem
  aufklappbar unter der Antwort (mit Datum, soweit bekannt).
- Die Anweisungen sind englisch und stehen oben in `app/modes/chat.py` (`DATE_PROMPT`, `WEB_PROMPT`, `TOOL_PROMPT`).

## Darstellung: Hell/Dunkel und Handy

- Der Knopf oben rechts schaltet das Farbschema um: **Auto** (wie das System) → **Hell** → **Dunkel**. Die Wahl
  gilt pro Gerät/Browser. Text und hervorgehobene Zustände haben in beiden Schemata mindestens 4,5 : 1 Kontrast.
- **Einheitliche Bedienelemente** (`static/style.css`, Abschnitt „Tokens“): Abstände nach einer Skala
  (4/8/12/16/24 px), eine Höhe für Eingabefelder, Auswahllisten und Knöpfe, drei Knopfarten (Hauptaktion gefüllt,
  normale Aktion umrandet, Symbol-Knöpfe mindestens 36 × 36 px). Sichtbare Zustände für Hover, Tastatur-Fokus
  (Fokusring) und deaktiviert.
- **Was offen ist, ist markiert:** Eintrag in der Chatliste, „+ Neuer Chat“ bzw. „+ Neuer Council“ (solange ein
  neuer, noch leerer offen ist), Titel über dem Chat und der Tab oben passen immer zusammen. Gesetzt wird das an
  einer Stelle (`renderActive()` in `static/chat.js`), sofort beim Öffnen, Anlegen, Umbenennen und Löschen.
- **Eingabezeile:** „Senden“ und „Stopp“ teilen sich einen Platz, das Textfeld springt beim Umschalten nicht.
  „⏭ Finale Antwort“ (Council) steht in der Statuszeile darüber.
- Auf schmalen Bildschirmen (Handy) liegt die Chatliste hinter „☰ Chats“, die Kopfzeile ist zweizeilig, die Tabs
  lassen sich wischen, Umbenennen/Löschen in der Liste sind immer sichtbar und groß genug zum Tippen, und
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
  "queue": { "parallel": 1 },                                 // gleichzeitige GPU-Aufträge (Admins)
  "council": { "members": [], "synthesizer": "", "rounds": 2, "parallel": false,
               "tool_steps": 3, "judge": "", "max_ctx": 16384 },  // Vorgaben für neue Councils
  "chat": {
    "model": "", "system": "", "options": {}, "think": null,   // Vorgaben für neue Chats …
    "knowledge": null, "tools": false, "web": false,           // … inkl. Wissensbasis, Werkzeuge, Websuche
    "presets": [{ "name": "…", "system": "…" }],               // System-Prompt-Vorlagen
    "auto_title": true, "title_model": ""                      // automatische Titel
  },
  "rag": { "embed_model": "bge-m3", "top_k": 5 },              // Wissensbasen
  "web": { "searxng_url": "", "max_results": 10, "num_ctx": 16384 }, // Websuche
  "code": { "allow_users": false, "model": "qwen2.5-coder:14b", "docker": "docker",
            "image": "local-council-sandbox:1", "timeout": 30, "cpus": 1, "memory_mb": 512,
            "roots": [] },                                     // Code-Modus und Code-Ordner (Admins)
  "limits": { "user_mb": 1024, "total_mb": 20480 },            // Speicher pro Benutzer / gesamt, 0 = aus
  "auth": { "secret": "" }                                     // Schlüssel für die Anmelde-Cookies
}
```

`chat`, `council` und `rag` hat jeder Benutzer für sich (gespeichert in `data/settings/<benutzer>.json`); die Werte
in `config.json` sind nur die Vorgaben für neue Benutzer. Alles andere gilt für alle und kann nur ein Admin ändern.

**Modelle sind nirgends fest einprogrammiert.** Du kannst sie jederzeit wählen:

- direkt im jeweiligen Modus. Die Auswahl gilt für die nächste Anfrage.
- mit **„Auswahl als Standard speichern“**. Damit landet die Auswahl in `config.json` und ist beim nächsten Öffnen vorausgewählt.
- durch direktes Bearbeiten von `config.json` (wirkt sofort, die Datei wird bei jeder Anfrage neu gelesen).

Der Tab **Einstellungen** bearbeitet Ollama-URL, `keep_alive`, Timeout und Modelloptionen und zeigt die installierten Modelle an.
Ist `council.synthesizer` leer, schreibt das erste Ratsmitglied die finale Antwort.

Diese Felder werden beim Tippen automatisch gespeichert. Sie überstehen also F5 und einen
Neustart und gelten auf allen Geräten im Heimnetz: Speed-Test-Prompt, Chat-System-Prompt,
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

## Benutzer, Anmeldung und Sicherheit

Mehrere Personen können die App gleichzeitig benutzen, jede mit **eigenem Konto**:

- **Eigene Daten:** Chats (`data/chats/<benutzer>/`), Wissensbasen (`data/rag/<benutzer>/`) und Einstellungen
  (Chat-Vorgaben, System-Prompt-Vorlagen, Council, Embedding-Modell; `data/settings/<benutzer>.json`). Niemand
  sieht die Chats oder Wissensbasen der anderen, auch Admins nicht.
- **Admins** stellen zusätzlich Ollama und die Websuche ein, benutzen den Speed-Test, entladen Modelle und legen
  unter **Einstellungen → Benutzer** Konten an, setzen Passwörter neu, vergeben Admin-Rechte oder löschen Konten
  (mit allen Daten). Mindestens ein Admin bleibt immer.
- **Anmelden** mit Benutzername und Passwort; jedes Gerät bleibt 30 Tage angemeldet („Abmelden“ oben rechts).
  Ein neues Passwort meldet diesen Benutzer auf allen anderen Geräten ab.
- **Umstieg von früher:** Beim ersten Start wird die alte Installation zum Konto `admin` mit dem bisherigen
  Passwort; alle vorhandenen Chats und Wissensbasen gehören dann `admin`.
- **Ohne Passwort:** Solange es nur ein Konto ohne Passwort gibt, ist die App offen (wie früher ohne Passwort).
  Weitere Benutzer lassen sich erst anlegen, wenn dieses Konto ein Passwort hat.
- Nach 10 falschen Versuchen von einer Adresse werden Anmeldungen von dort 15 Minuten lang abgelehnt.
- Gespeichert wird nur ein Hash (PBKDF2) in `data/users.json`; die API gibt ihn nie heraus.
- Passwort vergessen oder ausgesperrt: auf dem Server `.venv/bin/python -m app --set-password --user NAME`
  (ein unbekannter Name wird als neuer Admin angelegt); `--list-users` zeigt die Konten.

- **Speicherplatz:** Anhänge, Wissensbasen und Chats zählen pro Benutzer gegen `limits.user_mb` (Standard 1 GB),
  alles zusammen gegen `limits.total_mb` (20 GB); einstellbar unter Einstellungen → Benutzer. „Mein Konto“ zeigt
  den eigenen Verbrauch.
- **Anhänge** werden pro Inhalt einmal gespeichert, aber jeder, der eine Datei hochlädt, wird als Besitzer
  eingetragen. Lesen, an Chats hängen oder in eine Wissensbasis übernehmen darf eine Datei nur, wer sie selbst
  hochgeladen hat.

### Warteschlange für die GPU

Es gibt eine GPU und mehrere Benutzer. Damit Ollama nicht ständig Modelle hin- und herlädt, laufen
**Aufträge nacheinander**: eine Chat-Antwort (mit ihren Werkzeug-Aufrufen), ein ganzer Council-Lauf, ein
Speed-Test-Lauf, das Einlesen eines Dokuments, ein Chat-Titel oder „Modell laden“. Wer warten muss, sieht im Chat
„Du bist #2 in der Warteschlange“ und kann mit **Stopp** abbrechen. Wie viele Aufträge gleichzeitig laufen, stellt
ein Admin unter Einstellungen → Ollama ein („Gleichzeitige Aufträge“, Standard 1). Ein Council-Lauf zählt als ein
Auftrag; ob seine Mitglieder untereinander gleichzeitig antworten, regelt „Mitglieder gleichzeitig“ beim Council.
Höhere Werte lohnen sich nur, wenn alle dasselbe Modell nutzen und `OLLAMA_NUM_PARALLEL` entsprechend gesetzt ist.

### Isolation testen

`tests/test_isolation.py` legt in einem temporären Ordner (die echten Daten bleiben unberührt) einen Admin und
zwei Benutzer an und prüft über die API, dass Benutzer B von A nichts lesen, auflisten, exportieren, durchsuchen,
löschen oder herunterladen kann (Chats, Wissensbasen, Einstellungen, Erinnerungen, Bewertungen, Anhänge,
Einzel- und ZIP-Export, Suche), dass normale Benutzer auf alle Admin-Routen 403 bekommen, dass Passwort-Reset und
Löschen eines Kontos dessen Sitzungen beenden, die Login-Sperre pro Adresse und pro Konto, die Speichergrenzen
und dass „Webseite lesen“ nur erlaubte Adressen abruft. Braucht kein Ollama:

```bash
.venv/bin/python tests/test_isolation.py
```

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

- **Kein HTTPS:** Passwort und Cookie gehen im Heimnetz unverschlüsselt über die Leitung.
- **Chats speichert der Browser:** Wird ein Tab mitten in einer Antwort geschlossen, ist die Frage gespeichert, die
  Teilantwort nicht. Bearbeiten zwei Geräte gleichzeitig denselben Chat, gewinnt der zuletzt gespeicherte Stand.
- **Kontext-Anzeige ist geschätzt** (≈ 3,5 Zeichen pro Token). Ohne eigene Einstellung nutzt Ollama nur 4096
  Tokens, längere Chats und Dokumente werden dann vorne abgeschnitten.
- **PDFs:** nur mit Textebene, keine Texterkennung (OCR) für eingescannte Seiten; Tabellen und Spalten kommen als
  Fließtext an.
- **Wissensbasen:** einfache Abschnitte fester Größe, keine Layout-Erkennung. Eine Wissensbasis bleibt bei dem
  Embedding-Modell, mit dem sie angelegt wurde; zum Wechseln neu anlegen.
- **Werkzeuge:** nur mit Modellen, die Tool-Calling können, und kleine Modelle nutzen sie nicht immer (rechnen
  dann z. B. selbst). Höchstens 5 Werkzeug-Aufrufe pro Antwort.
- **Websuche-Qualität** hängt am Modell: Auch mit den Anweisungen liest ein 8B-Modell manchmal die falsche Seite
  oder verlässt sich doch auf einen veralteten Ausschnitt. Die Quellenliste unter der Antwort zeigt, worauf es
  sich stützt. Viele Webseiten liefern SearXNG kein Datum.
- **Websuche** braucht eine eigene SearXNG-Instanz.
- **„Webseite lesen“** liest nur Adressen aus Suchergebnissen derselben Anfrage oder solche, die du selbst
  geschrieben hast (gegen das Herausschicken von Chat-Inhalten über präparierte Links). Suchergebnisse früherer
  Nachrichten zählen nicht; dann sucht das Modell erneut. Die Adresse wird einmal aufgelöst, geprüft und genau
  diese IP verbunden (kein DNS-Rebinding).
- **Council:** Bewertungen durch kleine Modelle schwanken stark. Mehrere Mitglieder auf einer 12-GB-GPU heißt
  meist: nacheinander laden; mit verschiedenen großen Modellen dauert ein Lauf entsprechend.
- **Code-Modus** braucht Docker-Zugriff für die App und das gebaute Image (siehe Code-Modus).
- **Formeln:** KaTeX kann nicht alles, was LaTeX kann (z. B. keine eigenen Pakete, kein TikZ). Die Thinking-Ausgabe
  wird als reiner Text gezeigt.
- **Speed-Test:** Gleichzeitige Läufe teilen sich GPU und CPU; Tokens/s sind dann nicht mit Einzelläufen vergleichbar.

## Council-Prompts anpassen

Die Prompts für Diskussion und Zusammenfassung stehen als Konstanten oben in `app/modes/council.py`
(`SYSTEM_PROMPT`, `ROLE_PROMPT`, `DISCUSSION_PROMPT`, `SYNTHESIS_PROMPT`), die Rollen-Vorlagen in
`static/council.js` (`ROLE_PRESETS`), der Richter-Prompt in `app/judge.py`. Sie sind auf Englisch, weil das bei kleinen
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
│   ├── auth.py          # Passwort-Hashes, signierte Cookies, Host-Prüfung
│   ├── users.py         # Benutzerkonten, Einstellungen pro Benutzer, Umstieg vom alten Passwort
│   ├── storage.py       # wer welche Anhänge lesen darf, Speichergrenzen
│   ├── gpu.py           # Warteschlange für GPU-Aufträge
│   ├── memory.py        # Erinnerungen pro Benutzer, Vorschläge aus Antworten
│   ├── judge.py         # Bewertung von Council-Antworten
│   ├── benchmark.py     # Benchmark Council gegen Einzelmodell (Fragen, Läufe im Hintergrund, Richter, Stimmen)
│   ├── sandbox.py       # Code-Modus: Python im Docker-Container
│   ├── codechunks.py    # Quelltext für Wissensbasen (ZIP/Ordner, nach Funktionen aufgeteilt)
│   ├── rag.py           # Wissensbasen: Abschnitte, Embeddings, Suche
│   ├── tools.py         # Werkzeuge für Tool-Calling (Rechner, Datum, Web)
│   ├── websearch.py     # SearXNG-Suche, Webseiten als Text (nur öffentliche Adressen)
│   └── modes/
│       ├── runner.py    # gemeinsamer Baustein: Jobs parallel/seriell ausführen, Streams zusammenführen
│       ├── speedtest.py # Modus 1
│       ├── agent.py     # ein Modell antwortet, mit Werkzeug-Schleife (Chat und Council-Mitglieder)
│       ├── council.py   # Council: Rollen, Runden, geteilte Recherche, Kontextprüfung, Überspringen, Bewertung
│       └── chat.py      # Chat: Wissensbasis und Websuche vorbereiten, dann agent
├── static/              # Frontend: reines HTML/CSS/JS, kein Build-Schritt
│   ├── index.html
│   ├── style.css
│   ├── core.js          # gemeinsame Helfer: API, NDJSON-Stream, Autosave, Kopieren
│   ├── theme-init.js    # Farbschema vor dem ersten Zeichnen setzen
│   ├── theme.js         # Hell/Dunkel/Auto
│   ├── auth.js          # Anmeldung, eigenes Passwort, Benutzerverwaltung
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
│   ├── council.js       # Council-Sitzungen im Chat: Mitglieder/Rollen, Runden-Ansicht, Bewertung
│   ├── memory.js        # Erinnerungen: Einstellungen, Vorschläge unter Antworten
│   ├── code.js          # Code-Modus: Ausführen-Knopf, Ausgabe, Sandbox-Einstellungen, Code-Ordner
│   ├── benchmark.js     # Benchmark-Tab: Lauf einrichten, Fragen bearbeiten, Ergebnis, selbst bewerten
│   ├── app.js           # Speed-Test, Einstellungen, Start
│   └── vendor/          # marked, DOMPurify, highlight.js, KaTeX (unverändert, mit Lizenz-Hinweis)
├── data/                # Konten, Messdaten, Chats, Anhänge, Wissensbasen, nicht im Git
├── deploy/
│   ├── local-council.service  # systemd-Dienst für den Autostart
│   └── sandbox/Dockerfile     # Image für den Code-Modus
├── tests/
│   └── test_isolation.py      # Isolation zwischen Benutzern, Admin-Rechte, Sitzungen, Speichergrenzen
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
| POST | `/api/models/load`, `/api/models/unload` | `{model}` → Modell laden / sofort entladen (Entladen: Admin) |
| POST | `/api/models/show` | `{model}` → Fähigkeiten (`vision`, `thinking`, `tools`) und maximale Kontextlänge |
| GET / PUT | `/api/config` | Konfiguration lesen / speichern (`chat`, `council`, `rag` pro Benutzer, der Rest nur Admin) |
| POST | `/api/speedtest` | `{prompt, models?, parallel?}` → NDJSON-Stream (Admin) |
| GET | `/api/speedtest/history` | alle gespeicherten Speed-Test-Messungen (Admin) |
| POST | `/api/speedtest/history/prune` | `{keep: 3}` → behält pro Modell und Modus nur die neuesten `keep` Messungen (Admin) |
| POST | `/api/council` | `{messages, members: [{model, role, system}], synthesizer?, rounds, parallel, tools, web, tool_steps, knowledge?, judge?, memory}` → NDJSON-Stream |
| POST | `/api/council/{run}/skip` | Diskussion beenden, finale Antwort schreiben lassen |
| GET | `/api/judge` | eigene Council-Bewertungen |
| GET / POST / PUT | `/api/memory` | Erinnerungen lesen / `{text}` hinzufügen / `{enabled}` an/aus |
| PATCH / DELETE | `/api/memory/{id}` | Erinnerung ändern / löschen |
| GET | `/api/code` | darf ich Code ausführen, ist die Sandbox bereit? |
| POST | `/api/code/run` | `{code}` → `{stdout, stderr, error, images, seconds, timed_out}` |
| POST | `/api/rag/{id}/folder` | `{path}` Code-Ordner einlesen (Admin) → NDJSON-Fortschritt |
| GET | `/api/queue`, `/api/storage` | Auslastung der Warteschlange; eigener Speicherverbrauch |
| GET / PUT | `/api/benchmark/questions` | Fragenkatalog lesen / speichern (Admin) |
| GET / POST | `/api/benchmark/runs` | Läufe auflisten / `{baseline, members, synthesizer, rounds, parallel, judge}` anlegen und starten (Admin) |
| GET / DELETE | `/api/benchmark/runs/{id}` | Lauf mit Antworten, Fortschritt und Zusammenfassung / löschen (Admin) |
| POST | `/api/benchmark/runs/{id}/start`, `…/stop`, `…/vote` | fortsetzen, stoppen, `{qid, label: A\|B\|tie}` selbst bewerten (Admin) |
| POST | `/api/chat` | `{messages: [{role, content, attachments?}], model?, system?, options?, think?, knowledge?, tools?, web?, memory?, code?}` → NDJSON-Stream |
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
| POST | `/api/web/test` | SearXNG-Verbindung prüfen (Admin) |
| GET / POST | `/api/auth`, `/api/login`, `/api/logout` | Anmeldestatus und Benutzer, `{username, password}` anmelden, abmelden |
| PUT | `/api/auth/password` | `{current, password}` eigenes Passwort ändern |
| GET / POST | `/api/users` | Konten auflisten / `{name, password, admin}` anlegen (Admin) |
| PATCH / DELETE | `/api/users/{name}` | `{password?, admin?}` ändern / Konto mit allen Daten löschen (Admin) |

Chats und Wissensbasen gehören immer dem angemeldeten Benutzer. Felder mit `?` fallen auf seine Einstellungen zurück. Beispiel:

```bash
curl -N http://localhost:8080/api/speedtest -H 'Content-Type: application/json' \
  -d '{"prompt": "Hallo!", "models": ["llama3.2:3b", "qwen3:4b"], "parallel": false}'
```
