"""Start the server: python -m app [--host HOST] [--port PORT]"""
import argparse

import uvicorn

from app import config


def main():
    server = config.load()["server"]
    parser = argparse.ArgumentParser(description="Local Council server")
    parser.add_argument("--host", default=server["host"])
    parser.add_argument("--port", type=int, default=server["port"])
    args = parser.parse_args()
    uvicorn.run("app.main:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
