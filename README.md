# GardenGlow

GardenGlow ist eine Progressive Web App (PWA) zur Verwaltung eines Gartenkatalogs.
Die Anwendung verwaltet Pflanzorte, Pflanzen, Fotos und Kommentare – mit **ausschließlicher Anmeldung über OIDC (OpenID Connect)**.

## Funktionsbeschreibung

GardenGlow ist für den Betrieb in Containern ausgelegt und speichert alle Daten persistent in einem Volume.
Nach dem Start meldet sich der Benutzer über einen externen OIDC-Provider an (kein lokaler Benutzername/Passwort-Login).
Nach erfolgreicher Anmeldung können Gartenbereiche (Pflanzorte) angelegt und darin Pflanzen verwaltet werden.
Zu Pflanzen lassen sich Fotos mit Datum und Kommentar sowie reine Textkommentare hinterlegen.
Zusätzlich ist die Anwendung als installierbare PWA nutzbar und enthält einen Healthcheck-Endpunkt für Monitoring.

## Features

- OIDC-Login (OpenID Connect) als **einziger** Authentifizierungsweg
- Benutzerprofil mit Name, E-Mail und Avatar (Avatar-Download vom OIDC-Profilbild)
- Verwaltung von Pflanzorten und zugeordneten Pflanzen
- Foto-Uploads inkl. Datum und Beschreibung
- Kommentare auch ohne Foto möglich
- Installierbare PWA (inkl. Web App Manifest / Service Worker)
- Hell-/Dunkelmodus
- Reverse-Proxy-tauglich durch `ProxyFix`
- Healthcheck unter `/healthz`

## Start mit Docker Compose

### `docker-compose.yml`

```yaml
services:
  gardenglow:
    build: https://github.com/doenke/garten.git#main
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
      WIDGET_API_KEY: change-this-api-key
      DEBUG_MODE: "false"
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

### Start

```bash
docker compose up --build
```

## Wichtige Umgebungsvariablen

### Allgemein

- `SECRET_KEY` – Flask Secret Key (Sessions/Signaturen)
- `DATABASE_URL` – SQLAlchemy-Datenbankverbindung (z. B. SQLite in `/data`)
- `UPLOAD_FOLDER` – Verzeichnis für hochgeladene Pflanzenfotos
- `MAX_ATTACHMENT_SIZE_BYTES` – Maximalgröße pro Dateiupload (Standard: `15728640` = 15 MiB)
- `AVATAR_FOLDER` – Verzeichnis für lokal gespeicherte Benutzer-Avatare
- `MAP_FOLDER` – Verzeichnis für Karten-/Lageplan-Dateien
- `WIDGET_API_KEY` – API-Key für den Statistik-Webservice `/api/stats`
- `HEADER_LOGO_URL` – URL eines optionalen Header-Logos (wenn leer oder nicht gesetzt, wird kein Logo angezeigt)
- `DEBUG_MODE` – aktiviert mit `1`, `true`, `yes`, `on` oder `y` das vollständige Magic-/Taxonomie-Debugging. Ohne aktivierten Debug-Modus werden auf der Pflanzenseite keine Magic-Debug-Hinweise und kein Magic-Debuglog angezeigt. Bei aktivem Debug enthält die JSON-Antwort zusätzlich alle externen Webanfragen samt Headern, Status und vollständigem Antwortinhalt. Standard: `false`.

### OIDC (Pflicht für Login)

- `OIDC_SERVER_METADATA_URL` – URL zur OIDC Discovery (`.well-known/openid-configuration`)
- `OIDC_CLIENT_ID` – OIDC Client-ID
- `OIDC_CLIENT_SECRET` – OIDC Client-Secret
- `OIDC_LOGOUT_URL` *(optional)* – Externe Logout-URL des Identity-Providers

> Hinweis: Ohne korrekt gesetzte OIDC-Variablen ist keine Anmeldung möglich, da GardenGlow keinen lokalen Passwort-Login anbietet.

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
