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
gleich heissen oder eine benötigte Gruppe in Authentik fehlt; dann wird nichts
geändert.

Sind ZULIP_URL, ZULIP_API_USER und ZULIP_API_KEY gesetzt (API-Key eines
Zulip-Administrators oder Owners, sonst sind fremde Adressen nicht sichtbar),
wird die Benutzerliste von Zulip abgeglichen: WARNUNG für jede in Webling
eingetragene 'Zulip E-Mail', die Zulip nicht kennt, und für jedes aktive
Zulip-Konto, dessen Adresse zu keinem Mitglied gehört (weder 'E-Mail' noch
'Zulip E-Mail'). Letztere würde die SCIM-Provisionierung deaktivieren.

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

def groups_of(member):
    props = member['properties']
    return ([props['Status']] if props['Status'] else []) + (props['Funktion'] or [])


def member_active(member):
    # Ausgetreten: sofort. Sonst aktiv bis einschliesslich Austrittsdatum,
    # deaktiviert ab dem Folgetag.
    props = member['properties']
    end = props['Austrittsdatum']
    return props['Status'] != 'Ausgetreten' and (not end or date.fromisoformat(end) >= date.today())


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
    missing = auth.missing_groups(statuses | funktionen)
    if missing:
        sys.exit(f"FEHLER: Gruppen fehlen in Authentik (in den Blueprint aufnehmen): {sorted(missing)}")


def sync_member(auth, wbl, member):
    props = member['properties']
    member_id = props['Mitglieder ID']
    email = wbl.authentik_email(member)
    if not email:
        print(f"WARNUNG: Mitglied {member_id} ({props['Vorname']} {props['Name']}) hat keine E-Mail, übersprungen")
        return None
    name = f"{props['Vorname'].strip()} {props['Name'].strip()}"
    login = unix_name(f"{name}.{member_id}")
    groups = groups_of(member)
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
    else:
        print(f"Angelegt {email}: name={name}, aktiv={active}, gruppen={groups}")
        auth.create_user(email=email, login=login, display_name=name,
                         active=active, groupnames=groups, attributes=attributes)
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
    check_groups(auth, wbl.members)
    check_zulip_emails(wbl, zulip_users())

    synced_ids = set()
    for member in wbl.members:
        member_id = sync_member(auth, wbl, member)
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
