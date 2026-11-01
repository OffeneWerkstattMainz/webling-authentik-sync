import psycopg2


class AuthentikDb:

    def __init__(self, *, dbname, user, password, host, port):
        self.db_params = dict(dbname=dbname, user=user, password=password, host=host, port=port)
        self.conn = None

    def connect(self):
        self.conn = psycopg2.connect(**self.db_params)

    def disconnect(self):
        if self.conn:
            self.conn.close()

    # Die API nimmt keine Passwort-Hashes an, daher direkt in die DB schreiben.
    # Ein in Authentik gesetztes Passwort bleibt: überschrieben wird nur ein
    # unbrauchbares oder eines, das seit dem Anlegen nie geändert wurde.
    # Authentiks set_password setzt password_change_date, dieser direkte
    # Schreibzugriff nicht, daher gilt ein migrierter Hash als nie geändert.
    # Liefert 'updated', 'kept' (in Authentik gesetzt) oder 'missing'.
    def update_password(self, email, password_hash):
        with self.conn.cursor() as cursor:
            cursor.execute(
                "UPDATE authentik_core_user SET password = %s"
                " WHERE lower(trim(email)) = lower(%s)"
                " AND (password LIKE '!%%' OR coalesce(password, '') = ''"
                "      OR password_change_date <= date_joined + interval '1 minute')",
                (password_hash, email.strip()))
            updated = cursor.rowcount
            if not updated:
                cursor.execute("SELECT 1 FROM authentik_core_user WHERE lower(trim(email)) = lower(%s)",
                               (email.strip(),))
                exists = cursor.fetchone() is not None
        self.conn.commit()
        return 'updated' if updated else ('kept' if exists else 'missing')
