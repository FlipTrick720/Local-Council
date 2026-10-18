"""Start the server: python -m app [--host HOST] [--port PORT]

Password: python -m app --set-password (asks for it) or --remove-password.
"""
import argparse
import getpass

import uvicorn

from app import auth, config


def main():
    server = config.load()["server"]
    parser = argparse.ArgumentParser(description="Local Council server")
    parser.add_argument("--host", default=server["host"])
    parser.add_argument("--port", type=int, default=server["port"])
    parser.add_argument("--set-password", action="store_true", help="Passwort für die Web-Oberfläche setzen")
    parser.add_argument("--remove-password", action="store_true", help="Passwortschutz abschalten")
    args = parser.parse_args()
    if args.remove_password:
        auth.set_password("")
        print("Passwortschutz ist aus.")
        return
    if args.set_password:
        password = getpass.getpass("Neues Passwort: ")
        if not password or password != getpass.getpass("Wiederholen: "):
            raise SystemExit("Abgebrochen: leer oder nicht gleich.")
        auth.set_password(password)
        print("Passwort gesetzt. Alle Geräte müssen sich neu anmelden.")
        return
    uvicorn.run("app.main:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
