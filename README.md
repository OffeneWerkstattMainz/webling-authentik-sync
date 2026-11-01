# webling-authentik-sync

Synchronisiert die Mitglieder der Webling-Instanz nach Authentik. Die Werte in
den Webling Feldern *Status* und *Funktion* bestimmen, in welche (gleichnamigen)
Gruppen in Authentik das Mitglied eingruppiert wird.

Die Gruppen müssen vorab (bei uns mittels eines Blueprints) angelegt werden. Der
Sync vergibt nur die Mitgliedschaften und bricht ab, wenn ein Status und eine
Funktion gleich heißen.

Ein Status- oder Funktionswert ohne gleichnamige Gruppe ist unbekannt: Mitglieder
mit unbekanntem Status werden nicht angelegt, bei einer unbekannten Funktion
fehlt nur diese Gruppe, und schon vorhandene Benutzer behalten nur die bekannten
Gruppen. Für Werte in `NON_MEMBER_VALUES` geschieht
das stillschweigend, für alle anderen gibt es eine WARNUNG und eine Meldung an
`ZULIP_ALERT_WEBHOOK`, sobald sich die Menge dieser Werte ändert. Eine neue
Gruppe im Blueprint genügt, damit der nächste Durchlauf die Mitglieder anlegt.

Deaktivierung ausgetretener Mitglieder: wenn der Status in Webling "Ausgetreten"
lautet und entweder kein Datum für "Austrittsdatum" eingetragen wurde oder das
Austrittsdatum in der Vergangenheit liegt. Ebenso deaktiviert werden Benutzer,
deren Status in `NON_MEMBER_VALUES` steht.

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
| `ONBOARDING_EMAIL_STAGE` | Optional. Name einer E-Mail-Stage (bei uns `owm-onboarding-email`); wenn gesetzt, bekommt jedes neu angelegte, aktive Mitglied deren Mail. Unsere Vorlage `owm/onboarding.html` enthält keinen Token-Link, sondern verweist auf die Startseite des Flows `owm-recovery`, wo das Mitglied sich einen 30 Minuten gültigen Link zum Setzen des ersten Passworts schicken lässt. Der Service-Account braucht dafür zusätzlich die Rechte `authentik_core.reset_user_password` und `authentik_stages_email.view_emailstage` |
| `NON_MEMBER_VALUES` | Optional. JSON-Liste von Status- oder Funktionswerten ohne Authentik-Gruppe, die keine Mitgliedschaft bedeuten (z. B. `["Erfasst", "Ausgeschlossen"]`): dafür gibt es keine WARNUNG; Mitglieder mit so einem Status werden nicht angelegt, vorhandene Benutzer mit so einem Status deaktiviert |
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
        ONBOARDING_EMAIL_STAGE: owm-onboarding-email
        ZULIP_URL: ${ZULIP_URL:-}
        ZULIP_API_USER: ${ZULIP_API_USER:-}
        ZULIP_API_KEY: ${ZULIP_API_KEY:-}
      depends_on:
        - server

## Migrationsskripte

Nur einmalig müssen diese Skripte ausgeführt werden:

- `migrate-passwords.py` (Zulip-Passwort-Hashes nach Authentik, braucht die
Variablen `AUTHENTIK_DB_*` und `IGNORE_EMAILS`). Darf mit einem neuen Dump
wiederholt werden, bis Zulip auf den Authentik-Login umgestellt ist: in
Authentik gesetzte Passwörter überschreibt es nicht.

- `set-nextcloud-uids.py` (bestehende Nextcloud-Benutzer-IDs als Authentik-Attribut `nextcloud_user_id`)

Beide beschreiben ihre Eingabedateien im Docstring.

## Test

    python test_sync.py
