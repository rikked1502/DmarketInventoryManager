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


class Api:
    """Methods exposed to the page as window.pywebview.api.*. Keys never leave Python."""

    def __init__(self):
        self._client = None

    def connect(self):
        """(Re)read .env and verify the keys by fetching the balance."""
        self._client = None
        env = load_env(ENV_FILE)
        public, secret = env.get("DMARKET_PUBLIC_KEY", ""), env.get("DMARKET_SECRET_KEY", "")
        if not public or not secret:
            return {"state": "missing"}
        try:
            client = Client(public, secret)
            balance = client.balance()
        except (ValueError, DMarketError) as e:
            return {"state": "error", "message": str(e)}
        self._client = client
        return {"state": "ok", "balanceCents": balance}

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
        try:
            return json.loads(SETTINGS_FILE.read_text(encoding="utf-8")).get("theme")
        except (OSError, ValueError):
            return None

    def set_theme(self, theme):
        if theme in ("light", "dark"):
            SETTINGS_FILE.write_text(json.dumps({"theme": theme}), encoding="utf-8")


if __name__ == "__main__":
    webview.create_window("DMarket Inventory Manager", str(ROOT / "ui" / "index.html"), js_api=Api(),
                          width=1280, height=820, min_size=(900, 600))
    webview.start()
