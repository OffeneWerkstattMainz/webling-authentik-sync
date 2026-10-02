import os
import re
import requests
import json
from utils import env_value, lower

# Nur die einmaligen Migrationsskripte brauchen diese Maps (search_email);
# nach der ersten Passwort-Migration in Produktion entfernen.
webling_to_zulip = json.loads(os.getenv('WEBLING_TO_ZULIP_MAP') or '{}')

# Schlüssel = Zulip-E-Mail, Wert = Webling-E-Mail (None = geprüft, kein Webling-Mitglied)
zulip_to_webling = json.loads(os.getenv('ZULIP_TO_WEBLING_MAP') or '{}')

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
            json_data = response.json()
            return json_data
        except requests.exceptions.RequestException as e:
            print(f"Fehler beim Aufruf von {url}")
            raise e

    def fetch_members(self):
        self.members = self.get_json_from_url(f'{self.api_url}/member?format=full')

    def find_by_property(self, prop_name, value, value_function=None, return_first=False):
        if(not self.members):
            self.fetch_members()

        result = []
        for m in self.members:
            found_val = m['properties'][prop_name]
            searched_val = value
            if(value_function):
                found_val = value_function(found_val)
                searched_val = value_function(searched_val)

            if (found_val == searched_val):
                if(return_first):
                    return m
                else:
                    result.append(m)

        return result

    def search_email(self, email):
        e = lower(email)
        m = self.find_by_property('E-Mail', email, lower, True)
        if(m):
            return m

        m = self.find_by_property('Zulip E-Mail', email, lower, True)
        if(m):
            return m

        try:
            webling = zulip_to_webling[e]
        except KeyError:
            webling = None

        if(webling):
            return self.search_email(webling)

        webling = [key for key, value in webling_to_zulip.items() if value == e]
        if(len(webling) > 0):
            return self.search_email(webling[0])

    # E-Mail für das Authentik-Konto: 'Zulip E-Mail', falls gesetzt und keine
    # Zulip-Platzhalteradresse (user<N>@zulip.ow-mainz.de); ansonsten 'E-Mail'.
    def authentik_email(self, member):
        zulip_email = (member['properties'].get('Zulip E-Mail') or '').strip()
        if zulip_email and not re.fullmatch(r'user\d+@zulip\.ow-mainz\.de', zulip_email):
            return zulip_email
        return (member['properties']['E-Mail'] or '').strip()

    def firstname(self, member):
        return member['properties']['Vorname'].strip()

    def lastname(self, member):
        return member['properties']['Name'].strip()

    def memberid(self, member):
        return member['properties']['Mitglieder ID']
