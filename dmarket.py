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
BATCH_SIZE, CHUNK_PAUSE, RETRY_PAUSE = 20, 1.0, 3.0  # seconds


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


def details(a):
    """Short per-item facts that tell copies with the same title apart (float, stickers, unusual effect…)."""
    cs2, tf2, out = a.get("cs2") or {}, a.get("tf2") or {}, []
    try:
        wear = float(cs2.get("float") or 0)
    except ValueError:
        wear = 0
    if wear > 0:
        out.append(f"Float {wear:.6f}")
        if cs2.get("paintSeed"):
            out.append(f"Pattern {cs2['paintSeed']}")
    if cs2.get("phase"):
        out.append(cs2["phase"].replace("-", " ").title())
    stickers = [s.get("name", "").removeprefix("Sticker | ") for s in cs2.get("stickers") or []]
    if stickers:
        out.append("Stickers: " + ", ".join(stickers))
    charms = [c.get("name", "") for c in cs2.get("charms") or []]
    if charms:
        out.append("Charm: " + ", ".join(charms))
    if cs2.get("customName"):
        out.append(f"Name tag: {cs2['customName']}")
    if tf2.get("effect"):
        out.append("Effect: " + tf2["effect"])
    streak = tf2.get("killstreak") or ""
    if streak not in ("", "KILLSTREAK_UNSPECIFIED", "KILLSTREAK_NONE"):
        out.append(streak.removeprefix("KILLSTREAK_").title() + " killstreak")
    if tf2.get("spells"):
        out.append("Spells: " + ", ".join(tf2["spells"]))
    if tf2.get("paintColor"):
        out.append("Paint: " + tf2["paintColor"])
    unlock = (a.get("unlockDate") or "")[:19]  # API sends nanoseconds; seconds are enough
    if unlock and unlock > time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()):
        out.append(f"Trade locked until {unlock[:10]}")
    return out


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
                "details": details(a),
                "inMarket": in_market,
                "sellable": in_market and not a.get("saleRestricted") and not a.get("sellUnavailable"),
                "recommendedCents": to_cents(_amount(it.get("offerRecommendedPrice"))) or None,  # 0 = no price known
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
                "details": details(a),
                "priceCents": int(it.get("priceCents") or 0),
                "createdAt": it.get("createdAt", ""),
            })
        return {"items": items, "cursor": r.get("cursor", ""), "total": int(r.get("total") or 0)}

    def best_prices(self, game, titles):
        """{title: {low, high, bid}} in cents: cheapest and priciest DMarket listing, best buy order."""
        _check_game(game)
        titles = list(dict.fromkeys(titles))
        out = {t: {"low": None, "high": None, "bid": None} for t in titles}
        for i in range(0, len(titles), 100):
            chunk = titles[i:i + 100]
            r = self.call("POST", "/marketplace-api/v1/aggregated-prices", body={
                "filter": {"game": game, "titles": chunk, "includeP2p": False}, "limit": str(len(chunk))})
            for p in r.get("aggregatedPrices") or []:
                amount = _amount(p.get("offerBestPrice"))
                if p.get("title") in out and amount not in (None, ""):
                    out[p["title"]]["low"] = int(amount) or None
                bid = _amount(p.get("orderBestPrice"))
                if p.get("title") in out and bid not in (None, ""):
                    out[p["title"]]["bid"] = int(bid) or None
        for t in titles:
            # title filter is a prefix match, so keep only exact titles.
            # shortcut: looks at the 100 priciest prefix matches, enough unless a title prefixes many pricier ones.
            r = self.call("GET", "/marketplace-api/v2/offers", {
                "gameId": game, "title": t, "orderBy": "price", "orderDir": "desc", "limit": 100})
            out[t]["high"] = next((int(o.get("priceCents") or 0) or None for o in r.get("items") or []
                                   if (o.get("attributes") or {}).get("title") == t), None)
        return out

    def _batch(self, path, requests, id_key):
        """Send in small chunks with a pause, then retry failures once: big batches get refused by DMarket."""
        ok, failed, pending = 0, {}, requests
        for attempt in range(2):
            failed = {}
            for i in range(0, len(pending), BATCH_SIZE):
                if i or attempt:
                    time.sleep(RETRY_PAUSE if attempt and not i else CHUNK_PAUSE)
                chunk = pending[i:i + BATCH_SIZE]
                try:
                    r = self.call("POST", path, body={"requests": chunk})
                except DMarketError as e:  # keep earlier chunks' results; report this chunk as failed
                    failed.update({req[id_key]: {id_key: req[id_key], "code": "", "message": str(e)} for req in chunk})
                    continue
                ok += len(r.get("offers") or [])
                failed.update({f.get(id_key): f for f in r.get("failed") or []})
            if not failed:
                break
            pending = [req for req in requests if req[id_key] in failed]
        return {"ok": ok, "failed": list(failed.values())}

    def create_offers(self, items):
        """items: [{assetId, priceCents}]"""
        return self._batch("/marketplace-api/v2/offers:batchCreate", _check_prices(items, "assetId"), "assetId")

    def update_offers(self, items):
        """items: [{offerId, priceCents}]"""
        return self._batch("/marketplace-api/v2/offers:batchUpdate", _check_prices(items, "offerId"), "offerId")

    def delete_offers(self, offer_ids):
        return self._batch("/marketplace-api/v2/offers:batchDelete", [{"offerId": str(i)} for i in offer_ids], "offerId")

    # --- buy orders (targets) -------------------------------------------------

    def targets(self, game, title="", cursor=""):
        _check_game(game)
        r = self.call("GET", "/marketplace-api/v2/user/targets", {
            "gameId": game, "limit": 100, "title": title, "cursor": cursor,
            "orderBy": "createdAt", "orderDir": "desc",
        })
        items = []
        for it in r.get("items") or []:
            a = it.get("attributes") or {}
            items.append({
                "id": it.get("targetId"),
                "title": it.get("title") or a.get("title", ""),
                "image": a.get("image", ""),
                "details": details(a),
                "amount": int(it.get("amount") or 0),
                "priceCents": int(it.get("priceCents") or 0),
                "active": it.get("status") == "TARGET_STATUS_ACTIVE",
                "createdAt": it.get("createdAt", ""),
            })
        return {"items": items, "cursor": r.get("cursor", ""), "total": int(r.get("total") or 0)}

    def create_target(self, game, title, amount, price_cents):
        """One buy order for `amount` copies of an exact title. Returns {ok, failed} like the offer batches."""
        _check_game(game)
        amount, price_cents = int(amount), int(price_cents)
        if not title or not 1 <= amount <= 100 or price_cents <= 0:
            raise DMarketError("A buy order needs an exact item name, a quantity of 1-100 and a price above $0.00")
        r = self.call("POST", "/marketplace-api/v1/user-targets/create", body={"GameID": game, "Targets": [
            {"Title": title, "Amount": str(amount), "Price": {"Currency": "USD", "Amount": price_cents / 100}}]})
        res = (r.get("Result") or [{}])[0]
        if res.get("Successful"):
            return {"ok": 1, "failed": []}
        err = res.get("Error") or {}
        return {"ok": 0, "failed": [{"title": title, "code": err.get("Code", ""), "message": err.get("Message", "")}]}

    def delete_targets(self, target_ids):
        ok, failed = 0, []
        for i in range(0, len(target_ids), 100):
            chunk = [str(t) for t in target_ids[i:i + 100]]
            try:
                r = self.call("POST", "/marketplace-api/v1/user-targets/delete",
                              body={"Targets": [{"TargetID": t} for t in chunk]})
            except DMarketError as e:  # keep earlier chunks' results; report this chunk as failed
                failed += [{"targetId": t, "code": "", "message": str(e)} for t in chunk]
                continue
            for res in r.get("Result") or []:
                if res.get("Successful"):
                    ok += 1
                else:
                    err = res.get("Error") or {}
                    failed.append({"targetId": (res.get("DeleteTarget") or {}).get("TargetID", ""),
                                   "code": err.get("Code", ""), "message": err.get("Message", "")})
        return {"ok": ok, "failed": failed}

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
