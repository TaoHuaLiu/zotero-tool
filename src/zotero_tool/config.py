"""Settings from the environment (and a `.env` file found from the working directory upward)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import find_dotenv, load_dotenv


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    api_key: str
    library_type: str  # "group" or "user"
    library_id: str


def load_api_key() -> str:
    load_dotenv(find_dotenv(usecwd=True))
    api_key = os.environ.get("ZOTERO_API_KEY", "").strip()
    if not api_key:
        raise ConfigError(
            "ZOTERO_API_KEY is not set. Create one at https://www.zotero.org/settings/keys "
            "and put it in .env (see .env.example)."
        )
    return api_key


def load_settings(group: str | None = None, user: bool = False) -> Settings:
    """Resolve the API key and target library.

    Priority for the library: --user flag, --group option, ZOTERO_GROUP_ID,
    then ZOTERO_USER_ID (personal library). The user ID can be looked up from
    the key, so only the key is strictly required for `groups`.
    """
    api_key = load_api_key()
    if user:
        uid = os.environ.get("ZOTERO_USER_ID", "").strip()
        if not uid:
            uid = str(lookup_user_id(api_key))
        return Settings(api_key, "user", uid)
    gid = (group or os.environ.get("ZOTERO_GROUP_ID", "")).strip()
    if gid:
        return Settings(api_key, "group", gid)
    raise ConfigError(
        "No library selected: pass --group ID (see `zotero-tool groups`) "
        "or set ZOTERO_GROUP_ID in .env."
    )


def lookup_user_id(api_key: str) -> int:
    from pyzotero import zotero

    # key_info() hits /keys/<key>; pyzotero still wants some library ID to build the client
    info = zotero.Zotero("0", "user", api_key).key_info()
    return int(info["userID"])
