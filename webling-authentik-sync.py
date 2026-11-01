#!/usr/bin/env python3
"""Synchronisiert die Webling-Mitglieder nach Authentik.

Legt pro Webling-Mitglied einen Authentik-Benutzer an bzw. aktualisiert ihn.
Jeder Webling-Status und jede Funktion ist eine gleichnamige Authentik-Gruppe;
die Gruppen legt ein Blueprint an, der Sync vergibt nur die Mitgliedschaften
(und entfernt die von ihm früher vergebenen, die nicht mehr gelten, siehe
AuthentikClient.update_user). Mitglieder mit Status Ausgetreten oder einem
zurückliegenden Austrittsdatum werden deaktiviert, ebenso früher
synchronisierte Authentik-Benutzer, deren Mitglied aus Webling verschwunden ist
(erkannt am Benutzerattribut webling_id).

Exit-Code 1 und eine Meldung auf stderr, wenn ein Status und eine Funktion
gleich heissen; dann wird nichts geändert.

Ein Status- oder Funktionswert ohne gleichnamige Authentik-Gruppe ist
unbekannt. Ein Mitglied mit unbekanntem Status wird nicht angelegt; bei einer
unbekannten Funktion wird es ohne diese Gruppe angelegt, und ein schon
vorhandener Benutzer behält nur die bekannten Gruppen. Steht der Wert in
NON_MEMBER_VALUES (JSON-Liste, z. B. ["Erfasst", "Ausgeschlossen"]), geschieht das
stillschweigend, sonst gibt es eine WARNUNG pro Wert und eine Meldung an
ZULIP_ALERT_WEBHOOK, sobald sich die Menge dieser Werte ändert. Ein vorhandener
Benutzer, dessen Status in NON_MEMBER_VALUES steht, wird deaktiviert.

Sind ZULIP_URL, ZULIP_API_USER und ZULIP_API_KEY gesetzt (API-Key eines
Zulip-Administrators oder Owners, sonst sind fremde Adressen nicht sichtbar),
wird die Benutzerliste von Zulip abgeglichen: WARNUNG für jede in Webling
eingetragene 'Zulip E-Mail', die Zulip nicht kennt, und für jedes aktive
Zulip-Konto, dessen Adresse zu keinem Mitglied gehört (weder 'E-Mail' noch
'Zulip E-Mail'). Letztere würde die SCIM-Provisionierung deaktivieren.

Ist ONBOARDING_EMAIL_STAGE gesetzt (Name einer E-Mail-Stage, bei uns
owm-onboarding-email), bekommt jedes neu angelegte, aktive Mitglied eine
Willkommens-Mail. Sie verweist auf die Startseite des Flows owm-recovery, wo das
Mitglied sich einen kurz gültigen Link zum Setzen des ersten Passworts schicken
lässt.

Mit --loop läuft der Sync alle SYNC_INTERVAL Sekunden (Standard 600) und
meldet Fehlschläge an ZULIP_ALERT_WEBHOOK (URL eines Zulip-Webhooks vom Typ
"Slack incoming"), falls gesetzt.

"""
from dotenv import load_dotenv
load_dotenv()

import json
import os
import requests
import sys
import time
import traceback
import urllib.parse
import urllib.request
from datetime import date

from authentikclient import AuthentikClient
from utils import unix_name
from weblingclient import WeblingClient

UNCHANGED = []
# Werte ohne Gruppe, die beim letzten Durchlauf gemeldet wurden (--loop)
REPORTED_UNKNOWN = set()
# Die API legt für die Willkommens-Mail ein Token an; die Vorlage
# owm/onboarding.html zeigt es nicht (nur die Startseite des Flows owm-recovery),
# daher die kürzeste Gültigkeit.
ONBOARDING_TOKEN_DURATION = 'minutes=1'

def groups_of(member):
    props = member['properties']
    return ([props['Status']] if props['Status'] else []) + (props['Funktion'] or [])


def non_member_values():
    return set(json.loads(os.getenv('NON_MEMBER_VALUES') or '[]'))


def member_active(member):
    # Ausgetreten oder ein Status aus NON_MEMBER_VALUES: sofort. Sonst aktiv bis
    # einschliesslich Austrittsdatum, deaktiviert ab dem Folgetag.
    props = member['properties']
    end = props['Austrittsdatum']
    return (props['Status'] != 'Ausgetreten' and props['Status'] not in non_member_values()
            and (not end or date.fromisoformat(end) >= date.today()))


# Zulip-Benutzer (ohne Bots) als Liste von (adresse, name, aktiv), None wenn
# Zulip nicht konfiguriert oder nicht erreichbar ist.
def zulip_users():
    url, user, key = os.getenv('ZULIP_URL'), os.getenv('ZULIP_API_USER'), os.getenv('ZULIP_API_KEY')
    if not (url and user and key):
        return None
    try:
        response = requests.get(f"{url.rstrip('/')}/api/v1/users", auth=(user, key), timeout=30)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"WARNUNG: Zulip-Benutzerliste nicht abrufbar, Prüfung der Zulip E-Mails entfällt: {e}")
        return None
    return [ ((u.get('delivery_email') or u['email']).lower(), u['full_name'], u['is_active'])
             for u in response.json()['members'] if not u['is_bot'] ]


def check_zulip_emails(wbl, users):
    if users is None:
        return
    users = [ (email.lower(), name, active) for email, name, active in users ]
    zulip_emails = { email for email, _, _ in users }
    member_emails = set()
    for member in wbl.members:
        props = member['properties']
        member_emails.add((props['E-Mail'] or '').strip().lower())
        zulip_email = wbl.zulip_email(member)
        member_emails.add(zulip_email.lower())
        if zulip_email and zulip_email.lower() not in zulip_emails:
            print(f"WARNUNG: Zulip E-Mail {zulip_email} von Mitglied {props['Mitglieder ID']} "
                  f"({props['Vorname']} {props['Name']}) existiert nicht in Zulip")
    for email, name, active in users:
        if active and email not in member_emails:
            print(f"WARNUNG: Zulip-Konto {email} ({name}) gehört zu keinem Webling-Mitglied")


def check_groups(auth, members):
    statuses = {m['properties']['Status'] for m in members if m['properties']['Status']}
    funktionen = {f for m in members for f in (m['properties']['Funktion'] or [])}
    clash = statuses & funktionen
    if clash:
        sys.exit(f"FEHLER: Webling-Status und -Funktion heißen gleich: {sorted(clash)}")
    # Liefert die Werte ohne Authentik-Gruppe; gewarnt wird nur für die, die
    # nicht in NON_MEMBER_VALUES stehen.
    missing = auth.missing_groups(statuses | funktionen)
    unexpected = missing - non_member_values()
    for name in sorted(unexpected):
        count = sum(1 for m in members if name in groups_of(m))
        print(f"WARNUNG: Gruppe '{name}' fehlt in Authentik (in den Blueprint aufnehmen "
              f"oder in NON_MEMBER_VALUES), betrifft {count} Mitglieder")
    if unexpected != REPORTED_UNKNOWN:
        if unexpected:
            alert(f"webling-authentik-sync: Gruppen fehlen in Authentik (Mitglieder mit so einem "
                  f"Status werden nicht angelegt): {sorted(unexpected)}")
        REPORTED_UNKNOWN.clear()
        REPORTED_UNKNOWN.update(unexpected)
    return missing


def sync_member(auth, wbl, member, unknown_groups=frozenset()):
    props = member['properties']
    member_id = props['Mitglieder ID']
    email = wbl.authentik_email(member)
    if not email:
        print(f"WARNUNG: Mitglied {member_id} ({props['Vorname']} {props['Name']}) hat keine E-Mail, übersprungen")
        return None
    name = f"{props['Vorname'].strip()} {props['Name'].strip()}"
    login = unix_name(f"{name}.{member_id}")
    groups = [g for g in groups_of(member) if g not in unknown_groups]
    active = member_active(member)
    attributes = {'webling_id': member_id}

    # zuerst webling_id: findet den Benutzer auch, wenn sich die E-Mail in Webling geändert hat
    existing = auth.find_by_webling_id(member_id) or auth.find_by_email(email)
    other = existing and (existing.attributes or {}).get('webling_id')
    if other and other != member_id:
        print(f"WARNUNG: Mitglied {member_id} ({name}) teilt die E-Mail {email} mit Mitglied {other}, übersprungen")
        return None
    if existing:
        if auth.update_user(existing, email=email, login=login, display_name=name,
                            active=active, groupnames=groups, attributes=attributes):
            print(f"Aktualisiert {email}: name={name}, aktiv={active}, gruppen={groups}")
        else:
            UNCHANGED.append(member_id)
    elif props['Status'] in unknown_groups:
        # nur ein bekannter Status macht ein neues Mitglied; unbekannte Funktionen werden ignoriert
        return None
    else:
        print(f"Angelegt {email}: name={name}, aktiv={active}, gruppen={groups}")
        user = auth.create_user(email=email, login=login, display_name=name,
                                active=active, groupnames=groups, attributes=attributes)
        stage = os.getenv('ONBOARDING_EMAIL_STAGE')
        if active and stage and auth.send_recovery_email(user, stage, ONBOARDING_TOKEN_DURATION):
            print(f"Willkommens-Mail an {email} gesendet")
    return member_id


def deactivate_leavers(auth, synced_ids):
    for user in auth.list_users():
        webling_id = (user.attributes or {}).get('webling_id')
        if webling_id and webling_id not in synced_ids and user.is_active:
            print(f"Deaktiviert {user.email} (webling_id={webling_id} nicht mehr in Webling)")
            auth.deactivate_user(user)


def main():
    UNCHANGED.clear()
    wbl = WeblingClient()
    wbl.fetch_members()
    auth = AuthentikClient()
    auth.connect()
    unknown_groups = check_groups(auth, wbl.members)
    check_zulip_emails(wbl, zulip_users())

    synced_ids = set()
    for member in wbl.members:
        member_id = sync_member(auth, wbl, member, unknown_groups)
        if member_id:
            synced_ids.add(member_id)

    deactivate_leavers(auth, synced_ids)
    print(f"Fertig: {len(synced_ids)} Mitglieder synchronisiert, davon {len(UNCHANGED)} unverändert.")


def alert(text):
    url = os.getenv('ZULIP_ALERT_WEBHOOK')
    if not url:
        return
    data = urllib.parse.urlencode({'payload': json.dumps({'text': text})}).encode()
    try:
        urllib.request.urlopen(url, data, timeout=30)
    except Exception as e:
        print(f"Meldung an Zulip fehlgeschlagen: {e}", file=sys.stderr)


def loop():
    interval = int(os.getenv('SYNC_INTERVAL', '600'))
    while True:
        try:
            main()
        except SystemExit as e:
            print(e, file=sys.stderr)
            alert(f"webling-authentik-sync: {e}")
        except Exception:
            traceback.print_exc()
            alert("webling-authentik-sync fehlgeschlagen:\n```\n" + traceback.format_exc()[-1500:] + "\n```")
        sys.stdout.flush()
        time.sleep(interval)


if __name__ == '__main__':
    loop() if '--loop' in sys.argv[1:] else main()
