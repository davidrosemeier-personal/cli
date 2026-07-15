# Deployment

Es gibt zwei unterstützte Wege, die App bereitzustellen:

## Option A: Railway (oder andere Single-Container-Plattformen)

Plattformen wie Railway bauen das `Dockerfile` direkt und stellen selbst
Domain + TLS bereit — der Caddy-Reverse-Proxy aus Option B kommt dabei
**nicht** zum Einsatz.

- **Port:** `8501` (Streamlit-Standardport, im Dockerfile per
  `--server.port=8501` gesetzt)
- **Login:** Da kein Caddy/Basic-Auth-Layer davor läuft, übernimmt die App
  selbst den Passwortschutz. Environment-Variable `APP_PASSWORD` in den
  Railway-Projekteinstellungen setzen — ohne diese Variable ist die App
  ungeschützt erreichbar. `ASSEMBLYAI_API_KEY` genauso als Variable setzen.
- Persistenz: Railway-Dateisystem ist standardmäßig **nicht** dauerhaft
  über Deploys hinweg (außer mit einem Volume). Ohne Volume gehen
  `known_names.json` und Caches bei jedem Redeploy verloren — für reine
  Nutzung ohne Namens-Wiederverwendung reicht das, für dauerhafte Historie
  ein Railway-Volume auf `/app/uploads` mounten.

## Option B: Eigener VPS mit Docker Compose + Caddy

Deployt die App als Docker-Container hinter einem Caddy-Reverse-Proxy, der
automatisch ein TLS-Zertifikat besorgt (Let's Encrypt) und den Zugriff per
Basic Auth schützt.

### Voraussetzungen

- Ein VPS (z.B. Hetzner) mit Docker + Docker Compose Plugin installiert
- Eine Domain (oder Subdomain), deren DNS-A-Record auf die Server-IP zeigt
- Ports 80 und 443 auf dem Server offen (für Let's Encrypt + HTTPS)

### Einmalige Einrichtung auf dem Server

1. Repo klonen:
   ```
   git clone git@github.com:davidrosemeier-personal/cli.git /opt/plaud-transkript
   cd /opt/plaud-transkript
   ```

2. `.env` aus der Vorlage erstellen:
   ```
   cp .env.template .env
   ```
   Und ausfüllen:
   - `ASSEMBLYAI_API_KEY` — dein AssemblyAI-Key
   - `DOMAIN` — z.B. `meetings.deine-domain.de`
   - `BASIC_AUTH_USER` — Login-Benutzername
   - `BASIC_AUTH_HASH` — Passwort-Hash, erzeugt mit:
     ```
     docker run --rm caddy:2-alpine caddy hash-password --plaintext 'DEIN_PASSWORT'
     ```

3. Starten:
   ```
   docker compose up -d --build
   ```
   Caddy holt beim ersten Start automatisch ein Let's-Encrypt-Zertifikat für
   `DOMAIN`. Das dauert ein paar Sekunden; Logs prüfen mit
   `docker compose logs -f caddy`.

4. Aufrufen: `https://<DOMAIN>` — Browser fragt nach Benutzername/Passwort
   (Basic Auth), danach erscheint die App.

### Laufende Updates (automatisch per Git-Push)

Der Workflow [`.github/workflows/deploy.yml`](.github/workflows/deploy.yml) deployt bei jedem Push auf `main`
automatisch neu. Dafür in den GitHub-Repo-Settings unter **Settings → Secrets
and variables → Actions** folgende Secrets anlegen:

| Secret | Wert |
|---|---|
| `SSH_HOST` | IP-Adresse des Servers |
| `SSH_USER` | SSH-Benutzer auf dem Server |
| `SSH_KEY` | Privater SSH-Key mit Zugriff auf den Server (nicht der GitHub-Key!) |
| `SSH_PORT` | SSH-Port, falls nicht 22 (optional) |
| `DEPLOY_PATH` | Pfad des Repos auf dem Server, z.B. `/opt/plaud-transkript` |

Ohne diese Secrets bleibt der Workflow inaktiv — manuelles Update auf dem
Server per `git pull && docker compose up -d --build` funktioniert immer.

### Daten & Persistenz

Alles, was die App an Nutzdaten erzeugt (hochgeladene Audiodateien, Caches,
Transkripte, `known_names.json`), landet im Ordner `./data` auf dem Server
(Docker-Volume-Mount). Für ein Backup reicht es, diesen Ordner zu sichern.

## Bekannte Einschränkungen (aktueller Stand)

- **Beide Login-Varianten (Basic Auth via Caddy, `APP_PASSWORD` in der App)
  sind ein gemeinsames Login** für alle Nutzer — es gibt keine Trennung nach
  Person. Für den Anfang (du + wenige Kolleg:innen) ist das ok; bei mehr
  Nutzern wäre ein Umstieg auf OIDC (`st.login()` mit Google/Entra) und eine
  Trennung der Daten pro Nutzer sinnvoll.
- **Keine automatische Löschung** hochgeladener Audiodateien. Da es sich um
  dienstliche Meeting-Aufnahmen handelt, lohnt sich vorher ein kurzer
  Datenschutz-Check (Speicherdauer, AssemblyAI-Verarbeitung außerhalb der
  EU) mit eurem Datenschutzbeauftragten.
- Der Docker-Build wurde lokal nicht getestet (kein Docker auf diesem Mac
  installiert) — beim ersten Deploy auf dem Server also `docker compose logs`
  im Blick behalten.
