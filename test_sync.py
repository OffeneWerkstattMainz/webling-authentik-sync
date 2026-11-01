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
    def create_user(self, *a, **kw): self.calls.append('create'); return 'new-user'
    def send_recovery_email(self, user, stage, duration): self.calls.append(('mail', user, stage)); return True


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
os.environ['NON_MEMBER_VALUES'] = '["Ausgeschlossen"]'
assert not sync.member_active(member('Ausgeschlossen')) and sync.member_active(member('Mitglied'))
os.environ.pop('NON_MEMBER_VALUES')
assert sync.member_active(member('Ausgeschlossen'))

members = [member('Mitglied', ['Vorstand']), member('Anwartschaft')]
sync.check_groups(FakeAuth(['Mitglied', 'Vorstand', 'Anwartschaft']), members)
try:
    sync.check_groups(FakeAuth(['Mitglied', 'Vorstand']), [member('Vorstand', ['Vorstand'])])
    raise AssertionError('check_groups sollte eigentlich abbrechen, wenn Funktion und Status gleich heißen')
except SystemExit as e:
    assert str(e).startswith('FEHLER')

# fehlende Gruppe: kein Abbruch, WARNUNG, Meldung nur bei geänderter Menge, NON_MEMBER_VALUES still
import io, contextlib
alerts = []
sync.alert = alerts.append
out = io.StringIO()
with contextlib.redirect_stdout(out):
    assert sync.check_groups(FakeAuth(['Mitglied', 'Vorstand']), members) == {'Anwartschaft'}
    sync.check_groups(FakeAuth(['Mitglied', 'Vorstand']), members)
assert out.getvalue().count('WARNUNG') == 2 and len(alerts) == 1 and 'Anwartschaft' in alerts[0], (out.getvalue(), alerts)
os.environ['NON_MEMBER_VALUES'] = '["Anwartschaft"]'
out = io.StringIO()
with contextlib.redirect_stdout(out):
    assert sync.check_groups(FakeAuth(['Mitglied', 'Vorstand']), members) == {'Anwartschaft'}
assert 'WARNUNG' not in out.getvalue() and len(alerts) == 1, (out.getvalue(), alerts)
os.environ.pop('NON_MEMBER_VALUES')

# unbekannter Status: neues Mitglied wird nicht angelegt; unbekannte Funktion: angelegt ohne diese Gruppe;
# vorhandenes Mitglied behält die bekannten Gruppen
auth = FakeAuth([])
assert sync.sync_member(auth, FakeWbl(), member('Erfasst'), {'Erfasst'}) is None and auth.calls == []
class RecordingAuth(FakeAuth):
    def update_user(self, *a, **kw): self.calls.append(kw['groupnames']); return True
    def create_user(self, *a, **kw): self.calls.append(('create', kw['groupnames'])); return 'new-user'
auth = RecordingAuth([])
assert sync.sync_member(auth, FakeWbl(), member('Mitglied', ['Neu']), {'Neu'}) == 1 and auth.calls == [('create', ['Mitglied'])], auth.calls
auth = RecordingAuth([], by_email=User(1))
assert sync.sync_member(auth, FakeWbl(), member('Mitglied', ['Neu']), {'Neu'}) == 1 and auth.calls == [['Mitglied']], auth.calls

# gemeinsame E-Mail: der Benutzer eines anderen Mitglieds wird nie übernommen
auth = FakeAuth([], by_email=User(128))
assert sync.sync_member(auth, FakeWbl(), member('Mitglied', member_id=399)) is None and auth.calls == []
assert sync.sync_member(auth, FakeWbl(), member('Mitglied', member_id=128)) == 128 and auth.calls == ['update']

# Willkommens-Mail: nur neu angelegte, aktive Mitglieder und nur mit ONBOARDING_EMAIL_STAGE
os.environ.pop('ONBOARDING_EMAIL_STAGE', None)
auth = FakeAuth([])
sync.sync_member(auth, FakeWbl(), member('Mitglied'))
assert auth.calls == ['create'], auth.calls
os.environ['ONBOARDING_EMAIL_STAGE'] = 'owm-onboarding-email'
for m, expected in [(member('Mitglied'), ['create', ('mail', 'new-user', 'owm-onboarding-email')]),
                    (member('Ausgetreten'), ['create'])]:
    auth = FakeAuth([])
    sync.sync_member(auth, FakeWbl(), m)
    assert auth.calls == expected, auth.calls
auth = FakeAuth([], by_email=User(1))
sync.sync_member(auth, FakeWbl(), member('Mitglied'))
assert auth.calls == ['update'], auth.calls
os.environ.pop('ONBOARDING_EMAIL_STAGE')

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
wbl.members.append({'properties': {'Mitglieder ID': 4, 'Vorname': 'G', 'Name': 'H', 'E-Mail': ' Mixed.Case@X.org ', 'Zulip E-Mail': None}})
assert wbl.authentik_email(wbl.members[3]) == 'mixed.case@x.org'
# Zulip-Deaktivierung: nur inaktive Webling-Benutzer mit aktivem Zulip-Konto, nie Owner, nur mit ZULIP_DEACTIVATE=1
class AkUser:
    def __init__(self, email, active, webling_id=1):
        self.email, self.is_active, self.attributes = email, active, ({'webling_id': webling_id} if webling_id else {})
class ListAuth:
    def list_users(self):
        return [AkUser('gone@x.org', False), AkUser('Owner@x.org', False), AkUser('here@x.org', True),
                AkUser('admin@x.org', False, webling_id=None), AkUser('nozulip@x.org', False),
                AkUser('already@x.org', False)]
zusers = [('gone@x.org', 'G', True, 11, 400), ('owner@x.org', 'O', True, 12, 100), ('here@x.org', 'H', True, 13, 400),
          ('admin@x.org', 'A', True, 14, 200), ('already@x.org', 'X', False, 15, 400)]
deleted = []
class Resp:
    def raise_for_status(self): pass
sync.requests.delete = lambda url, **kw: deleted.append(url) or Resp()
os.environ.update(ZULIP_URL='https://z', ZULIP_API_USER='u', ZULIP_API_KEY='k')
sync.deactivate_zulip_accounts(ListAuth(), zusers)
assert deleted == [], deleted
os.environ['ZULIP_DEACTIVATE'] = '1'
out = io.StringIO()
with contextlib.redirect_stdout(out):
    sync.deactivate_zulip_accounts(ListAuth(), zusers)
    sync.deactivate_zulip_accounts(ListAuth(), None)
assert deleted == ['https://z/api/v1/users/11'], deleted
assert 'owner@x.org gehört einem Owner' in out.getvalue(), out.getvalue()
os.environ.pop('ZULIP_DEACTIVATE')
# Nextcloud-Deaktivierung: nur inaktive Webling-Benutzer mit aktivem Konto, nie der API-Benutzer
class NcUser(AkUser):
    def __init__(self, email, active, username, ncid=None, webling_id=1):
        super().__init__(email, active, webling_id)
        self.username = username
        if ncid: self.attributes['nextcloud_user_id'] = ncid
class NcAuth:
    def list_users(self):
        return [NcUser('a@x', False, 'anna_1', 'AnnaN'), NcUser('b@x', False, 'bob_2'), NcUser('c@x', True, 'carl_3', 'CarlN'),
                NcUser('d@x', False, 'dora_4', 'DoraN'), NcUser('e@x', False, 'eve_5', 'gone'), NcUser('f@x', False, 'api', 'api'),
                NcUser('g@x', False, 'gus_7', 'GusN', webling_id=None)]
nc_state = {'AnnaN': True, 'bob_2': True, 'CarlN': True, 'DoraN': False, 'api': True, 'GusN': True}
calls = []
class NcResp:
    def __init__(self, code, data=None): self.status_code, self.data = code, data
    def raise_for_status(self):
        if self.status_code >= 400: raise sync.requests.exceptions.HTTPError(self.status_code)
    def json(self): return {'ocs': {'data': self.data}}
class NcSession:
    def __init__(self): self.headers = {}
    def get(self, url, **kw):
        uid = url.rsplit('/', 1)[1]
        return NcResp(200, {'enabled': nc_state[uid]}) if uid in nc_state else NcResp(404)
    def put(self, url, **kw):
        calls.append(url); return NcResp(200)
sync.requests.Session = NcSession
os.environ.update(NEXTCLOUD_URL='https://nc', NEXTCLOUD_API_USER='api', NEXTCLOUD_API_PASSWORD='pw')
sync.deactivate_nextcloud_accounts(NcAuth())
assert calls == [], calls
os.environ['NEXTCLOUD_DEACTIVATE'] = '1'
with contextlib.redirect_stdout(io.StringIO()):
    sync.deactivate_nextcloud_accounts(NcAuth())
assert calls == ['https://nc/ocs/v2.php/cloud/users/AnnaN/disable', 'https://nc/ocs/v2.php/cloud/users/bob_2/disable'], calls
os.environ.pop('NEXTCLOUD_DEACTIVATE')
# Zulip-Namen: abweichender Zulip-Name landet in zulip_full_name, gleicher Name setzt None
class NameUser(AkUser):
    def __init__(self, email, name, attrs=None):
        super().__init__(email, True)
        self.name = name
        self.attributes.update(attrs or {})
class NameAuth:
    def __init__(self): self.set = []
    def list_users(self):
        return [NameUser('nick@x', 'Kambiz Darabi'), NameUser('same@x', 'Anna B'),
                NameUser('back@x', 'Toni S', {'zulip_full_name': 'Toni'}), NameUser('kept@x', 'Max M', {'zulip_full_name': 'Maxi'}),
                NameUser('nozulip@x', 'No Z')]
    def set_attribute(self, user, key, value): self.set.append((user.email, key, value))
nusers = [('nick@x', 'Kami Darabi', True, 1, 400), ('same@x', 'Anna B', True, 2, 400), ('back@x', 'Toni S', True, 3, 400),
          ('kept@x', 'Maxi', True, 4, 400)]
na = NameAuth()
with contextlib.redirect_stdout(io.StringIO()):
    sync.sync_zulip_names(na, nusers)
    sync.sync_zulip_names(na, None)
assert na.set == [('nick@x', 'zulip_full_name', 'Kami Darabi'), ('back@x', 'zulip_full_name', None)], na.set
print('ok')
