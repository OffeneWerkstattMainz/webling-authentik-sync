import psycopg2

from abc import ABC, abstractmethod

class DbClient(ABC):

    def __init__(self, *, dbname, user, password, host, port):
        # Verbindungsparameter der Datenbank
        self.db_params = {
            'dbname': dbname,
            'user': user,
            'password': password,
            'host': host,
            'port': port
        }
        self.conn = None
        self.members = None

    def connect(self):
        try:
            self.conn = psycopg2.connect(**self.db_params)
        except psycopg2.Error as e:
            print(f"Datenbankfehler: {e}")
            raise e

    def disconnect(self):
        if self.conn:
            self.conn.close()

    def check_conn(self):
        if not self.conn:
            raise Exception("Keine DB-Verbindung")


class AuthentikDb(DbClient):

    # Die API nimmt keine Passwort-Hashes an: wir müssen direkt in die DB schreiben
    def update_password(self, email, password_hash):
        self.check_conn()
        with self.conn.cursor() as cursor:
            cursor.execute(
                "UPDATE authentik_core_user SET password = %s WHERE lower(trim(email)) = lower(%s)",
                (password_hash, email.strip()))
            rowcount = cursor.rowcount
        self.conn.commit()
        return rowcount
