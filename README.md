# GardenGlow

GardenGlow ist eine Progressive Web App (PWA) zur Verwaltung eines Gartenkatalogs.
Die Anwendung verwaltet Pflanzorte, Pflanzen, Fotos und Kommentare – wahlweise mit OIDC (OpenID Connect) oder ohne OIDC automatisch mit dem Standardbenutzer **„Gärtner“**.

## Funktionsbeschreibung

GardenGlow ist für den Betrieb in Containern ausgelegt und speichert alle Daten persistent in einem Volume.
Nach dem Start meldet sich der Benutzer bei gesetzter OIDC-Konfiguration über einen externen OIDC-Provider an. Ohne OIDC-Variablen wird automatisch der Standardbenutzer **„Gärtner“** verwendet; ein gesonderter Login ist dann nicht nötig.
Nach erfolgreicher Anmeldung können Gartenbereiche (Pflanzorte) angelegt und darin Pflanzen verwaltet werden.
Zu Pflanzen lassen sich Fotos mit Datum und Kommentar sowie reine Textkommentare hinterlegen.
Zusätzlich ist die Anwendung als installierbare PWA nutzbar und enthält einen Healthcheck-Endpunkt für Monitoring.

## Features

- OIDC-Login (OpenID Connect) bei gesetzter OIDC-Konfiguration
- Automatischer Standardbenutzer **„Gärtner“**, wenn keine OIDC-Variablen gesetzt sind
- Benutzerprofil mit Name, E-Mail und Avatar (Avatar-Download vom OIDC-Profilbild)
- Verwaltung von Pflanzorten und zugeordneten Pflanzen
- Foto-Uploads inkl. Datum und Beschreibung
- Kommentare auch ohne Foto möglich
- Installierbare PWA (inkl. Web App Manifest / Service Worker)
- Hell-/Dunkelmodus
- Reverse-Proxy-tauglich durch `ProxyFix`
- Healthcheck unter `/healthz`

## Start mit Docker Compose

`docker-compose.yml` nutzt standardmäßig das veröffentlichte Container-Image aus GitHub Container Registry.
Über `GARDENGLOW_VERSION` kann ein konkreter Release-Tag gewählt werden; ohne Variable wird `latest` verwendet.

### `docker-compose.yml`

```yaml
services:
  gardenglow:
    image: ghcr.io/doenke/gardenglow:${GARDENGLOW_VERSION:-latest}
    container_name: gardenglow
    restart: unless-stopped
    environment:
      SECRET_KEY: changeme
      DATABASE_URL: sqlite:////data/garden.db
      UPLOAD_FOLDER: /data/uploads
      OIDC_SERVER_METADATA_URL: https://example.com/.well-known/openid-configuration
      OIDC_CLIENT_ID: change-me
      OIDC_CLIENT_SECRET: change-me
      OIDC_LOGOUT_URL: https://example.com/logout
    volumes:
      - gardenglow_data:/data
    ports:
      - "8000:8000"
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')"]
      interval: 30s
      timeout: 3s
      retries: 3
volumes:
  gardenglow_data:
```

### Start mit Release-Image

Konkreten Release-Tag nutzen:

```bash
GARDENGLOW_VERSION=1.2.3 docker compose up -d
```

Aktuelles `latest`-Image nutzen:

```bash
docker compose up -d
```

### Lokal aus dem ausgecheckten Repo bauen

Für lokale Builds ergänzt `docker-compose.build.yml` den bestehenden Service um `build: .`:

```bash
docker compose -f docker-compose.yml -f docker-compose.build.yml up --build
```

### Optionaler Remote-Build aus dem GitHub-Repository

Für den bisherigen Remote-Build-Komfort ergänzt `docker-compose.remote-build.yml` den bestehenden Service um den GitHub-Build-Kontext:

```bash
docker compose -f docker-compose.yml -f docker-compose.remote-build.yml up --build
```

## Releases / Container Images

Die veröffentlichten Container-Images werden über Tags versioniert:

- `latest` steht für den neuesten stabilen Release.
- `1.2.3` steht exakt für diesen Release.
- `1.2` kann optional als Minor-Tag verwendet werden und steht dann für den neuesten Patch der Minor-Version.
- `edge` oder `main` können optional als Entwicklungs-Tags verwendet werden und stehen dann für den aktuellen Entwicklungsstand.

Für produktive Deployments wird empfohlen, eine konkrete Version zu pinnen:

```bash
GARDENGLOW_VERSION=1.2.3 docker compose up -d
```

Produktive Systeme sollten nach Möglichkeit eine konkrete Version wie `1.2.3` statt `latest` verwenden, damit Updates kontrolliert und reproduzierbar erfolgen.

Update auf den neuesten stabilen Release:

```bash
docker compose pull
docker compose up -d
```

Lokaler Build aus dem ausgecheckten Repository:

```bash
docker compose -f docker-compose.yml -f docker-compose.build.yml up --build
```

## Wichtige Umgebungsvariablen

### Allgemein

- `SECRET_KEY` – Flask Secret Key (Sessions/Signaturen)
- `DATABASE_URL` – SQLAlchemy-Datenbankverbindung (z. B. SQLite in `/data`)
- `UPLOAD_FOLDER` – Verzeichnis für hochgeladene Pflanzenfotos
- `MAX_ATTACHMENT_SIZE_BYTES` – Maximalgröße pro Dateiupload (Standard: `15728640` = 15 MiB)
- `AVATAR_FOLDER` – Verzeichnis für lokal gespeicherte Benutzer-Avatare
- `MAX_AVATAR_SIZE_BYTES` – Maximalgröße für vom OIDC-Profil heruntergeladene Avatare (Standard: `5242880` = 5 MiB)
- `MAP_FOLDER` – Verzeichnis für Karten-/Lageplan-Dateien
- `BACKUP_FOLDER` – Verzeichnis, aus dem die Admin-/Wartungsseite die letzten Backups anzeigt (Standard: `/data/backups`)
- `APP_VERSION` – optionale Versionsanzeige auf der Admin-/Wartungsseite
- `GIT_COMMIT` – optionaler Git-Commit für die Admin-/Wartungsseite; wenn leer, wird der aktuelle Commit per `git rev-parse --short HEAD` ermittelt
- `WIDGET_API_KEY` – API-Key für den Statistik-Webservice `/api/stats`
- `HEADER_LOGO_URL` – URL eines optionalen Header-Logos (wenn leer oder nicht gesetzt, wird kein Logo angezeigt)
- `DEBUG_MODE` – aktiviert mit `1`, `true`, `yes`, `on` oder `y` das vollständige Magic-/Taxonomie-Debugging. Ohne aktivierten Debug-Modus werden auf der Pflanzenseite keine Magic-Debug-Hinweise und kein Magic-Debuglog angezeigt. Bei aktivem Debug enthält die JSON-Antwort zusätzlich alle externen Webanfragen samt Headern, Status und vollständigem Antwortinhalt. Standard: `false`.
- `IRRIGATION_PREDICTION_MAX_MINUTES` – Obergrenze für ML-Bewässerungsprognosen in Minuten (Standard: `120`). Negative Modellwerte werden zu `0`, Werte oberhalb der Obergrenze werden auf diese Obergrenze beschränkt.
- `IRRIGATION_PREDICTION_TRAIN_INTERVAL_DAYS` – Mindestabstand zwischen zwei Trainingsläufen je Beet in Tagen (Standard: `7`).
- `IRRIGATION_PREDICTION_TRAINING_LOOKBACK_DAYS` – Historischer Sensorzeitraum für das Modelltraining in Tagen (Standard und Maximum: `900`). Pro Beet beginnt das Training frühestens beim ersten verfügbaren Datenpunkt eines zugeordneten Feuchtesensors.
- `IRRIGATION_PREDICTION_TRAIN_CRON_TIME` – tägliche Uhrzeit, zu der fällige Bewässerungs-Prognosemodelle automatisch geprüft und bei Bedarf neu trainiert werden (Format `HH:MM`, Standard: `03:00`).
- `IRRIGATION_PREDICTION_TRAIN_CRON_ENABLED` – aktiviert (`true`) oder deaktiviert (`false`) den täglichen Trainings-Cronjob (Standard: `true`).

### OIDC (optional)

- `OIDC_SERVER_METADATA_URL` – URL zur OIDC Discovery (`.well-known/openid-configuration`)
- `OIDC_CLIENT_ID` – OIDC Client-ID
- `OIDC_CLIENT_SECRET` – OIDC Client-Secret
- `OIDC_LOGOUT_URL` *(optional)* – Externe Logout-URL des Identity-Providers

> Hinweis: Wenn keine der OIDC-Variablen gesetzt ist, startet GardenGlow ohne externen Login und meldet automatisch den Standardbenutzer **„Gärtner“** an. Sobald mindestens eine OIDC-Variable gesetzt ist, müssen `OIDC_SERVER_METADATA_URL`, `OIDC_CLIENT_ID` und `OIDC_CLIENT_SECRET` vollständig vorhanden sein.


## Webservice für Bewässerungs-Prognosen

GardenGlow stellt ML-basierte JSON-Endpunkte bereit, die aus den vorhandenen Sensor-Zeitreihen je Beet vorhersagen, wie viele Minuten die Bewässerung heute laufen sollte, um die Ziel-Bodenfeuchte zu erreichen. Ein täglicher Cronjob prüft standardmäßig um `03:00` Uhr, ob Modelle fällig sind, und trainiert sie bei Bedarf neu. Die Uhrzeit kann über `IRRIGATION_PREDICTION_TRAIN_CRON_TIME` im Format `HH:MM` geändert werden. Zusätzlich wird das Training pro Beet beim Abruf weiterhin automatisch ausgeführt, falls noch kein Modell vorhanden ist oder das letzte Training mindestens eine Woche zurückliegt.

Endpunkte:

- `GET /api/irrigation-predictions` – Prognosen für alle produktiven Beete
- `GET /api/locations/<id>/irrigation-prediction` – Prognose für ein einzelnes Beet

Authentifizierung entspricht `/api/stats`:

- Header `X-API-Key: <WIDGET_API_KEY>`
- alternativ `Authorization: Bearer <WIDGET_API_KEY>`

Die Antwort enthält unter anderem `predicted_minutes`, `raw_predicted_minutes`, `target_soil_moisture_percent`, Modell-Metadaten und die für die aktuelle Prognose verwendeten Features. Negative Vorhersagen werden auf `0` Minuten gesetzt; die Obergrenze wird auf der Konfigurationsseite oder über `IRRIGATION_PREDICTION_MAX_MINUTES` konfiguriert und beträgt standardmäßig `120` Minuten.

## Webservice für Pflanzen-/Beet-Zahlen

Es gibt einen zusätzlichen JSON-Endpunkt unter `GET /api/stats`, der folgende Werte ausgibt:

- `plants`: Anzahl aller Pflanzen
- `beds`: Anzahl aller Beete/Pflanzorte (ohne den internen Papierkorb)
- `uploads`: Anzahl aller hochgeladenen Dateien im Upload-Verzeichnis
- `upload_size_bytes`: Gesamtgröße aller Uploads in Byte
- `database_size_bytes`: Größe der SQLite-Datenbankdatei in Byte

Authentifizierung:
- Per Header `X-API-Key: <WIDGET_API_KEY>`
- alternativ `Authorization: Bearer <WIDGET_API_KEY>`

Wenn `WIDGET_API_KEY` nicht gesetzt ist, antwortet der Endpunkt mit `503`.

### Beispiel für gethomepage Custom API Widget

Beispielkonfiguration für `services.yaml` in gethomepage:

```yaml
- Garten:
        description: Pflanzen & Beete
        icon: mdi-flower
        href: https://gardenglow.example.com
        widget:
          type: customapi
          url: https://gardenglow.example.com/api/stats
          method: GET
          headers:
            X-API-Key: "{{HOMEPAGE_VAR_GARTEN_API_KEY}}"
          mappings:
            - field: plants
              label: Pflanzen
              format: number
            - field: beds
              label: Beete
              format: number
            - field: uploads
              label: Uploads
              format: number
            - field: upload_size_bytes
              label: Uploadgröße
              format: bytes
            - field: database_size_bytes
              label: DB-Größe
              format: bytes
```

Tipp: Lege den Key in gethomepage als Umgebungsvariable ab (z. B. `HOMEPAGE_VAR_GARTEN_API_KEY`) und hinterlege ihn nicht im Klartext in der YAML.
## Setup / Deployment

### Pflichtvariable `SECRET_KEY`

`SECRET_KEY` ist beim App-Start verpflichtend und wird **ohne Default** gelesen.

Anforderungen:
- gesetzt (nicht leer)
- kein offensichtlicher Placeholder (z. B. `dev-secret-change-me`, `changeme`, `secret`)
- mindestens 32 Zeichen

Beispiel (lokal):

```bash
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
```

Wenn `SECRET_KEY` fehlt oder zu schwach ist, bricht die App mit einer klaren Konfigurations-Exception beim Start ab.


## Umgebungsvariablen

- `COMMON_NAME_LOOKUP_LANG` steuert die Sprache für die automatische Suche des „Bürgerlichen Namens“ über Wikipedia. Standard: `de` (Deutsch).
- `DEBUG_MODE` schaltet das vollständige Magic-/Taxonomie-Debugging ein oder aus. Standardmäßig ist es aus; dann blendet die Pflanzenseite Magic-Debug-Hinweise und Magic-Debuglog vollständig aus. Bei aktivem Wert (`1`, `true`, `yes`, `on`, `y`) werden externe Webanfragen inklusive komplettem Response-Content im Debug-Block der JSON-Antwort ausgegeben.
