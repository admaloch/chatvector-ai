"""ChatVector management CLI.

Usage:
    python -m backend.cli create-tenant-key --tenant <name> [--tenant-id <id>]

Commands
--------
create-tenant-key
    Create a new tenant and generate an API key for it.
    The raw API key is printed once and never stored — copy it immediately.

list-tenant-keys
    List API keys for a tenant (id, prefix, status, created_at). Never
    returns the raw secret since it isn't stored.

revoke-tenant-key
    Revoke a key by id or prefix. Safe to run twice — revoking an
    already-revoked key is a no-op.

rotate-tenant-key
    Revoke an existing key and issue a new one for the same tenant.
    Prints the new raw key once.

set-tenant-key-expiry
    Set or clear expires_at on a key (ISO-8601 datetime, or "clear").

set-tenant-key-external-user-id
    Assign or clear external_user_id on a key.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime


def _parse_expires_at(value: str) -> datetime | None:
    if value.lower() == "clear":
        return None
    return datetime.fromisoformat(value)


def _print_raw_key_block(raw_key: str, api_key, *, action: str) -> None:
    print()
    print("=" * 60)
    print(action)
    print(f"  Key ID : {api_key.id}")
    print(f"  Prefix : {api_key.prefix}")
    if api_key.external_user_id:
        print(f"  External user ID : {api_key.external_user_id}")
    if api_key.expires_at:
        print(f"  Expires at : {api_key.expires_at}")
    print()
    print("Raw API key (shown once — copy it now):")
    print()
    print(f"  {raw_key}")
    print()
    print("=" * 60)
    print()
    print("Add to your client's Authorization header:")
    print(f"  Authorization: Bearer {raw_key}")
    print()


async def cmd_create_tenant_key(
    tenant_name: str,
    tenant_id: str | None,
    external_user_id: str | None,
    expires_at: datetime | None,
) -> None:
    from services.api_key_service import create_api_key, create_tenant

    tenant = await create_tenant(name=tenant_name, tenant_id=tenant_id)
    raw_key, api_key = await create_api_key(
        tenant_id=tenant.id,
        external_user_id=external_user_id,
        expires_at=expires_at,
    )

    print()
    print("=" * 60)
    print("Tenant created")
    print(f"  ID   : {tenant.id}")
    print(f"  Name : {tenant.name}")
    print()
    _print_raw_key_block(raw_key, api_key, action="API key created")


async def cmd_list_tenant_keys(tenant_id: str) -> None:
    from services.api_key_service import list_tenant_keys

    keys = await list_tenant_keys(tenant_id=tenant_id)

    if not keys:
        print(f"No API keys found for tenant '{tenant_id}'.")
        return

    print()
    print(f"API keys for tenant '{tenant_id}':")
    print("-" * 90)
    print(
        f"{'ID':<38} {'Prefix':<10} {'Status':<10} "
        f"{'External user':<16} {'Expires':<20} Created"
    )
    print("-" * 90)
    for key in keys:
        external_user = key.external_user_id or "-"
        expires = key.expires_at.isoformat() if key.expires_at else "-"
        print(
            f"{str(key.id):<38} {key.prefix:<10} {key.status:<10} "
            f"{external_user:<16} {expires:<20} {key.created_at}"
        )
    print()


async def cmd_revoke_tenant_key(
    tenant_id: str,
    key_id: str | None,
    prefix: str | None,
) -> None:
    from services.api_key_service import revoke_api_key

    if not key_id and not prefix:
        print("Error: must provide --key-id or --prefix")
        return

    success = await revoke_api_key(tenant_id=tenant_id, key_id=key_id, prefix=prefix)

    if success:
        print(f"Key revoked for tenant '{tenant_id}'.")
    else:
        print(f"No matching key found for tenant '{tenant_id}'.")


async def cmd_rotate_tenant_key(tenant_id: str, key_id: str) -> None:
    from services.api_key_service import rotate_api_key

    result = await rotate_api_key(tenant_id=tenant_id, key_id=key_id)
    if result is None:
        print(f"No matching key found for tenant '{tenant_id}'.")
        return

    raw_key, api_key = result
    _print_raw_key_block(raw_key, api_key, action="API key rotated")


async def cmd_set_tenant_key_expiry(
    tenant_id: str,
    key_id: str,
    expires_at: datetime | None,
) -> None:
    from services.api_key_service import set_api_key_expiry

    success = await set_api_key_expiry(
        tenant_id=tenant_id,
        key_id=key_id,
        expires_at=expires_at,
    )
    if success:
        if expires_at is None:
            print(f"Cleared expiry for key '{key_id}' on tenant '{tenant_id}'.")
        else:
            print(
                f"Set expiry for key '{key_id}' on tenant '{tenant_id}' "
                f"to {expires_at.isoformat()}."
            )
    else:
        print(f"No matching key found for tenant '{tenant_id}'.")


async def cmd_set_tenant_key_external_user_id(
    tenant_id: str,
    key_id: str,
    external_user_id: str | None,
) -> None:
    from services.api_key_service import set_api_key_external_user_id

    success = await set_api_key_external_user_id(
        tenant_id=tenant_id,
        key_id=key_id,
        external_user_id=external_user_id,
    )
    if success:
        if external_user_id is None:
            print(f"Cleared external_user_id for key '{key_id}' on tenant '{tenant_id}'.")
        else:
            print(
                f"Set external_user_id for key '{key_id}' on tenant '{tenant_id}' "
                f"to {external_user_id!r}."
            )
    else:
        print(f"No matching key found for tenant '{tenant_id}'.")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="backend.cli",
        description="ChatVector management commands",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser(
        "create-tenant-key",
        help="Create a tenant and generate an API key",
    )
    create_parser.add_argument(
        "--tenant",
        required=True,
        metavar="NAME",
        help="Human-readable tenant name (e.g. 'demo' or 'Acme Corp')",
    )
    create_parser.add_argument(
        "--tenant-id",
        metavar="ID",
        default=None,
        help="Optional stable tenant identifier (defaults to slugified name)",
    )
    create_parser.add_argument(
        "--external-user-id",
        metavar="ID",
        default=None,
        help="Optional developer-side user identifier to store with the key",
    )
    create_parser.add_argument(
        "--expires-at",
        metavar="ISO",
        default=None,
        help="Optional ISO-8601 expiration datetime for the key",
    )

    list_parser = subparsers.add_parser(
        "list-tenant-keys",
        help="List API keys for a tenant",
    )
    list_parser.add_argument("--tenant-id", required=True, metavar="ID")

    revoke_parser = subparsers.add_parser(
        "revoke-tenant-key",
        help="Revoke an API key by id or prefix",
    )
    revoke_parser.add_argument("--tenant-id", required=True, metavar="ID")
    revoke_parser.add_argument("--key-id", metavar="ID", default=None)
    revoke_parser.add_argument("--prefix", metavar="PREFIX", default=None)

    rotate_parser = subparsers.add_parser(
        "rotate-tenant-key",
        help="Rotate an API key (revoke old, issue new)",
    )
    rotate_parser.add_argument("--tenant-id", required=True, metavar="ID")
    rotate_parser.add_argument("--key-id", required=True, metavar="ID")

    expiry_parser = subparsers.add_parser(
        "set-tenant-key-expiry",
        help="Set or clear API key expiration",
    )
    expiry_parser.add_argument("--tenant-id", required=True, metavar="ID")
    expiry_parser.add_argument("--key-id", required=True, metavar="ID")
    expiry_parser.add_argument(
        "--expires-at",
        required=True,
        metavar="ISO",
        help="ISO-8601 datetime, or 'clear' to remove expiration",
    )

    external_user_parser = subparsers.add_parser(
        "set-tenant-key-external-user-id",
        help="Set or clear external_user_id on an API key",
    )
    external_user_parser.add_argument("--tenant-id", required=True, metavar="ID")
    external_user_parser.add_argument("--key-id", required=True, metavar="ID")
    external_user_parser.add_argument(
        "--external-user-id",
        required=True,
        metavar="ID",
        help="Developer-side user identifier, or 'clear' to remove",
    )

    args = parser.parse_args()

    if args.command == "create-tenant-key":
        expires_at = (
            _parse_expires_at(args.expires_at) if args.expires_at else None
        )
        asyncio.run(
            cmd_create_tenant_key(
                args.tenant,
                args.tenant_id,
                args.external_user_id,
                expires_at,
            )
        )
    elif args.command == "list-tenant-keys":
        asyncio.run(cmd_list_tenant_keys(args.tenant_id))
    elif args.command == "revoke-tenant-key":
        asyncio.run(
            cmd_revoke_tenant_key(args.tenant_id, args.key_id, args.prefix)
        )
    elif args.command == "rotate-tenant-key":
        asyncio.run(cmd_rotate_tenant_key(args.tenant_id, args.key_id))
    elif args.command == "set-tenant-key-expiry":
        asyncio.run(
            cmd_set_tenant_key_expiry(
                args.tenant_id,
                args.key_id,
                _parse_expires_at(args.expires_at),
            )
        )
    elif args.command == "set-tenant-key-external-user-id":
        external_user_id = args.external_user_id
        if external_user_id.lower() == "clear":
            external_user_id = None
        asyncio.run(
            cmd_set_tenant_key_external_user_id(
                args.tenant_id,
                args.key_id,
                external_user_id,
            )
        )
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
