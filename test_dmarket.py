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

    def test_create_target_body_and_failure(self):
        sent, urlopen = capture({"Result": [{"Successful": False, "Error": {"Code": "NotEnoughBalance", "Message": "Not enough balance"}}]})
        with mock.patch("urllib.request.urlopen", urlopen):
            res = dmarket.Client(PUBLIC, SECRET).create_target("a8db", "Clutch Case", 5, 38)
        self.assertEqual(json.loads(sent["req"].data), {"GameID": "a8db", "Targets": [
            {"Title": "Clutch Case", "Amount": "5", "Price": {"Currency": "USD", "Amount": 0.38}}]})
        self.assertEqual(res, {"ok": 0, "failed": [{"title": "Clutch Case", "code": "NotEnoughBalance", "message": "Not enough balance"}]})
        for bad in (("", 1, 38), ("Clutch Case", 0, 38), ("Clutch Case", 101, 38), ("Clutch Case", 1, 0)):
            with self.assertRaises(dmarket.DMarketError):
                dmarket.Client(PUBLIC, SECRET).create_target("a8db", *bad)

    def test_delete_targets_counts_results(self):
        sent, urlopen = capture({"Result": [
            {"DeleteTarget": {"TargetID": "t1"}, "Successful": True},
            {"DeleteTarget": {"TargetID": "t2"}, "Successful": False, "Error": {"Code": "BadRequest", "Message": "gone"}}]})
        with mock.patch("urllib.request.urlopen", urlopen):
            res = dmarket.Client(PUBLIC, SECRET).delete_targets(["t1", "t2"])
        self.assertEqual(json.loads(sent["req"].data), {"Targets": [{"TargetID": "t1"}, {"TargetID": "t2"}]})
        self.assertEqual(res, {"ok": 1, "failed": [{"targetId": "t2", "code": "BadRequest", "message": "gone"}]})

    def test_path_params_signed_decoded(self):
        sent = []

        def urlopen(req, timeout):
            sent.append(req)
            return FakeResponse({"aggregatedPrices": [], "orders": [
                {"title": "AK-47 | Redline (Field-Tested)", "price": "9999", "amount": "9", "attributes": {"floatPartValue": "FT-0", "phase": "any"}},
                {"title": "AK-47 | Redline (Field-Tested)", "price": "4993", "amount": "4", "attributes": {"floatPartValue": "any", "phase": "any"}},
                {"title": "AK-47 | Redline (Field-Tested)", "price": "2", "amount": "1"}]})
        with mock.patch("urllib.request.urlopen", urlopen), mock.patch("time.time", return_value=1700000000):
            m = dmarket.Client(PUBLIC, SECRET).market("a8db", "AK-47 | Redline (Field-Tested)")
        req = sent[1]
        self.assertEqual(req.full_url, dmarket.API + "/marketplace-api/v1/targets-by-title/a8db/AK-47%20%7C%20Redline%20%28Field-Tested%29")
        self.verify(req, "GET/marketplace-api/v1/targets-by-title/a8db/AK-47 | Redline (Field-Tested)1700000000")
        self.assertEqual((m["bid"], m["bidLow"], m["orders"]), (4993, 2, 5))

    def test_target_attrs_never_widen(self):
        self.assertEqual(dmarket.target_attrs({"floatPart": "FLOAT_PART_FN_0", "phase": "PHASE_TITLE_UNSPECIFIED", "paintSeed": 0}),
                         {"floatPartValue": "FN-0"})
        self.assertEqual(dmarket.target_attrs({"phase": "PHASE_TITLE_BLACK_PEARL", "paintSeed": 661}),
                         {"phase": "black-pearl", "paintSeed": 661})
        self.assertEqual(dmarket.target_attrs({}), {})
        self.assertIsNone(dmarket.target_attrs({"floatPart": "FLOAT_PART_SOMETHING_NEW"}))
        self.assertIsNone(dmarket.target_attrs({"phase": "PHASE_TITLE_GAMMA_1"}))

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


class PauseTest(unittest.TestCase):
    """Pause/activate must never lose a buy order: local copy first, DMarket second."""

    def setUp(self):
        import tempfile
        from pathlib import Path
        import app
        self.app = app
        self.tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(app, "SETTINGS_FILE", Path(self.tmp.name) / "settings.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        self.client = mock.Mock(public_key="pk")
        self.api = app.Api()
        self.api._client = self.client

    def row(self, id, status="active", attrs=None):
        return {"id": id, "title": "Clutch Case", "amount": 5, "priceCents": 38, "status": status, "attrs": attrs if attrs is not None else {}}

    def test_pause_keeps_copy_only_for_removed_orders(self):
        self.client.delete_targets.return_value = {"ok": 1, "failed": [{"targetId": "t2", "code": "", "message": "nope"}]}
        res = self.api.pause_targets("a8db", [self.row("t1"), self.row("t2")])
        self.client.delete_targets.assert_called_once_with(["t1", "t2"])
        store = self.app.paused(self.client)
        self.assertEqual(len(store), 1)
        self.assertEqual(res["paused"], [store[0]["id"]])
        self.assertEqual((store[0]["title"], store[0]["amount"], store[0]["priceCents"]), ("Clutch Case", 5, 38))

    def test_pause_network_error_drops_copies(self):
        self.client.delete_targets.side_effect = dmarket.DMarketError("down")
        with self.assertRaises(dmarket.DMarketError):
            self.api.pause_targets("a8db", [self.row("t1")])
        self.assertEqual(self.app.paused(self.client), [])

    def test_pause_refuses_unrecreatable_restrictions(self):
        bad = self.row("t1")
        bad["attrs"] = None
        with self.assertRaises(dmarket.DMarketError):
            self.api.pause_targets("a8db", [bad])
        self.client.delete_targets.assert_not_called()

    def test_activate_inactive_recreates_and_keeps_copy_on_failure(self):
        self.client.delete_targets.return_value = {"ok": 2, "failed": []}
        self.client.create_target.side_effect = [{"ok": 1, "failed": []},
                                                 {"ok": 0, "failed": [{"title": "Clutch Case", "code": "NotEnoughBalance", "message": ""}]}]
        res = self.api.activate_targets("a8db", [self.row("t1", "inactive", {"floatPartValue": "FN-0"}), self.row("t2", "inactive")])
        self.assertEqual(self.client.create_target.call_args_list[0].args, ("a8db", "Clutch Case", 5, 38, {"floatPartValue": "FN-0"}))
        self.assertEqual(res["ok"], 1)
        self.assertEqual(len(self.app.paused(self.client)), 1)  # the failed one stays paused, not lost

    def test_remove_paused_is_local(self):
        self.client.delete_targets.return_value = {"ok": 1, "failed": []}
        pid = self.api.pause_targets("a8db", [self.row("t1")])["paused"][0]
        self.client.delete_targets.reset_mock()
        self.assertEqual(self.api.delete_targets([pid])["ok"], 1)
        self.client.delete_targets.assert_not_called()
        self.assertEqual(self.app.paused(self.client), [])


if __name__ == "__main__":
    unittest.main()
