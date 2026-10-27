import json
import unittest
from unittest import mock

from nacl.signing import SigningKey, VerifyKey

import dmarket

SK = SigningKey.generate()
PUBLIC = SK.verify_key.encode().hex()
SECRET = (SK.encode() + SK.verify_key.encode()).hex()  # DMarket format: seed + public key


class FakeResponse:
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def capture(body):
    sent = {}

    def urlopen(req, timeout):
        sent["req"] = req
        return FakeResponse(body)

    return sent, urlopen


class SigningTest(unittest.TestCase):
    def verify(self, req, expected_message):
        sig = req.get_header("X-request-sign")
        self.assertTrue(sig.startswith("dmar ed25519 "))
        VerifyKey(bytes.fromhex(PUBLIC)).verify(expected_message.encode(), bytes.fromhex(sig.split()[-1]))
        self.assertEqual(req.get_header("X-api-key"), PUBLIC)

    def test_get_signs_method_path_query_and_timestamp(self):
        sent, urlopen = capture({"items": [], "cursor": "", "total": "0"})
        with mock.patch("urllib.request.urlopen", urlopen), mock.patch("time.time", return_value=1700000000):
            dmarket.Client(PUBLIC, SECRET).inventory("a8db", title="AK-47 | Redline", dmarket_only=True)
        req = sent["req"]
        path = req.full_url.removeprefix(dmarket.API)
        self.assertEqual(path, "/marketplace-api/v2/user/inventory?gameId=a8db&limit=100&title=AK-47+%7C+Redline&treeFilters=inMarket%3Dtrue")
        self.verify(req, "GET" + path + "1700000000")

    def test_post_signs_body(self):
        sent, urlopen = capture({"offers": [{"id": "o1"}], "failed": []})
        with mock.patch("urllib.request.urlopen", urlopen), mock.patch("time.time", return_value=1700000000):
            res = dmarket.Client(PUBLIC, SECRET).create_offers([{"assetId": "a1", "priceCents": 1599}])
        req = sent["req"]
        body = req.data.decode()
        self.assertEqual(json.loads(body), {"requests": [{"assetId": "a1", "priceCents": "1599"}]})
        self.verify(req, "POST/marketplace-api/v2/offers:batchCreate" + body + "1700000000")
        self.assertEqual(res, {"ok": 1, "failed": []})

    def test_batch_chunks_and_retries_failures_once(self):
        calls = []

        def urlopen(req, timeout):
            ids = [r["assetId"] for r in json.loads(req.data)["requests"]]
            calls.append(ids)
            if len(calls) == 1:  # first chunk: two items refused
                return FakeResponse({"offers": [{} for _ in ids[2:]], "failed": [{"assetId": i, "code": "158"} for i in ids[:2]]})
            if len(calls) == 2:  # second chunk: whole request errors out
                raise dmarket.urllib.error.URLError("boom")
            return FakeResponse({"offers": [{} for _ in ids[1:]], "failed": [{"assetId": ids[0], "code": "158"}]})

        items = [{"assetId": f"a{i}", "priceCents": 10} for i in range(25)]
        with mock.patch("urllib.request.urlopen", urlopen), mock.patch("time.sleep") as sleep:
            res = dmarket.Client(PUBLIC, SECRET).create_offers(items)
        self.assertEqual([len(c) for c in calls], [20, 5, 7])  # 20 + 5, then retry of 2 + 5 failures
        self.assertEqual(calls[2], ["a0", "a1", "a20", "a21", "a22", "a23", "a24"])
        self.assertEqual(res["ok"], 18 + 6)
        self.assertEqual([f["assetId"] for f in res["failed"]], ["a0"])
        self.assertEqual(sleep.call_count, 2)

    def test_rejects_non_positive_price(self):
        with self.assertRaises(dmarket.DMarketError):
            dmarket.Client(PUBLIC, SECRET).create_offers([{"assetId": "a1", "priceCents": 0}])

    def test_rejects_bad_keys(self):
        with self.assertRaises(ValueError):
            dmarket.Client("nothex", SECRET)


class MoneyTest(unittest.TestCase):
    def test_to_cents(self):
        self.assertEqual(dmarket.to_cents("15.99"), 1599)
        self.assertEqual(dmarket.to_cents(15.99), 1599)
        self.assertEqual(dmarket.to_cents(0.29), 29)
        self.assertIsNone(dmarket.to_cents(None))

    def test_inventory_reads_capitalized_amount(self):
        # Shape seen from the live API (docs say lowercase "amount").
        sent, urlopen = capture({"items": [{"attributes": {"id": "a1", "title": "Gallery Case"}, "inMarket": True,
                                            "offerRecommendedPrice": {"Currency": "USD", "Amount": "0.43"}}],
                                 "cursor": "", "total": "1"})
        with mock.patch("urllib.request.urlopen", urlopen):
            item = dmarket.Client(PUBLIC, SECRET).inventory("tf2")["items"][0]
        self.assertEqual(item["recommendedCents"], 43)

    def test_details(self):
        skin = {"cs2": {"float": "0.2356003224849701", "paintSeed": 412, "phase": "phase-2",
                        "stickers": [{"name": "Sticker | Natus Vincere | Katowice 2019"}], "charms": [], "customName": ""},
                "unlockDate": "2999-01-01T00:00:00.123456789Z"}
        self.assertEqual(dmarket.details(skin), ["Float 0.235600", "Pattern 412", "Phase 2",
                                                 "Stickers: Natus Vincere | Katowice 2019", "Trade locked until 2999-01-01"])
        case = {"cs2": {"float": "0", "paintSeed": 0, "phase": "", "stickers": []}, "unlockDate": "1970-01-01T00:00:00Z"}
        self.assertEqual(dmarket.details(case), [])
        hat = {"tf2": {"effect": "Burning Flames", "killstreak": "KILLSTREAK_NONE", "spells": [], "paintColor": ""}}
        self.assertEqual(dmarket.details(hat), ["Effect: Burning Flames"])

    def test_history_net_after_fee(self):
        sent, urlopen = capture({"Trades": [{
            "OfferID": "o1", "Title": "AWP | Asiimov (Field-Tested)", "Price": {"Amount": 50.0},
            "Fee": {"Amount": {"Amount": 2.5}}, "OfferClosedAt": "1700000000", "Status": "successful",
        }], "Cursor": ""})
        with mock.patch("urllib.request.urlopen", urlopen):
            row = dmarket.Client(PUBLIC, SECRET).history("sold")["rows"][0]
        self.assertEqual((row["priceCents"], row["feeCents"], row["netCents"]), (5000, 250, 4750))


class AccountsTest(unittest.TestCase):
    def test_load_accounts(self):
        import app  # imported here so the signing tests don't need pywebview
        env = {
            "DMARKET_PUBLIC_KEY": "p0", "DMARKET_SECRET_KEY": "s0",
            "DMARKET_PUBLIC_KEY_ALT": "p1", "DMARKET_SECRET_KEY_ALT": "s1",
            "DMARKET_PUBLIC_KEY_SECOND_MAIN": "p2", "DMARKET_SECRET_KEY_SECOND_MAIN": "s2",
            "DMARKET_PUBLIC_KEY_NOSECRET": "p3",
            "DMARKET_PUBLIC_KEYX": "p4", "DMARKET_SECRET_KEYX": "s4",
        }
        self.assertEqual(app.load_accounts(env), {"Main": ("p0", "s0"), "Alt": ("p1", "s1"), "Second Main": ("p2", "s2")})


if __name__ == "__main__":
    unittest.main()
