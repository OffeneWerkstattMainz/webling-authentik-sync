from authentik_client.rest import ApiException
from utils import env_value
import authentik_client
import json

# Die Python-Client-Bibliothek wird automatisch aus der OpenAPI-Beschreibung generiert.
#
# Der Quellcode einer bestimmten Version liegt im Repo unter dem jeweiligen Tag:
#
# https://github.com/goauthentik/client-python/tree/version-2026.2
#
# Die Links zu den Methoden auf PyPI sind tot:
#
# https://pypi.org/project/authentik-client/
#
# aber es gibt eine ausführliche Dokumentation unter
#
# https://api.goauthentik.io/
#
# Jede Authentik-Instanz hat ihren eigenen OpenAPI-Browser unter
#
# https://<host>:port/api/v3/
#
class AuthentikClient:
    def __init__(self, debug=False):
        self.configuration = authentik_client.Configuration(
            # api/v3 wird NICHT automatisch angehängt
            host = env_value('AUTHENTIK_API_URL'),
            access_token = env_value('AUTHENTIK_API_TOKEN'),
            debug = debug
        )
        self.api_client = authentik_client.ApiClient(self.configuration)
        self.api = authentik_client.CoreApi(self.api_client)
        self.current_groups = []

    def connect(self):
        self._retrieve_current_groups()

    def _retrieve_current_groups(self):
        try:
            response = self.api.core_groups_list()
            self.current_groups = [ { "name": g.name, "uuid": str(g.pk) }
                                    for g in response.results ]
        except ApiException as e:
            print(f"Gruppenliste konnte nicht aus Authentik geladen werden: {e}")
            raise e

    def missing_groups(self, names):
        current = { g['name'] for g in self.current_groups }
        return set(names) - current

    def group_uuid(self, groupname):
        for g in self.current_groups:
            if g['name'] == groupname:
                return g['uuid']

    def find_by_webling_id(self, webling_id):
        try:
            response = self.api.core_users_list(
                attributes = json.dumps({'webling_id': webling_id}))
            if len(response.results) > 0:
                return response.results[0]
        except ApiException as e:
            print(f"Suche nach Benutzer mit webling_id {webling_id} in Authentik fehlgeschlagen: {e}")
            raise e

    def find_by_email(self, email):
        try:
            response = self.api.core_users_list(email=email)
            if len(response.results) > 0:
                return response.results[0]
        except ApiException as e:
            print(f"Suche nach Benutzer mit E-Mail {email} in Authentik fehlgeschlagen: {e}")
            raise e

    def list_users(self):
        users = []
        page = 1
        while True:
            response = self.api.core_users_list(page=page)
            users.extend(response.results)
            if not response.pagination.next:
                return users
            page = int(response.pagination.next)

    # Die Gruppen müssen existieren (Blueprint). Die vergebenen Gruppennamen
    # werden im Benutzerattribut webling_synced_groups festgehalten; beim Update
    # werden dort verzeichnete Mitgliedschaften, die nicht mehr in groupnames
    # stehen, entfernt. In Authentik manuell vergebene Mitgliedschaften bleiben.
    # Liefert den angelegten Benutzer.
    def create_user(self, *, email, login, display_name, active, groupnames, attributes={}):
        try:
            request = authentik_client.models.UserRequest(
                username = login,
                name = display_name,
                is_active = active,
                groups = [ self.group_uuid(g) for g in groupnames ],
                email = email,
                attributes = { **attributes, 'webling_synced_groups': groupnames },
            )
            return self.api.core_users_create(request)
        except ApiException as e:
            print(f"Benutzer {display_name} ({email}) konnte in Authentik nicht angelegt werden: {e}")
            raise e

    # Schickt eine Mail über die Recovery-API (Link in den Recovery-Flow der
    # Brand im Kontext als url). stage_name ist die E-Mail-Stage, deren Betreff
    # und Vorlage verwendet werden. Der Service-Account braucht dafür
    # authentik_core.reset_user_password und authentik_stages_email.view_emailstage.
    # Ein Fehler wird nur gemeldet: der Benutzer ist schon angelegt, das Passwort
    # kann er auch über "Passwort vergessen?" setzen.
    def send_recovery_email(self, authentik_user, stage_name, token_duration):
        try:
            stages = authentik_client.StagesApi(self.api_client).stages_email_list(name=stage_name).results
            if not stages:
                print(f"WARNUNG: E-Mail-Stage {stage_name} fehlt in Authentik, keine Mail an {authentik_user.email}")
                return False
            request = authentik_client.models.UserRecoveryEmailRequest(
                email_stage = stages[0].pk, token_duration = token_duration)
            self.api.core_users_recovery_email_create(authentik_user.pk, request)
            return True
        except ApiException as e:
            print(f"WARNUNG: Mail mit Stage {stage_name} an {authentik_user.email} fehlgeschlagen: {e}")
            return False

    # Liefert True, wenn etwas geändert werden musste.
    def update_user(self, authentik_user, *, email, login, display_name, active,
                    groupnames, attributes={}):
        old_attributes = authentik_user.attributes or {}
        # UUIDs der Gruppen, die dieser Sync beim letzten Mal vergeben hat (gelöschte Gruppen: None)
        previous = { self.group_uuid(g)
                     for g in old_attributes.get('webling_synced_groups', []) }
        kept = [ str(g) for g in authentik_user.groups if str(g) not in previous ]
        groups = kept + [ uuid for g in groupnames
                          if (uuid := self.group_uuid(g)) not in kept ]
        attributes = { **old_attributes, **attributes, 'webling_synced_groups': groupnames }
        if (authentik_user.username, authentik_user.name, authentik_user.is_active,
            authentik_user.email, set(str(g) for g in authentik_user.groups),
            authentik_user.attributes or {}) == \
           (login, display_name, active, email, set(groups), attributes):
            return False
        try:
            request = authentik_client.models.UserRequest(
                username = login,
                name = display_name,
                is_active = active,
                groups = groups,
                email = email,
                attributes = attributes,
            )
            self.api.core_users_update(authentik_user.pk, request)
            return True
        except ApiException as e:
            print(f"Benutzer {display_name} ({email}) konnte in Authentik nicht aktualisiert werden: {e}")
            raise e

    def set_attribute(self, authentik_user, key, value):
        try:
            request = authentik_client.models.PatchedUserRequest(
                attributes = { **(authentik_user.attributes or {}), key: value })
            self.api.core_users_partial_update(authentik_user.pk, request)
        except ApiException as e:
            print(f"Attribut {key} konnte bei {authentik_user.email} in Authentik nicht gesetzt werden: {e}")
            raise e

    def deactivate_user(self, authentik_user):
        try:
            request = authentik_client.models.PatchedUserRequest(is_active=False)
            self.api.core_users_partial_update(authentik_user.pk, request)
        except ApiException as e:
            print(f"Benutzer {authentik_user.email} konnte in Authentik nicht deaktiviert werden: {e}")
            raise e
