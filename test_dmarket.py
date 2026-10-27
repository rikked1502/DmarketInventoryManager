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

    def test_history_net_after_fee(self):
        sent, urlopen = capture({"Trades": [{
            "OfferID": "o1", "Title": "AWP | Asiimov (Field-Tested)", "Price": {"Amount": 50.0},
            "Fee": {"Amount": {"Amount": 2.5}}, "OfferClosedAt": "1700000000", "Status": "successful",
        }], "Cursor": ""})
        with mock.patch("urllib.request.urlopen", urlopen):
            row = dmarket.Client(PUBLIC, SECRET).history("sold")["rows"][0]
        self.assertEqual((row["priceCents"], row["feeCents"], row["netCents"]), (5000, 250, 4750))


if __name__ == "__main__":
    unittest.main()
