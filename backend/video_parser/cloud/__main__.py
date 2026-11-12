"""Explicit server entry point and interactive account bootstrap."""
from __future__ import annotations

import argparse
from getpass import getpass
import os
from pathlib import Path

from fastapi import HTTPException

from .app import create_app
from .store import Store


def main():
    parser = argparse.ArgumentParser(description="Private Video Parser material storage")
    parser.add_argument("--data-dir", required=True, type=Path, help="Private server data directory, separate from local workspace")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8090)
    create = commands.add_parser("create-user", help="Create an account; password is prompted, never a command-line argument")
    create.add_argument("login", help="Login name, e.g. Celyn, or an e-mail address")
    rename = commands.add_parser("rename-user", help="Change an existing account's login name, keeping its projects")
    rename.add_argument("login")
    rename.add_argument("new_login")
    commands.add_parser("gc", help="Purge expired recycle-bin files; keep permanent deletion tombstones")
    args = parser.parse_args()
    if os.name == "posix":
        os.umask(0o077)
    try:
        if args.command == "serve":
            import uvicorn
            uvicorn.run(create_app(args.data_dir), host=args.host, port=args.port, proxy_headers=False, access_log=False)
        elif args.command == "create-user":
            password = getpass("Password (at least 12 characters): ")
            confirmation = getpass("Confirm password: ")
            if password != confirmation:
                parser.error("Passwords do not match")
            user = Store(args.data_dir).create_user(args.login, password)
            print("Created account:", user["login"])
        elif args.command == "rename-user":
            user = Store(args.data_dir).rename_user(args.login, args.new_login)
            print("Renamed account:", user["login"])
        else:
            print("Removed expired blobs:", Store(args.data_dir).collect())
    except HTTPException as error:
        parser.exit(1, f"Error: {error.detail['code']}\n")


if __name__ == "__main__":
    main()
