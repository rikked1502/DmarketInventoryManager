"""Minimal signed client for the DMarket trading API (https://api.dmarket.com)."""
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal

from nacl.signing import SigningKey

API = "https://api.dmarket.com"
GAMES = {"a8db", "tf2"}  # CS2, Team Fortress 2


class DMarketError(Exception):
    pass


def sign(secret_hex, message):
    # DMarket secret keys are 64 bytes (seed + public key); the first 32 are the Ed25519 seed.
    seed = bytes.fromhex(secret_hex)[:32]
    return SigningKey(seed).sign(message.encode()).signature.hex()


def to_cents(dollars):
    """'15.99' or 15.99 -> 1599. None/'' -> None."""
    if dollars in (None, ""):
        return None
    return int((Decimal(str(dollars)) * 100).quantize(Decimal(1)))


def _amount(money):
    # The live API sends {"Currency", "Amount"} where the docs say lowercase; accept both.
    money = money or {}
    return money.get("Amount", money.get("amount"))


def _check_game(game):
    if game not in GAMES:
        raise DMarketError(f"Unsupported game: {game}")


def _check_prices(items, id_key):
    clean = []
    for it in items:
        cents = int(it["priceCents"])
        if cents <= 0:
            raise DMarketError("Prices must be greater than $0.00")
        clean.append({id_key: str(it[id_key]), "priceCents": str(cents)})
    return clean


class Client:
    def __init__(self, public_key, secret_key):
        public_key, secret_key = public_key.strip().lower(), secret_key.strip().lower()
        if len(bytes.fromhex(secret_key)) not in (32, 64) or len(bytes.fromhex(public_key)) != 32:
            raise ValueError("Keys must be hex strings (public: 64 chars, secret: 128 chars)")
        self.public_key, self.secret_key = public_key, secret_key

    def call(self, method, path, params=None, body=None):
        query = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v not in (None, "")})
        if query:
            path += "?" + query
        data = json.dumps(body, separators=(",", ":")) if body is not None else ""
        ts = str(int(time.time()))
        req = urllib.request.Request(
            API + path,
            data=data.encode() or None,
            method=method,
            headers={
                "X-Api-Key": self.public_key,
                "X-Sign-Date": ts,
                "X-Request-Sign": "dmar ed25519 " + sign(self.secret_key, method + path + data + ts),
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "DMarketInventoryManager/1.0",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raw = e.read().decode(errors="replace")
            try:
                msg = json.loads(raw).get("message") or raw
            except (ValueError, AttributeError):
                msg = raw
            raise DMarketError(f"HTTP {e.code}: {msg or e.reason}") from None
        except urllib.error.URLError as e:
            raise DMarketError(f"Network error: {e.reason}") from None

    # --- account -------------------------------------------------------------

    def balance(self):
        return int(self.call("GET", "/account/v1/balance").get("usd") or 0)

    # --- inventory & offers --------------------------------------------------

    def inventory(self, game, title="", dmarket_only=False, cursor=""):
        _check_game(game)
        r = self.call("GET", "/marketplace-api/v2/user/inventory", {
            "gameId": game, "limit": 100, "title": title, "cursor": cursor,
            "treeFilters": "inMarket=true" if dmarket_only else "",
        })
        items = []
        for it in r.get("items") or []:
            a = it.get("attributes") or {}
            in_market = bool(it.get("inMarket"))
            items.append({
                "id": a.get("id"),
                "title": a.get("title", ""),
                "image": a.get("imageUri", ""),
                "float": (a.get("cs2") or {}).get("float", ""),
                "inMarket": in_market,
                "sellable": in_market and not a.get("saleRestricted") and not a.get("sellUnavailable"),
                "recommendedCents": to_cents(_amount(it.get("offerRecommendedPrice"))),
            })
        return {"items": items, "cursor": r.get("cursor", ""), "total": int(r.get("total") or 0)}

    def offers(self, game, title="", cursor=""):
        _check_game(game)
        r = self.call("GET", "/marketplace-api/v2/user/offers", {
            "gameId": game, "limit": 100, "title": title, "cursor": cursor,
            "orderBy": "createdAt", "orderDir": "desc",
        })
        items = []
        for it in r.get("items") or []:
            a = it.get("attributes") or {}
            items.append({
                "id": it.get("offerId"),
                "title": a.get("title", ""),
                "image": a.get("imageUri", ""),
                "float": (a.get("cs2") or {}).get("float", ""),
                "priceCents": int(it.get("priceCents") or 0),
                "createdAt": it.get("createdAt", ""),
            })
        return {"items": items, "cursor": r.get("cursor", ""), "total": int(r.get("total") or 0)}

    def best_prices(self, game, titles):
        """{title: lowest offer price in cents} for the given exact titles."""
        _check_game(game)
        titles, out = list(dict.fromkeys(titles)), {}
        for i in range(0, len(titles), 100):
            chunk = titles[i:i + 100]
            r = self.call("POST", "/marketplace-api/v1/aggregated-prices",
                          body={"filter": {"game": game, "titles": chunk}, "limit": str(len(chunk))})
            for p in r.get("aggregatedPrices") or []:
                amount = _amount(p.get("offerBestPrice"))
                out[p.get("title")] = int(amount) if amount not in (None, "") else None
        return out

    def _batch(self, path, requests):
        r = self.call("POST", path, body={"requests": requests})
        return {"ok": len(r.get("offers") or []), "failed": r.get("failed") or []}

    def create_offers(self, items):
        """items: [{assetId, priceCents}]"""
        return self._batch("/marketplace-api/v2/offers:batchCreate", _check_prices(items, "assetId"))

    def update_offers(self, items):
        """items: [{offerId, priceCents}]"""
        return self._batch("/marketplace-api/v2/offers:batchUpdate", _check_prices(items, "offerId"))

    def delete_offers(self, offer_ids):
        return self._batch("/marketplace-api/v2/offers:batchDelete", [{"offerId": str(i)} for i in offer_ids])

    # --- history -------------------------------------------------------------

    def history(self, kind, cursor=""):
        """kind 'sold' (closed offers) or 'bought' (closed buy orders). Account-wide, all games."""
        if kind not in ("sold", "bought"):
            raise DMarketError(f"Unknown history kind: {kind}")
        path = "/marketplace-api/v1/user-offers/closed" if kind == "sold" else "/marketplace-api/v1/user-targets/closed"
        r = self.call("GET", path, {"Limit": 50, "OrderDir": "desc", "Cursor": cursor})
        rows = []
        for t in r.get("Trades") or []:
            price = to_cents((t.get("Price") or {}).get("Amount")) or 0
            if kind == "sold":
                fee = to_cents(((t.get("Fee") or {}).get("Amount") or {}).get("Amount")) or 0
                net, date = price - fee, t.get("OfferClosedAt")
            else:
                fee, net, date = None, -price, t.get("ClosedAt")
            rows.append({
                "kind": kind,
                "id": t.get("OfferID") if kind == "sold" else t.get("TargetID"),
                "title": t.get("Title", ""),
                "priceCents": price,
                "feeCents": fee,
                "netCents": net,
                "status": t.get("Status", ""),
                "date": int(date or 0),
            })
        return {"rows": rows, "cursor": r.get("Cursor", "")}
