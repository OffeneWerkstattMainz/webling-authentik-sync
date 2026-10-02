#!/usr/bin/env python3
"""Einmalig: die bestehende Nextcloud-Benutzer-ID jedes Mitglieds in Authentik hinterlegen.

Aufruf: set-nextcloud-uids.py <nextcloud-users.tsv> [report.txt]

Die TSV-Datei enthält pro Nextcloud-Benutzer eine Zeile "uid<TAB>email":

  SELECT userid, configvalue FROM oc_preferences
  WHERE appid='settings' AND configkey='email'

Jedes Nextcloud-Konto wird über die E-Mail einem Webling-Mitglied zugeordnet
(gleiche Zuordnung wie bei der Passwort-Migration); die Nextcloud-UID wird dann
in das Authentik-Benutzerattribut nextcloud_user_id geschrieben. Der OIDC-Claim
"user_id" (Blueprint nextcloud-oidc.yaml) bevorzugt dieses Attribut, so dass
OIDC-Logins das bestehende Nextcloud-Konto samt Dateien übernehmen. Benutzer
ohne das Attribut bekommen den Authentik-Benutzernamen (neue Konten).

Manuelle Zuordnungen für Nextcloud-Konten, die die E-Mail-Suche nicht auflösen
kann, kommen in die optionale Umgebungsvariable NEXTCLOUD_UID_MAP (JSON,
Nextcloud-UID -> Webling-E-Mail, null = geprüft, kein Mitglied). Der reguläre
Sync erhält das Attribut (update_user führt Attribute zusammen), das Skript
muss also nur bei neuen manuellen Zuordnungen erneut laufen.
"""
from dotenv import load_dotenv
load_dotenv()

import json
import os
import sys

from authentikclient import AuthentikClient
from weblingclient import WeblingClient

# Nextcloud-UID -> Webling-E-Mail (null = geprüft, kein Webling-Mitglied)
UID_MAP = json.loads(os.getenv('NEXTCLOUD_UID_MAP') or '{}')


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    tsv_path = sys.argv[1]
    report_path = sys.argv[2] if len(sys.argv) > 2 else 'nextcloud-uid-report.txt'

    wbl = WeblingClient()
    wbl.fetch_members()
    auth = AuthentikClient()
    auth.connect()

    assigned, unmatched, known_unmatched, no_authentik, collisions = [], [], [], [], []
    claimed = {}  # authentik pk -> nextcloud uid

    with open(tsv_path) as f:
        rows = [line.rstrip('\n').partition('\t') for line in f if line.strip()]

    for nc_uid, _, email in rows:
        email = email.strip()
        if nc_uid in UID_MAP:
            target = UID_MAP[nc_uid]
            if target is None:
                known_unmatched.append(f"{nc_uid} ({email})")
                continue
            member = wbl.search_email(target)
        else:
            member = wbl.search_email(email) if email else None
        if not member:
            unmatched.append(f"{nc_uid} ({email or 'no email'})")
            continue
        user = (auth.find_by_webling_id(wbl.memberid(member))
                or auth.find_by_email(wbl.authentik_email(member)))
        if not user:
            no_authentik.append(f"{nc_uid} ({email}) -> {wbl.authentik_email(member)}")
            continue
        if user.pk in claimed:
            collisions.append(f"{nc_uid} and {claimed[user.pk]} both match {user.email}")
            continue
        claimed[user.pk] = nc_uid
        auth.set_attribute(user, 'nextcloud_user_id', nc_uid)
        assigned.append(f"{nc_uid} -> {user.email}")
        print(f"nextcloud_user_id={nc_uid} gesetzt für {user.email}")

    with open(report_path, 'w') as f:
        def section(title, lines):
            f.write(f"# {title} ({len(lines)})\n\n")
            f.writelines(line + '\n' for line in lines)
            f.write('\n')
        section("Zugewiesen", assigned)
        section("Nextcloud-Konten ohne Webling-Treffer (prüfen, dann in NEXTCLOUD_UID_MAP eintragen)", unmatched)
        section("Bekannte Nicht-Mitglieder (NEXTCLOUD_UID_MAP: null)", known_unmatched)
        section("Zugeordnet, aber kein Authentik-Benutzer", no_authentik)
        section("Kollisionen (zwei Nextcloud-Konten, ein Mitglied) - über NEXTCLOUD_UID_MAP auflösen", collisions)

    print(f"\nFertig: {len(assigned)} zugewiesen, {len(unmatched)} ohne Zuordnung, "
          f"{len(collisions)} Kollisionen.\nReport: {report_path}")


if __name__ == '__main__':
    main()
