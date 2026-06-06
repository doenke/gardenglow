# GardenGlow

GardenGlow ist eine Progressive Web App (PWA) zur Verwaltung eines Gartenkatalogs.
Die Anwendung verwaltet Pflanzorte, Pflanzen, Fotos und Kommentare – wahlweise mit OIDC (OpenID Connect) oder ohne OIDC automatisch mit dem Standardbenutzer **„Gärtner“**.

## Inhalt

- [Schnellstart](#schnellstart)
- [Features](#features)
- [Konfiguration](#konfiguration)
- [Authentifizierung](#deployment-mit-oidc)
- [Deployment und Betrieb](#setup--deployment)
- [API-Endpunkte](#webservice-für-bewässerungs-prognosen)
- [Entwicklung](#lokal-aus-dem-ausgecheckten-repo-bauen)

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

## Schnellstart

`docker-compose.yml` nutzt standardmäßig das veröffentlichte Container-Image aus GitHub Container Registry.
Über `GARDENGLOW_VERSION` kann ein konkreter Release-Tag gewählt werden; ohne Variable wird `latest` verwendet.

Erzeuge zuerst einen sicheren `SECRET_KEY`:

```bash
python -c 'import secrets; print(secrets.token_urlsafe(48))'
```

Minimales `docker-compose.yml` ohne OIDC-Variablen:

```yaml
services:
  gardenglow:
    image: ghcr.io/doenke/gardenglow:${GARDENGLOW_VERSION:-latest}
    container_name: gardenglow
    restart: unless-stopped
    environment:
      SECRET_KEY: hier-den-generierten-secret-key-einfuegen
      DATABASE_URL: sqlite:////data/garden.db
      UPLOAD_FOLDER: /data/uploads
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

Ohne OIDC-Variablen startet GardenGlow automatisch mit dem Standardbenutzer **„Gärtner“**; weitere Details findest du unter [Betrieb ohne OIDC](#betrieb-ohne-oidc). Datenbank und Uploads liegen persistent im Docker-Volume `gardenglow_data`. Nach dem Start mit `docker compose up -d` erreichst du die App unter `http://localhost:8000`.


## Authentifizierung

GardenGlow kann entweder ohne externen Identity-Provider oder mit OIDC betrieben werden. Welche Variante aktiv ist, hängt ausschließlich von den gesetzten OIDC-Umgebungsvariablen ab.

### Betrieb ohne OIDC

Setze für diesen Betriebsmodus keine OIDC-Variablen. Wenn keine OIDC-Variablen vorhanden sind, startet GardenGlow ohne externen Login und meldet automatisch den Standardbenutzer **„Gärtner“** an.

Dieser Modus eignet sich für private Installationen oder Umgebungen, die bereits durch einen Reverse Proxy, ein VPN oder eine vergleichbare vorgelagerte Zugriffskontrolle geschützt sind.

### Betrieb mit OIDC

Für den Betrieb mit OIDC müssen die folgenden Pflichtvariablen vollständig gesetzt sein:

- `OIDC_SERVER_METADATA_URL`
- `OIDC_CLIENT_ID`
- `OIDC_CLIENT_SECRET`

Optional kann zusätzlich `OIDC_LOGOUT_URL` gesetzt werden, um auf eine externe Logout-URL des Identity-Providers zu verweisen.

Sobald mindestens eine OIDC-Variable gesetzt ist, müssen die Pflichtvariablen `OIDC_SERVER_METADATA_URL`, `OIDC_CLIENT_ID` und `OIDC_CLIENT_SECRET` vollständig vorhanden sein. Unvollständige OIDC-Konfigurationen werden nicht als Betrieb ohne OIDC interpretiert.

## Deployment mit OIDC

Wenn GardenGlow mit einem externen OIDC-Provider betrieben werden soll, müssen `OIDC_SERVER_METADATA_URL`, `OIDC_CLIENT_ID` und `OIDC_CLIENT_SECRET` vollständig gesetzt werden. `OIDC_LOGOUT_URL` ist optional.

Beispiel für `docker-compose.yml` mit OIDC; beachte dazu die Hinweise unter [Betrieb mit OIDC](#betrieb-mit-oidc):

```yaml
services:
  gardenglow:
    image: ghcr.io/doenke/gardenglow:${GARDENGLOW_VERSION:-latest}
    container_name: gardenglow
    restart: unless-stopped
    environment:
      SECRET_KEY: hier-den-generierten-secret-key-einfuegen
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

## Deployment und Betrieb

### Container-Images

Die veröffentlichten Container-Images liegen in der GitHub Container Registry und werden über Tags versioniert:

- `1.2.3` steht exakt für diesen Release und ist der empfohlene Produktivpfad, weil Deployments damit kontrolliert und reproduzierbar bleiben.
- `1.2` kann optional als Minor-Tag verwendet werden und steht dann für den neuesten Patch der Minor-Version.
- `latest` steht für den neuesten stabilen Release. Verwende `latest` in Produktion nur bewusst, wenn automatische Wechsel auf neue Releases gewünscht sind.
- `edge` oder `main` können optional als Entwicklungs-Tags verwendet werden und stehen für den aktuellen Entwicklungsstand. Diese Tags sind nicht für produktive Deployments empfohlen.

### Version pinnen

Für produktive Deployments sollte eine konkrete Version gepinnt werden. Setze dazu `GARDENGLOW_VERSION` auf einen Release-Tag und starte den Compose-Stack mit diesem Wert:

```bash
GARDENGLOW_VERSION=1.2.3 docker compose up -d
```

### Update durchführen

Wenn in `GARDENGLOW_VERSION` eine konkrete Version gesetzt ist, ändere sie zuerst auf den gewünschten Ziel-Release. Ziehe anschließend das Image und starte den Container neu:

```bash
docker compose pull
docker compose up -d
```

### Lokal aus dem Repository bauen

Für lokale Builds aus dem ausgecheckten Repository ergänzt `docker-compose.build.yml` den bestehenden Service um `build: .`:

```bash
docker compose -f docker-compose.yml -f docker-compose.build.yml up --build
```

### Remote-Build aus GitHub

Für einen Remote-Build direkt aus dem GitHub-Repository ergänzt `docker-compose.remote-build.yml` den bestehenden Service um den GitHub-Build-Kontext:

```bash
docker compose -f docker-compose.yml -f docker-compose.remote-build.yml up --build
```

### Healthcheck

GardenGlow stellt den Healthcheck-Endpunkt `GET /healthz` bereit. Der Endpunkt eignet sich für Monitoring, Load Balancer und Container-Orchestrierung, damit Dienste den Zustand der Anwendung regelmäßig prüfen und fehlerhafte Container automatisch erkennen können.

Beispiel für einen Docker-Compose-Healthcheck:

```yaml
healthcheck:
  test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')"]
  interval: 30s
  timeout: 3s
  retries: 3
```

## Konfiguration

Die wichtigsten Einstellungen werden über Umgebungsvariablen gelesen. `SECRET_KEY` ist immer verpflichtend. Die OIDC-Variablen sind optional, müssen aber vollständig gesetzt sein, sobald mindestens eine der drei Kernvariablen (`OIDC_SERVER_METADATA_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`) verwendet wird.

### Allgemein

| Variable | Pflicht | Standard | Beschreibung |
| --- | --- | --- | --- |
| `SECRET_KEY` | Ja | — | Flask Secret Key für Sessions und Signaturen. Muss gesetzt, nicht leer, kein offensichtlicher Placeholder und mindestens 32 Zeichen lang sein. |
| `DATABASE_URL` | Nein | `sqlite:///garden.db` | SQLAlchemy-Datenbankverbindung, z. B. SQLite in `/data`. |
| `APP_VERSION` | Nein | leer | Optionale Versionsanzeige auf der Admin-/Wartungsseite. |
| `GIT_COMMIT` | Nein | leer | Optionaler Git-Commit für die Admin-/Wartungsseite; wenn leer, wird der aktuelle Commit per `git rev-parse --short HEAD` ermittelt. |
| `GARDENGLOW_EXTERNAL_URL` | Nein | leer | Öffentliche Basis-URL der GardenGlow-Instanz, z. B. für Home-Assistant-/Widget-Links. |
| `HEADER_LOGO_URL` | Nein | leer | URL eines optionalen Header-Logos; wenn leer oder nicht gesetzt, wird kein Logo angezeigt. |

### Uploads und Dateien

| Variable | Pflicht | Standard | Beschreibung |
| --- | --- | --- | --- |
| `UPLOAD_FOLDER` | Nein | `/data/uploads` | Verzeichnis für hochgeladene Pflanzenfotos. |
| `MAX_ATTACHMENT_SIZE_BYTES` | Nein | `15728640` | Maximalgröße pro Dateiupload in Byte (15 MiB). |
| `AVATAR_FOLDER` | Nein | `/data/avatars` | Verzeichnis für lokal gespeicherte Benutzer-Avatare. |
| `MAX_AVATAR_SIZE_BYTES` | Nein | `5242880` | Maximalgröße für vom OIDC-Profil heruntergeladene Avatare in Byte (5 MiB). |
| `MAP_FOLDER` | Nein | `/data/maps` | Verzeichnis für Karten-/Lageplan-Dateien. |
| `BACKUP_FOLDER` | Nein | `/data/backups` | Verzeichnis, aus dem die Admin-/Wartungsseite die letzten Backups anzeigt. |
| `STATS_UPLOAD_CACHE_TTL_SECONDS` | Nein | `60` | Cache-Dauer in Sekunden für die Größenberechnung der Upload-Statistiken. |

### OIDC

| Variable | Pflicht | Standard | Beschreibung |
| --- | --- | --- | --- |
| `OIDC_SERVER_METADATA_URL` | Bedingt | leer | URL zur OIDC Discovery (`.well-known/openid-configuration`). Pflicht, sobald OIDC über eine der Kernvariablen aktiviert wird. |
| `OIDC_CLIENT_ID` | Bedingt | leer | OIDC Client-ID. Pflicht, sobald OIDC über eine der Kernvariablen aktiviert wird. |
| `OIDC_CLIENT_SECRET` | Bedingt | leer | OIDC Client-Secret. Pflicht, sobald OIDC über eine der Kernvariablen aktiviert wird. |
| `OIDC_LOGOUT_URL` | Nein | leer | Externe Logout-URL des Identity-Providers. |

> Hinweis: Wenn keine der OIDC-Kernvariablen gesetzt ist, startet GardenGlow ohne externen Login und meldet automatisch den Standardbenutzer **„Gärtner“** an.

### APIs und Integrationen

| Variable | Pflicht | Standard | Beschreibung |
| --- | --- | --- | --- |
| `WIDGET_API_KEY` | Nein | leer | API-Key für den Statistik-Webservice `/api/stats` und die Bewässerungsprognose-Endpunkte. Wenn leer, antwortet `/api/stats` mit `503`. |

### Influx/Sensorik

| Variable | Pflicht | Standard | Beschreibung |
| --- | --- | --- | --- |
| `INFLUX_URL` | Nein | leer | URL der InfluxDB-Instanz für Sensor-Zeitreihen. |
| `INFLUX_TOKEN` | Nein | leer | InfluxDB API-Token. |
| `INFLUX_ORG` | Nein | leer | InfluxDB Organisation. |
| `INFLUX_BUCKET` | Nein | leer | InfluxDB Bucket mit den Sensor-Zeitreihen. |
| `INFLUX_TIMEOUT_SECONDS` | Nein | `5` | Timeout für InfluxDB-Anfragen in Sekunden; Werte unter `0.1` werden auf `0.1` begrenzt. |

### Bewässerungsprognosen

| Variable | Pflicht | Standard | Beschreibung |
| --- | --- | --- | --- |
| `IRRIGATION_PREDICTION_MAX_MINUTES` | Nein | `120` | Obergrenze für ML-Bewässerungsprognosen in Minuten. Negative Modellwerte werden zu `0`, Werte oberhalb der Obergrenze werden auf diese Obergrenze beschränkt. |
| `IRRIGATION_PREDICTION_TRAIN_INTERVAL_DAYS` | Nein | `7` | Mindestabstand zwischen zwei Trainingsläufen je Beet in Tagen. |
| `IRRIGATION_PREDICTION_TRAINING_LOOKBACK_DAYS` | Nein | `900` | Historischer Sensorzeitraum für das Modelltraining in Tagen; maximal `900`, mindestens `1`. Pro Beet beginnt das Training frühestens beim ersten verfügbaren Datenpunkt eines zugeordneten Feuchtesensors. |
| `IRRIGATION_PREDICTION_TRAIN_CRON_TIME` | Nein | `03:00` | Tägliche Uhrzeit, zu der fällige Bewässerungs-Prognosemodelle automatisch geprüft und bei Bedarf neu trainiert werden (Format `HH:MM`). |
| `IRRIGATION_PREDICTION_TRAIN_CRON_ENABLED` | Nein | `true` | Aktiviert (`true`) oder deaktiviert (`false`) den täglichen Trainings-Cronjob. |

### Debug/Taxonomie

| Variable | Pflicht | Standard | Beschreibung |
| --- | --- | --- | --- |
| `COMMON_NAME_LOOKUP_LANG` | Nein | `de` | Sprache für die automatische Suche des „Bürgerlichen Namens“ über Wikipedia. |
| `DEBUG_MODE` | Nein | `false` | Aktiviert mit `1`, `true`, `yes`, `on` oder `y` das vollständige Magic-/Taxonomie-Debugging. Ohne aktivierten Debug-Modus werden auf der Pflanzenseite keine Magic-Debug-Hinweise und kein Magic-Debuglog angezeigt; bei aktivem Debug enthält die JSON-Antwort zusätzlich externe Webanfragen samt Headern, Status und vollständigem Antwortinhalt. |

## API-Endpunkte

Die folgenden JSON-Endpunkte sind für externe Integrationen und Widgets gedacht. Für beide API-Gruppen kann der in `WIDGET_API_KEY` konfigurierte Schlüssel entweder über `X-API-Key: <WIDGET_API_KEY>` oder über `Authorization: Bearer <WIDGET_API_KEY>` gesendet werden.

### `GET /api/stats`

- **Methode und Pfad:** `GET /api/stats`
- **Zweck:** Liefert kompakte Pflanzen-, Beet-, Upload- und Datenbankstatistiken für Dashboards oder Widgets.
- **Authentifizierung:** Erfordert `WIDGET_API_KEY`; nutze den Header `X-API-Key: <WIDGET_API_KEY>` oder alternativ `Authorization: Bearer <WIDGET_API_KEY>`.
- **Wichtige Antwortfelder:**
  - `plants`: Anzahl aller Pflanzen
  - `beds`: Anzahl aller Beete/Pflanzorte ohne den internen Papierkorb
  - `uploads`: Anzahl aller hochgeladenen Dateien im Upload-Verzeichnis
  - `upload_size_bytes`: Gesamtgröße aller Uploads in Byte
  - `database_size_bytes`: Größe der SQLite-Datenbankdatei in Byte; bei anderen Datenbanktypen oder fehlender Datei `0`
- **Fehler-/Sonderfälle:** Wenn `WIDGET_API_KEY` nicht gesetzt ist, antwortet der Endpunkt mit `503`. Bei fehlendem oder falschem API-Key antwortet er mit `401`. Upload-Statistiken werden für die Dauer von `STATS_UPLOAD_CACHE_TTL_SECONDS` zwischengespeichert.

### `GET /api/irrigation-predictions`

- **Methode und Pfad:** `GET /api/irrigation-predictions`
- **Zweck:** Liefert ML-basierte Bewässerungsprognosen für alle produktiven Beete. GardenGlow sagt aus den vorhandenen Sensor-Zeitreihen vorher, wie viele Minuten die Bewässerung heute laufen sollte, um die Ziel-Bodenfeuchte zu erreichen.
- **Authentifizierung:** Erfordert `WIDGET_API_KEY`; nutze den Header `X-API-Key: <WIDGET_API_KEY>` oder alternativ `Authorization: Bearer <WIDGET_API_KEY>`.
- **Wichtige Antwortfelder:**
  - `predictions`: Liste der Prognosen je Beet
  - `max_minutes`: aktuell konfigurierte Obergrenze für vorhergesagte Bewässerungsdauer
  - Pro Eintrag in `predictions`: `location_id`, `location_name`, `target_soil_moisture_percent`, `predicted_minutes`, `raw_predicted_minutes`, `source`, `trained_now`, `training_error`, `model` und `features`
- **Fehler-/Sonderfälle:** Wenn `WIDGET_API_KEY` nicht gesetzt ist, antwortet der Endpunkt mit `503`; bei fehlendem oder falschem API-Key mit `401`. Wenn InfluxDB nicht vollständig konfiguriert ist, antwortet der Endpunkt ebenfalls mit `503`. Ein täglicher Cronjob prüft standardmäßig um `03:00` Uhr, ob Modelle fällig sind, und trainiert sie bei Bedarf neu; die Uhrzeit kann über `IRRIGATION_PREDICTION_TRAIN_CRON_TIME` im Format `HH:MM` geändert werden. Beim Abruf wird das Training zusätzlich automatisch ausgeführt, falls noch kein Modell vorhanden ist oder das letzte Training mindestens eine Woche zurückliegt. Negative Vorhersagen werden auf `0` Minuten gesetzt; Werte oberhalb der konfigurierten Obergrenze werden begrenzt.

### `GET /api/locations/<id>/irrigation-prediction`

- **Methode und Pfad:** `GET /api/locations/<id>/irrigation-prediction`
- **Zweck:** Liefert die ML-basierte Bewässerungsprognose für ein einzelnes Beet mit der angegebenen numerischen Standort-ID.
- **Authentifizierung:** Erfordert `WIDGET_API_KEY`; nutze den Header `X-API-Key: <WIDGET_API_KEY>` oder alternativ `Authorization: Bearer <WIDGET_API_KEY>`.
- **Wichtige Antwortfelder:** `location_id`, `location_name`, `target_soil_moisture_percent`, `predicted_minutes`, `raw_predicted_minutes`, `max_minutes`, `source`, `trained_now`, `training_error`, `model` und `features`.
- **Fehler-/Sonderfälle:** Wenn `WIDGET_API_KEY` nicht gesetzt ist, antwortet der Endpunkt mit `503`; bei fehlendem oder falschem API-Key mit `401`. Wenn InfluxDB nicht vollständig konfiguriert ist, antwortet der Endpunkt ebenfalls mit `503`. Für den internen Papierkorb wird keine Bewässerung vorhergesagt; der Endpunkt antwortet dann mit `400`. Für unbekannte Standort-IDs gilt die normale Flask-404-Behandlung. Negative Vorhersagen werden auf `0` Minuten gesetzt; Werte oberhalb der konfigurierten Obergrenze werden begrenzt.

### Beispiel: gethomepage Custom API Widget

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
