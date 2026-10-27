"""DMarket Inventory Manager: desktop window (pywebview) around dmarket.Client."""
import json
import threading
import time
import uuid
from pathlib import Path

import webview

from dmarket import Client, DMarketError, attrs_details

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


_settings_lock = threading.Lock()  # pywebview runs each API call on its own thread


def edit_settings(fn):
    with _settings_lock:
        settings = read_settings()
        fn(settings)
        SETTINGS_FILE.write_text(json.dumps(settings), encoding="utf-8")


def save_settings(**changes):
    edit_settings(lambda s: s.update(changes))


def paused(client):
    """Buy orders paused in this app, per account. DMarket's API has no pause, so these exist only here."""
    return read_settings().get("paused", {}).get(client.public_key, [])


def edit_paused(client, fn):
    def go(settings):
        store = settings.setdefault("paused", {})
        store[client.public_key] = fn(store.get(client.public_key, []))
    edit_settings(go)


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

    def targets(self, game, title, cursor):
        c = self._c()
        page = c.targets(game, title, cursor)
        if not cursor:  # paused copies only live here; show them with the first page
            page["items"] = [{**p, "status": "paused", "details": attrs_details(p["attrs"]), "createdAt": p["pausedAt"]}
                             for p in paused(c) if p["game"] == game and p["title"].lower().startswith(title.lower())] + page["items"]
        return page

    def market(self, game, title):
        return self._c().market(game, title)

    def search_titles(self, game, text):
        return self._c().search_titles(game, text)

    def create_target(self, game, title, amount, price_cents):
        return self._c().create_target(game, title, amount, price_cents)

    def delete_targets(self, target_ids):
        c = self._c()
        local = {i for i in target_ids if i.startswith("paused-")}
        if local:
            edit_paused(c, lambda ps: [p for p in ps if p["id"] not in local])
        remote = [i for i in target_ids if i not in local]
        res = c.delete_targets(remote) if remote else {"ok": 0, "failed": []}
        return {**res, "ok": res["ok"] + len(local)}

    def pause_targets(self, game, rows):
        return self._pause(self._c(), game, rows)

    def _pause(self, c, game, rows):
        """Save a local copy first, then remove the order on DMarket, so nothing is lost if a step fails."""
        rows = [r for r in rows if r.get("status") != "paused"]
        if any(r.get("attrs") is None for r in rows):
            raise DMarketError("Some of these buy orders have restrictions this app can't recreate. Change them on dmarket.com.")
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        copies = {r["id"]: {"id": "paused-" + uuid.uuid4().hex, "game": game, "title": r["title"], "image": r.get("image", ""),
                            "amount": int(r["amount"]), "priceCents": int(r["priceCents"]), "attrs": r["attrs"], "pausedAt": now}
                  for r in rows}
        edit_paused(c, lambda ps: ps + list(copies.values()))
        try:
            res = c.delete_targets(list(copies))
        except DMarketError:
            new = {p["id"] for p in copies.values()}
            edit_paused(c, lambda ps: [p for p in ps if p["id"] not in new])
            raise
        still_live = {copies[f["targetId"]]["id"] for f in res["failed"] if f.get("targetId") in copies}
        edit_paused(c, lambda ps: [p for p in ps if p["id"] not in still_live])
        return {**res, "paused": [p["id"] for p in copies.values() if p["id"] not in still_live]}

    def activate_targets(self, game, rows):
        """Paused copies are created again; DMarket-inactive orders are paused first, then created again."""
        c, failed = self._c(), []
        ids = {r["id"] for r in rows if r.get("status") == "paused"}
        inactive = [r for r in rows if r.get("status") == "inactive"]
        if inactive:
            res = self._pause(c, game, inactive)
            failed += res["failed"]
            ids.update(res["paused"])
        ok = 0
        for p in [p for p in paused(c) if p["id"] in ids]:
            try:
                res = c.create_target(game, p["title"], p["amount"], p["priceCents"], p["attrs"])
            except DMarketError as e:
                res = {"ok": 0, "failed": [{"title": p["title"], "code": "", "message": str(e)}]}
            if res["ok"]:  # created on DMarket: drop the local copy
                edit_paused(c, lambda ps, done=p["id"]: [q for q in ps if q["id"] != done])
            ok += res["ok"]
            failed += res["failed"]
        return {"ok": ok, "failed": failed}

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
