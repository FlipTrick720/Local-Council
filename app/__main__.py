"""Start the server: python -m app [--host HOST] [--port PORT]

Accounts: python -m app --set-password [--user NAME] sets a password (asks for it); an unknown NAME is created as
an admin, so this also gets you back in when the admin password is lost. --list-users shows the accounts.
"""
import argparse
import getpass

import uvicorn

from app import config, users


def main():
    server = config.load()["server"]
    parser = argparse.ArgumentParser(description="Local Council server")
    parser.add_argument("--host", default=server["host"])
    parser.add_argument("--port", type=int, default=server["port"])
    parser.add_argument("--set-password", action="store_true",
                        help="Passwort für ein Konto setzen (unbekannte Namen werden als Admin angelegt)")
    parser.add_argument("--user", default=users.FIRST_ADMIN, help="Konto für --set-password (Standard: admin)")
    parser.add_argument("--list-users", action="store_true", help="Konten anzeigen")
    args = parser.parse_args()
    if args.list_users:
        for u in users.list_all():
            print(f"{u['name']:<32} {'Admin' if u['admin'] else 'Benutzer':<9} {'' if u['has_password'] else '(ohne Passwort)'}")
        return
    if args.set_password:
        name = args.user.strip().lower()
        password = getpass.getpass(f"Neues Passwort für {name}: ")
        if not password or password != getpass.getpass("Wiederholen: "):
            raise SystemExit("Abgebrochen: leer oder nicht gleich.")
        try:
            if users.get(name):
                users.update(name, password=password)
                print(f"Passwort für {name} gesetzt. {name} muss sich auf allen Geräten neu anmelden.")
            else:
                users.create(name, password, admin=True)
                print(f"Admin-Konto {name} angelegt.")
        except users.UserError as e:
            raise SystemExit(str(e))
        return
    users.list_all()  # turns an old single-password setup into accounts before the first request
    uvicorn.run("app.main:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
