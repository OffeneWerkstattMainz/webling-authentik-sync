import re
import requests
import urllib3
from utils import env_value, lower

TIMEOUT = 60


class WeblingError(Exception):
    pass


class WeblingClient:

    def __init__(self):
        self.api_key = env_value('WEBLING_API_KEY')
        self.api_url = env_value('WEBLING_API_URL')
        self.members = None

    # Jeder Fehlschlag wird zu einem WeblingError mit einer lesbaren Meldung für
    # den Zulip-Alarm; die Originalausnahme hängt als __cause__ daran und steht
    # im Stacktrace des Container-Logs.
    def get_json_from_url(self, url):
        headers = {
            'apikey': f'{self.api_key}'
        }
        try:
            response = requests.get(url, headers=headers, timeout=TIMEOUT)
        # Timeout vor ConnectionError: ConnectTimeout ist beides.
        except requests.exceptions.Timeout as e:
            raise WeblingError(f"Webling antwortet nicht innerhalb von {TIMEOUT} s ({url})") from e
        except requests.exceptions.SSLError as e:
            raise WeblingError(f"TLS-Fehler bei der Verbindung zu Webling ({url})") from e
        except requests.exceptions.ConnectionError as e:
            reason = getattr(e.args[0], 'reason', None) if e.args else None
            if isinstance(reason, urllib3.exceptions.NameResolutionError):
                raise WeblingError(f"Webling-Host nicht per DNS auflösbar ({url})") from e
            raise WeblingError(f"Keine Verbindung zu Webling ({url})") from e
        except requests.exceptions.RequestException as e:
            raise WeblingError(f"Abruf von Webling fehlgeschlagen ({url}): {e}") from e

        status = f"HTTP {response.status_code} {response.reason}"
        if response.status_code in (401, 403):
            raise WeblingError(f"Webling lehnt den API-Key ab ({status}, {url})")
        if response.status_code >= 500:
            raise WeblingError(f"Serverfehler bei Webling ({status}, {url})")
        if response.status_code >= 400:
            raise WeblingError(f"Webling lehnt die Anfrage ab ({status}, {url})")
        if not response.content.strip():
            raise WeblingError(f"Webling lieferte eine leere Antwort ({status}, {url})")
        try:
            return response.json()
        except ValueError as e:
            content_type = response.headers.get('Content-Type', 'unbekannt')
            raise WeblingError(f"Webling lieferte kein JSON ({status}, Content-Type {content_type}, {url})") from e

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
