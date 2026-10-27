"""DMarket Inventory Manager: desktop window (pywebview) around dmarket.Client."""
import json
from pathlib import Path

import webview

from dmarket import Client, DMarketError

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
SETTINGS_FILE = ROOT / "settings.json"


def load_env(path):
    env = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                env[key.strip()] = value.strip().strip("'\"")
    return env


def load_accounts(env):
    """DMARKET_PUBLIC_KEY/DMARKET_SECRET_KEY -> 'Main'; DMARKET_PUBLIC_KEY_<NAME>/DMARKET_SECRET_KEY_<NAME> -> '<Name>'."""
    accounts = {}
    for key, public in env.items():
        if not key.startswith("DMARKET_PUBLIC_KEY"):
            continue
        suffix = key[len("DMARKET_PUBLIC_KEY"):]
        secret = env.get("DMARKET_SECRET_KEY" + suffix, "")
        if (suffix == "" or suffix.startswith("_")) and public and secret:
            accounts[suffix[1:].replace("_", " ").title() or "Main"] = (public, secret)
    return accounts


def read_settings():
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(**changes):
    SETTINGS_FILE.write_text(json.dumps({**read_settings(), **changes}), encoding="utf-8")


class Api:
    """Methods exposed to the page as window.pywebview.api.*. Keys never leave Python."""

    def __init__(self):
        self._client = None

    def connect(self, account=None):
        """(Re)read .env, pick an account (given > last used > first) and verify it by fetching the balance."""
        self._client = None
        accounts = load_accounts(load_env(ENV_FILE))
        if not accounts:
            return {"state": "missing", "accounts": []}
        if account not in accounts:
            account = read_settings().get("account")
        if account not in accounts:
            account = next(iter(accounts))
        result = {"accounts": list(accounts), "account": account}
        try:
            client = Client(*accounts[account])
            balance = client.balance()
        except (ValueError, DMarketError) as e:
            return {**result, "state": "error", "message": str(e)}
        self._client = client
        save_settings(account=account)
        return {**result, "state": "ok", "balanceCents": balance}

    def _c(self):
        if not self._client:
            raise DMarketError("Not connected. Check your .env keys.")
        return self._client

    def balance(self):
        return self._c().balance()

    def inventory(self, game, title, dmarket_only, cursor):
        return self._c().inventory(game, title, dmarket_only, cursor)

    def offers(self, game, title, cursor):
        return self._c().offers(game, title, cursor)

    def best_prices(self, game, titles):
        return self._c().best_prices(game, titles)

    def create_offers(self, items):
        return self._c().create_offers(items)

    def update_offers(self, items):
        return self._c().update_offers(items)

    def delete_offers(self, offer_ids):
        return self._c().delete_offers(offer_ids)

    def history(self, kind, cursor):
        return self._c().history(kind, cursor)

    # The built-in server picks a random port, so localStorage would not survive restarts.
    def get_theme(self):
        return read_settings().get("theme")

    def set_theme(self, theme):
        if theme in ("light", "dark"):
            save_settings(theme=theme)


if __name__ == "__main__":
    webview.create_window("DMarket Inventory Manager", str(ROOT / "ui" / "index.html"), js_api=Api(),
                          width=1280, height=820, min_size=(900, 600))
    webview.start()
