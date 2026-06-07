# GardenGlow 🌿✨

GardenGlow ist dein digitales Gartentagebuch: ein schöner Ort für Beete, Pflanzen, Fotos, Notizen und kleine Garten-Erfolge. Vergiss nie wieder, wie deine Pflanzen heißen, wann du sie gesetzt hast und welche Sorte im letzten Sommer so gut getragen hat. Du siehst, was wo wächst, hältst Entwicklungen fest und kannst auf Wunsch Sensorwerte sowie smarte Bewässerung einbinden.

Kurz gesagt: weniger Zettelwirtschaft, mehr Überblick im Grünen und schnell bei der Hand, was eigentlich welche Pflanze ist.

## Was GardenGlow für dich macht

- **Beete und Gartenbereiche organisieren** – vom Hochbeet bis zur wilden Ecke hinterm Schuppen.
- **Pflanzen liebevoll dokumentieren** – mit Namen, Standort, Fotos, Kommentaren, Verlauf und passenden Links in externe Pflanzenkataloge.
- **Gartenmomente festhalten** – Blüte, Rückschnitt, Umtopfen, Ernte oder einfach: „Heute sieht sie fantastisch aus“.
- **Sensorwerte sichtbar machen** – Bodenfeuchte, Temperatur, Regen und Bewässerung können direkt beim Beet landen.
- **Bewässerung besser einschätzen** – GardenGlow kann aus vorhandenen Messwerten vorschlagen, wie lange ein Beet Wasser braucht.
- **Home Assistant anbinden** – wenn du willst, kann deine Automation die GardenGlow-Prognose nutzen.
- **Hell oder dunkel genießen** – je nachdem, ob du gerade in der Sonne oder abends auf dem Sofa planst.

## Für wen ist das?

GardenGlow passt zu dir, wenn du ...

- wissen möchtest, welche Pflanze wo steht,
- Fotos und Notizen nicht mehr in Chatverläufen verlieren willst,
- gerne beobachtest, wie dein Garten sich verändert,
- Sensoren oder Home Assistant nutzt – aber nicht musst,
- deinen Garten ein bisschen smarter, schöner und entspannter verwalten möchtest.

## Schnellstart mit Docker

Wenn du GardenGlow einfach ausprobieren, reicht meist dieses Setup.

1. Erzeuge einen sicheren Schlüssel:

```bash
python -c 'import secrets; print(secrets.token_urlsafe(48))'
```

2. Lege eine `docker-compose.yml` an:

```yaml
services:
  gardenglow:
    image: ghcr.io/doenke/gardenglow:${GARDENGLOW_VERSION:-latest}
    container_name: gardenglow
    restart: unless-stopped
    environment:
      SECRET_KEY: hier-den-generierten-secret-key-einfuegen
      DATABASE_URL: sqlite:////data/garden.db
    volumes:
      - gardenglow_data:/data
    ports:
      - "8000:8000"

volumes:
  gardenglow_data:
```


Danach öffnest du `http://localhost:8000`. Ohne zusätzliche Login-Konfiguration begrüßt dich GardenGlow automatisch als **„Gärtner“**. 

## Ein erster Rundgang

### 1. Beete anlegen

Lege deine Gartenbereiche so an, wie du wirklich denkst: „Tomatenhaus“, „Kräuterbeet“, „Vorgarten“, „Schattenecke“ oder „Experimentierfläche“. GardenGlow ist bewusst flexibel – es muss kein perfekter Plan sein.

### 2. Pflanzen hinzufügen

Füge Pflanzen zu ihren Standorten hinzu und ergänze Fotos, Kommentare oder Beobachtungen. So entsteht mit der Zeit eine lebendige Chronik: Was hat funktioniert? Was kam wieder? Was war ein Fehlkauf? Was muss nächstes Jahr unbedingt nochmal her?

### 3. Fortschritt sehen

Fotos und Notizen machen Veränderungen sichtbar. Gerade bei Aussaat, Stecklingen, Jungpflanzen und Stauden ist das Gold wert: Du erkennst, was wirklich wächst – und nicht nur, was du hoffst.

### 4. Optional smarter werden

Wenn du Sensoren nutzt, kann GardenGlow Bodenfeuchte, Temperatur, Regen und Bewässerung direkt mit deinen Beeten verknüpfen. Aus diesen Daten kann die App eine Bewässerungsdauer vorschlagen. Das ist besonders praktisch für automatische Bewässerung oder wenn du deine Pflanzen nicht nach Bauchgefühl ertränken möchtest.

## Pflanzenkataloge und Nachschlagewerke

Wenn du den wissenschaftlichen Namen einer Pflanze pflegst, kann GardenGlow passende externe Katalog-IDs vorschlagen und daraus direkte Links zu Nachschlagewerken bauen. Unterstützt werden unter anderem **Wikipedia**, **Mein schöner Garten**, **NaturaDB**, **FloraWeb**, **GBIF**, **World Flora Online** und **POWO/IPNI**. So springst du aus deinem Gartentagebuch schnell zu Steckbriefen, Taxonomie und weiterführenden Informationen.

## Smarte Bewässerung

GardenGlow kann Messwerte aus InfluxDB nutzen und daraus je Beet eine Empfehlung ableiten: **Wie viele Minuten sollte heute bewässert werden?**

Das ist kein Muss. GardenGlow funktioniert auch wunderbar als Pflanzen- und Beetjournal. Mit Sensoren wird es aber zum kleinen Gartencockpit:

- aktuelle Bodenfeuchte direkt am Beet,
- historische Verläufe für Feuchte, Temperatur, Regen und Bewässerung,
- Ziel-Bodenfeuchte pro Beet,
- automatische Prognosen für die Bewässerungsdauer,
- Home-Assistant-Blueprint für passende Automationen.

Für Home Assistant stellt GardenGlow einen Blueprint unter `/homeassistant/gardenglow-irrigation-blueprint.yaml` bereit. Die Import-URL findest du in der GardenGlow-Konfiguration im Bereich **Home Assistant Blueprint**.

## Kleine Konfiguration, großer Nutzen

Für den normalen Start brauchst du nur wenige Werte:

| Einstellung | Wofür? |
| --- | --- |
| `SECRET_KEY` | Pflicht. Schützt Sessions und sollte ein langer, zufälliger Wert sein. |
| `DATABASE_URL` | Speicherort der Datenbank. Für Docker ist `sqlite:////data/garden.db` praktisch. |
| `UPLOAD_FOLDER` | Speicherort für Pflanzenfotos. Für Docker passt `/data/uploads`. |
| `GARDENGLOW_VERSION` | Optionaler Container-Tag. Ohne Wert wird `latest` genutzt. |
| `HEADER_LOGO_URL` | Optionales Logo im Kopfbereich. |
| `WIDGET_API_KEY` | Optionaler Schlüssel für Widgets und Bewässerungs-APIs. |
| `API_REQUEST_HEADER_LOGGING` | Optionales Debug-Logging: schreibt die vollständigen Request-Header für `/api`-Endpunkte ins Docker-Log. Standardmäßig aktiv, wenn `DEBUG_MODE=true` ist. |

Für Sensorik kommen bei Bedarf noch InfluxDB-Werte dazu:

| Einstellung | Wofür? |
| --- | --- |
| `INFLUX_URL` | Adresse deiner InfluxDB. |
| `INFLUX_TOKEN` | API-Token für den Zugriff. |
| `INFLUX_ORG` | Organisation in InfluxDB. |
| `INFLUX_BUCKET` | Bucket mit den Zeitreihen. |

## Login: einfach oder professionell

Standardmäßig ist GardenGlow angenehm unkompliziert: Wenn du keine externe Anmeldung konfigurierst, startet die App mit dem Benutzer **„Gärtner“**. Das ist ideal für private Installationen, die bereits durch dein Heimnetz, VPN oder einen Reverse Proxy geschützt sind.

Für professionelle Setups kann GardenGlow auch mit OIDC betrieben werden. Dann brauchst du vollständig:

- `OIDC_SERVER_METADATA_URL`
- `OIDC_CLIENT_ID`
- `OIDC_CLIENT_SECRET`

Optional ist `OIDC_LOGOUT_URL`. Sobald du OIDC nutzt, müssen die drei Pflichtwerte vollständig gesetzt sein.

## Für Integrationen

Wenn du Dashboards, Widgets oder Home Assistant anbinden möchtest, gibt es schlanke JSON-Endpunkte. Sie verwenden den `WIDGET_API_KEY` über `X-API-Key` oder `Authorization: Bearer`.

| Endpunkt | Liefert |
| --- | --- |
| `GET /api/stats` | Kompakte Zahlen zu Pflanzen, Beeten, Uploads und Datenbankgröße. |
| `GET /api/irrigation-predictions` | Bewässerungsprognosen für alle produktiven Beete. |
| `GET /api/locations/<id>/irrigation-prediction` | Bewässerungsprognose für ein einzelnes Beet. |

Beispiel für ein [gethomepage](https://gethomepage.dev/)-Widget:

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
```

Tipp: Speichere den Key als Umgebungsvariable und nicht direkt im Klartext in der Widget-Datei.

## Für Entwicklerinnen und Entwickler

GardenGlow ist eine Container-App und speichert Daten persistent, wenn du ein Volume verwendest. Die Pflanzen-Datenbanken bzw. Kataloge sind aktuell fest in der App definiert und brauchen keine eigene Verwaltung in Compose. Lokal kannst du den `SECRET_KEY` so setzen:

```bash
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
```

Wenn der Schlüssel fehlt, leer ist, offensichtlich unsicher aussieht oder kürzer als 32 Zeichen ist, startet GardenGlow bewusst nicht. Das verhindert versehentlich unsichere Installationen.

---

Viel Spaß beim Pflanzen, Planen, Fotografieren und Gießen. 🌱

## Technische Doku

Dieser Abschnitt ist für alle gedacht, die GardenGlow dauerhaft betreiben, automatisieren oder in eine bestehende Homelab-/Server-Umgebung einbauen möchten.

### Vollständige `docker-compose.yml`

Die folgende Compose-Datei enthält die üblichen Einstellungen für Datenbank, Uploads, optionale Sensorik, Widgets, Home Assistant, OIDC und einen Container-Healthcheck. Für den einfachen Privatbetrieb kannst du die optionalen Blöcke leer lassen oder entfernen.

```yaml
services:
  gardenglow:
    image: ghcr.io/doenke/gardenglow:latest
    # für die DEV Version:
    #build: https://github.com/doenke/gardenglow.git#main
    container_name: gardenglow
    restart: unless-stopped
    environment:
      # Pflicht: bitte durch einen eigenen, langen Zufallswert ersetzen.
      SECRET_KEY: hier-den-generierten-secret-key-einfuegen

      # Persistente Daten in /data.
      DATABASE_URL: sqlite:////data/garden.db
      UPLOAD_FOLDER: /data/uploads
      MAX_ATTACHMENT_SIZE_BYTES: "15728640"
      AVATAR_FOLDER: /data/avatars
      MAX_AVATAR_SIZE_BYTES: "5242880"
      MAP_FOLDER: /data/maps
      BACKUP_FOLDER: /data/backups
      STATS_UPLOAD_CACHE_TTL_SECONDS: "60"

      # Optional: öffentliche URL und eigenes Logo.
      GARDENGLOW_EXTERNAL_URL: https://gardenglow.example.com
      HEADER_LOGO_URL: ""

      # Optional: API-Key für Widgets und Bewässerungs-Endpunkte.
      WIDGET_API_KEY: bitte-aendern-wenn-genutzt

      # Optional: InfluxDB für Sensorwerte und Bewässerungsprognosen.
      INFLUX_URL: ""
      INFLUX_TOKEN: ""
      INFLUX_ORG: ""
      INFLUX_BUCKET: ""
      INFLUX_TIMEOUT_SECONDS: "5"

      # Optional: Bewässerungsprognosen feinjustieren.
      IRRIGATION_PREDICTION_MAX_MINUTES: "120"
      IRRIGATION_PREDICTION_TRAIN_INTERVAL_DAYS: "7"
      IRRIGATION_PREDICTION_TRAINING_LOOKBACK_DAYS: "900"
      IRRIGATION_PREDICTION_TRAIN_CRON_TIME: "03:00"
      IRRIGATION_PREDICTION_TRAIN_CRON_ENABLED: "true"

      # Optional: Pflanzen-/Taxonomie-Hilfen.
      COMMON_NAME_LOOKUP_LANG: de
      DEBUG_MODE: "false"
      # Optional: API-Request-Header im Docker-Log ausgeben (sonst nur bei DEBUG_MODE=true).
      API_REQUEST_HEADER_LOGGING: "false"

      # Optional: professioneller Login per OIDC.
      # Wenn du OIDC nutzt, müssen alle drei Kernwerte vollständig gesetzt sein.
      OIDC_SERVER_METADATA_URL: ""
      OIDC_CLIENT_ID: ""
      OIDC_CLIENT_SECRET: ""
      OIDC_LOGOUT_URL: ""

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

### Wichtige Hinweise zum Betrieb

- `SECRET_KEY` ist Pflicht, muss mindestens 32 Zeichen lang sein und darf kein offensichtlicher Platzhalter sein.
- Ohne OIDC-Konfiguration startet GardenGlow automatisch mit dem Benutzer **„Gärtner“**. Schütze die Instanz dann über Heimnetz, VPN, Reverse Proxy oder eine vergleichbare vorgelagerte Zugriffskontrolle.
- Sobald du eine der OIDC-Kernvariablen nutzt, müssen `OIDC_SERVER_METADATA_URL`, `OIDC_CLIENT_ID` und `OIDC_CLIENT_SECRET` vollständig gesetzt sein.
- Für Sensorwerte und Bewässerungsprognosen müssen `INFLUX_URL`, `INFLUX_TOKEN`, `INFLUX_ORG` und `INFLUX_BUCKET` zusammenpassen.
- `WIDGET_API_KEY` schützt die JSON-Endpunkte für Statistik-Widgets und Bewässerungsprognosen.

### API-Kurzreferenz

| Endpunkt | Zweck | Authentifizierung |
| --- | --- | --- |
| `GET /api/stats` | Pflanzen-, Beet-, Upload- und Datenbankstatistiken für Dashboards. | `WIDGET_API_KEY` |
| `GET /api/irrigation-predictions` | Bewässerungsprognosen für alle produktiven Beete. | `WIDGET_API_KEY` |
| `GET /api/locations/<id>/irrigation-prediction` | Bewässerungsprognose für ein einzelnes Beet. | `WIDGET_API_KEY` |

Den API-Key sendest du entweder als Header `X-API-Key: <WIDGET_API_KEY>` oder als `Authorization: Bearer <WIDGET_API_KEY>`.
