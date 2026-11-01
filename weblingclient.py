import re
import requests
from utils import env_value, lower


class WeblingClient:

    def __init__(self):
        self.api_key = env_value('WEBLING_API_KEY')
        self.api_url = env_value('WEBLING_API_URL')
        self.members = None

    def get_json_from_url(self, url):
        headers = {
            'apikey': f'{self.api_key}'
        }
        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Fehler beim Aufruf von {url}")
            raise e

    def fetch_members(self):
        self.members = self.get_json_from_url(f'{self.api_url}/member?format=full')

    def find_by_property(self, prop_name, value, value_function=None):
        if not self.members:
            self.fetch_members()
        if value_function:
            value = value_function(value)
        for m in self.members:
            found = m['properties'][prop_name]
            if value_function:
                found = value_function(found)
            if found == value:
                return m

    # Mitglied zu einer Adresse: erst 'E-Mail', dann 'Zulip E-Mail'.
    def search_email(self, email):
        return (self.find_by_property('E-Mail', email, lower)
                or self.find_by_property('Zulip E-Mail', email, lower))

    # 'Zulip E-Mail' des Mitglieds, leer wenn nicht gesetzt oder nur eine
    # Zulip-Platzhalteradresse (user<N>@zulip.ow-mainz.de).
    def zulip_email(self, member):
        zulip_email = (member['properties'].get('Zulip E-Mail') or '').strip()
        if re.fullmatch(r'user\d+@zulip\.ow-mainz\.de', zulip_email):
            return ''
        return zulip_email

    # E-Mail des Authentik-Kontos: 'Zulip E-Mail', falls gesetzt, sonst 'E-Mail'.
    # Kleingeschrieben: Zulips SCIM vergleicht die Adresse beim Aktualisieren mit
    # Groß-/Kleinschreibung, die Eindeutigkeitsprüfung ohne; abweichende
    # Schreibweisen enden dort mit 409 "Email address already in use".
    def authentik_email(self, member):
        return (self.zulip_email(member) or (member['properties']['E-Mail'] or '').strip()).lower()

    def firstname(self, member):
        return member['properties']['Vorname'].strip()

    def lastname(self, member):
        return member['properties']['Name'].strip()

    def memberid(self, member):
        return member['properties']['Mitglieder ID']
