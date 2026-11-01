#!/usr/bin/env python3
"""Einmalige Migration der argon2-Passwort-Hashes von Zulip nach Authentik.

Aufruf: migrate-passwords.py <zulip-users.csv> [report.txt]

Die CSV-Datei ist ein Dump von zerver_userprofile (Spalten siehe unten):

  COPY (SELECT delivery_email, full_name, password, is_active, is_bot
        FROM zerver_userprofile ORDER BY delivery_email) TO STDOUT WITH CSV HEADER

Jeder Zulip-Benutzer wird über die Felder 'E-Mail' und 'Zulip E-Mail' einem
Webling-Mitglied zugeordnet, dann wird der argon2-Hash in authentik_core_user beim
Benutzer mit der Webling-E-Mail eingetragen. Zulip und Authentik speichern
beide argon2-Hashes im Django-Format, der Hash wird also unverändert kopiert.
Die API nimmt keine Hashes an, daher der direkte DB-Schreibzugriff
(Umgebungsvariablen AUTHENTIK_DB_*).

Kann wiederholt werden: ein Passwort, das der Benutzer in Authentik gesetzt hat,
wird nicht überschrieben (siehe AuthentikDb.update_password).

Der Report listet migrierte Benutzer mit abweichender Zulip-/Webling-E-Mail,
in Authentik gesetzte und deshalb behaltene Passwörter,
Zulip-Benutzer ohne Webling-Treffer und Webling-Mitglieder ohne Zulip-Konto
(kein Passwort zu migrieren).
"""
from dotenv import load_dotenv
load_dotenv()

import csv
import sys

import json

from dbclient import AuthentikDb
from utils import env_value
from weblingclient import WeblingClient

# Zulip-Konten, die übersprungen werden (Bots, Organisationskonten)
ignore_emails = json.loads(env_value('IGNORE_EMAILS'))


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    csv_path = sys.argv[1]
    report_path = sys.argv[2] if len(sys.argv) > 2 else 'password-migration-report.txt'

    wbl = WeblingClient()
    wbl.fetch_members()

    auth_db = AuthentikDb(
        dbname = env_value('AUTHENTIK_DB_NAME'),
        user = env_value('AUTHENTIK_DB_USER'),
        password = env_value('AUTHENTIK_DB_PASSWORD'),
        host = env_value('AUTHENTIK_DB_HOST'),
        port = env_value('AUTHENTIK_DB_PORT'))
    auth_db.connect()

    migrated, email_differs, unmatched, not_in_authentik, no_hash, kept = [], [], [], [], [], []
    matched_webling_emails = set()

    with open(csv_path, newline='') as f:
        for row in csv.DictReader(f):
            zulip_email = row['delivery_email'].strip()
            if row['is_bot'] == 't' or zulip_email.lower() in ignore_emails:
                continue
            member = wbl.search_email(zulip_email)
            if not member:
                unmatched.append(f"{zulip_email} ({row['full_name']}, active={row['is_active']})")
                continue
            webling_email = wbl.authentik_email(member)
            matched_webling_emails.add(webling_email.lower())
            if not row['password'].startswith('argon2$'):
                no_hash.append(f"{zulip_email} ({row['full_name']})")
                continue
            result = auth_db.update_password(webling_email, row['password'])
            if result == 'missing':
                not_in_authentik.append(f"{webling_email} (zulip: {zulip_email})")
                continue
            if result == 'kept':
                kept.append(f"{webling_email} (zulip: {zulip_email})")
                continue
            migrated.append(zulip_email)
            if webling_email.lower() != zulip_email.lower():
                email_differs.append(f"webling: {webling_email} <- zulip: {zulip_email}")
            print(f"Passwort von {webling_email} migriert")

    no_zulip = [f"{wbl.authentik_email(m)} ({wbl.firstname(m)} {wbl.lastname(m)})"
                for m in wbl.members
                if wbl.authentik_email(m) and wbl.authentik_email(m).lower() not in matched_webling_emails]

    with open(report_path, 'w') as f:
        def section(title, lines):
            f.write(f"# {title} ({len(lines)})\n\n")
            f.writelines(line + '\n' for line in lines)
            f.write('\n')
        section("Migriert", migrated)
        section("Migriert, Webling- und Zulip-E-Mail unterschiedlich", email_differs)
        section("Zulip-Benutzer ohne Webling-Treffer", unmatched)
        section("Zugeordnet, aber kein Authentik-Benutzer mit der Webling-E-Mail", not_in_authentik)
        section("Passwort in Authentik selbst gesetzt, nicht überschrieben", kept)
        section("Zugeordnet, aber kein argon2-Hash in Zulip (nie ein Passwort gesetzt)", no_hash)
        section("Webling-Mitglieder ohne Zulip-Konto (kein Passwort migriert)", no_zulip)

    print(f"\nFertig: {len(migrated)} migriert, {len(unmatched)} Zulip-Benutzer ohne Zuordnung, "
          f"{len(no_zulip)} Webling-Mitglieder ohne Zulip-Konto.\nReport: {report_path}")


if __name__ == '__main__':
    main()
