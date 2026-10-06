# webling-authentik-sync

Synchronisiert die Mitglieder der Webling-Instanz nach Authentik. Die Werte in
den Webling Feldern *Status* und *Funktion* bestimmen, in welche (gleichnamigen)
Gruppen in Authentik das Mitglied eingruppiert wird.

Die Gruppen müssen vorab (bei uns mittels eines Blueprints) angelegt werden. Der
Sync vergibt nur die Mitgliedschaften und bricht ab, wenn eine Gruppe fehlt oder
ein Status und eine Funktion gleich heißen.

Deaktivierung ausgetretener Mitglieder: wenn der Status in Webling "Ausgetreten"
lautet und entweder kein Datum für "Austrittsdatum" eingetragen wurde oder das
Austrittsdatum in der Vergangenheit liegt.

## Aufruf

    python webling-authentik-sync.py          # ein Durchlauf
    python webling-authentik-sync.py --loop   # alle SYNC_INTERVAL Sekunden

Das Container-Image (`Dockerfile`, vom GitHub-Workflow nach
`ghcr.io/offenewerkstattmainz/webling-authentik-sync` gebaut) startet
standardmäßig mit `--loop`.

## Umgebungsvariablen

| Variable | Bedeutung |
|---|---|
| `WEBLING_API_URL` | `https://<instanz>.webling.ch/api/1` |
| `WEBLING_API_KEY` | Webling-API-Key mit Lesezugriff auf die Mitglieder |
| `AUTHENTIK_API_URL` | `https://<host>/api/v3` (innerhalb des Compose-Stacks: `http://server:9000/api/v3`) |
| `AUTHENTIK_API_TOKEN` | Token eines Service-Accounts, der Benutzer und Gruppen lesen und schreiben darf |
| `SYNC_INTERVAL` | Sekunden zwischen zwei Durchläufen im `--loop`-Modus, Standard 600 |
| `ZULIP_ALERT_WEBHOOK` | Optional. URL eines Zulip-Webhooks vom Typ "Slack incoming"; fehlgeschlagene Durchläufe werden dorthin gemeldet |
| `ZULIP_URL`, `ZULIP_API_USER`, `ZULIP_API_KEY` | Optional. Zulip-Instanz und API-Zugang eines Zulip-Administrators; wenn gesetzt, warnt der Sync bei jeder in Webling eingetragenen `Zulip E-Mail`, die Zulip nicht kennt, und bei jedem aktiven Zulip-Konto ohne Webling-Mitglied |

Nicht nur docker, sondern auch der Python Code liest eine `.env`-Datei im
Arbeitsverzeichnis, falls vorhanden.

## Compose-Service

Eine solche Konfiguration in der Compose Datei von Authentik sorgt für einen
Sync, der alle 10 Minuten läuft:

    webling-sync:
      image: ghcr.io/offenewerkstattmainz/webling-authentik-sync:latest
      restart: unless-stopped
      environment:
        WEBLING_API_URL: ${WEBLING_API_URL:?}
        WEBLING_API_KEY: ${WEBLING_API_KEY:?}
        AUTHENTIK_API_URL: http://server:9000/api/v3
        AUTHENTIK_API_TOKEN: ${WEBLING_SYNC_API_TOKEN:?}
        SYNC_INTERVAL: 600
        ZULIP_ALERT_WEBHOOK: ${ZULIP_ALERT_WEBHOOK:-}
        ZULIP_URL: ${ZULIP_URL:-}
        ZULIP_API_USER: ${ZULIP_API_USER:-}
        ZULIP_API_KEY: ${ZULIP_API_KEY:-}
      depends_on:
        - server

## Migrationsskripte

Nur einmalig müssen diese Skripte ausgeführt werden:

- `migrate-passwords.py` (Zulip-Passwort-Hashes nach Authentik, braucht die
Variablen `AUTHENTIK_DB_*` und `IGNORE_EMAILS`)

- `set-nextcloud-uids.py` (bestehende Nextcloud-Benutzer-IDs als Authentik-Attribut `nextcloud_user_id`)

Beide beschreiben ihre Eingabedateien im Docstring.

## Test

    python test_sync.py
