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
    def update_password(self, email, password_hash):
        with self.conn.cursor() as cursor:
            cursor.execute(
                "UPDATE authentik_core_user SET password = %s WHERE lower(trim(email)) = lower(%s)",
                (password_hash, email.strip()))
            rowcount = cursor.rowcount
        self.conn.commit()
        return rowcount
