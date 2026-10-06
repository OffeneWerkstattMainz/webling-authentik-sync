#!/usr/bin/env python3
"""Offline-Prüfung der Gruppen- und Aktivitätsregeln des Syncs: python test_sync.py"""
import importlib.util
import os
from datetime import date, timedelta

os.environ.setdefault('WEBLING_API_KEY', 'x')
os.environ.setdefault('WEBLING_API_URL', 'x')
os.environ.setdefault('AUTHENTIK_API_URL', 'x')
os.environ.setdefault('AUTHENTIK_API_TOKEN', 'x')
spec = importlib.util.spec_from_file_location('sync', 'webling-authentik-sync.py')
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


def member(status, funktion=None, end=None, member_id=1):
    return {'properties': {'Status': status, 'Funktion': funktion, 'Austrittsdatum': end,
                           'Mitglieder ID': member_id, 'Vorname': 'A', 'Name': 'B'}}


class FakeAuth:
    def __init__(self, names, by_email=None): self.names, self.by_email, self.calls = set(names), by_email, []
    def missing_groups(self, names): return set(names) - self.names
    def find_by_webling_id(self, webling_id): return None
    def find_by_email(self, email): return self.by_email
    def update_user(self, *a, **kw): self.calls.append('update'); return True
    def create_user(self, *a, **kw): self.calls.append('create')


class FakeWbl:
    def authentik_email(self, member): return 'shared@example.org'


class User:
    def __init__(self, webling_id): self.attributes = {'webling_id': webling_id}


today = date.today().isoformat()
yesterday = (date.today() - timedelta(days=1)).isoformat()

assert sync.groups_of(member('Mitglied', ['Vorstand'])) == ['Mitglied', 'Vorstand']
assert sync.groups_of(member('Anwartschaft')) == ['Anwartschaft']
assert sync.member_active(member('Mitglied'))
assert sync.member_active(member('Mitglied', end=today))
assert not sync.member_active(member('Mitglied', end=yesterday))
assert not sync.member_active(member('Ausgetreten'))

members = [member('Mitglied', ['Vorstand']), member('Anwartschaft')]
sync.check_groups(FakeAuth(['Mitglied', 'Vorstand', 'Anwartschaft']), members)
for auth, bad in [(FakeAuth(['Mitglied', 'Vorstand']), members),
                  (FakeAuth(['Mitglied', 'Vorstand']), [member('Vorstand', ['Vorstand'])])]:
    try:
        sync.check_groups(auth, bad)
        raise AssertionError('check_groups sollte eigentlich abbrechen, wenn Funktion und Status gleich heißen')
    except SystemExit as e:
        assert str(e).startswith('FEHLER')

# gemeinsame E-Mail: der Benutzer eines anderen Mitglieds wird nie übernommen
auth = FakeAuth([], by_email=User(128))
assert sync.sync_member(auth, FakeWbl(), member('Mitglied', member_id=399)) is None and auth.calls == []
assert sync.sync_member(auth, FakeWbl(), member('Mitglied', member_id=128)) == 128 and auth.calls == ['update']

# Zulip check: Webling 'Zulip E-Mail' unknown to Zulip, and active Zulip accounts without member
import io, contextlib
from weblingclient import WeblingClient
wbl = WeblingClient.__new__(WeblingClient)
wbl.members = [
    {'properties': {'Mitglieder ID': 1, 'Vorname': 'A', 'Name': 'B', 'E-Mail': 'a@x.org', 'Zulip E-Mail': 'a@zulip.org'}},
    {'properties': {'Mitglieder ID': 2, 'Vorname': 'C', 'Name': 'D', 'E-Mail': 'c@x.org', 'Zulip E-Mail': 'wrong@zulip.org'}},
    {'properties': {'Mitglieder ID': 3, 'Vorname': 'E', 'Name': 'F', 'E-Mail': 'e@x.org', 'Zulip E-Mail': 'user7@zulip.ow-mainz.de'}},
]
out = io.StringIO()
with contextlib.redirect_stdout(out):
    sync.check_zulip_emails(wbl, [('a@zulip.org', 'A B', True), ('E@X.ORG', 'E F', True),
                                  ('stranger@zulip.org', 'S T', True), ('gone@zulip.org', 'G O', False)])
    sync.check_zulip_emails(wbl, None)
warnings = out.getvalue()
assert warnings.count('WARNUNG') == 2 and 'wrong@zulip.org' in warnings and 'stranger@zulip.org' in warnings, warnings
assert wbl.authentik_email(wbl.members[2]) == 'e@x.org'
print('ok')
