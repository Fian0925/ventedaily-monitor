import hashlib
import hmac
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import urlencode

from flask import Flask

import config
import database
import mini_app


def init_data(user_id=42, auth_date=None):
    fields = {
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "user": json.dumps({"id": user_id, "first_name": "Ayu"}),
    }
    message = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    signing_key = hmac.digest(b"WebAppData", config.TELEGRAM_BOT_TOKEN.encode(), "sha256")
    fields["hash"] = hmac.new(signing_key, message.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


class MiniAppTests(unittest.TestCase):
    def setUp(self):
        app = Flask(mini_app.__name__)
        app.register_blueprint(mini_app.mini_app)
        self.client = app.test_client()
        self.headers = {"Authorization": f"tma {init_data()}"}
        self.snapshot = {
            "updated_at": "2026-11-19T14:00:00Z",
            "data": {
                "Produk Navy SIZE Size L": {"stock": "Aman", "harga": "Rp 100.000"},
                "Produk Navy SIZE XL": {"stock": "Habis", "harga": "Rp 100.000"},
            },
        }

    def test_demo_shell_loads_without_a_telegram_session(self):
        page = self.client.get("/mini/")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Pantauan", page.data)
        self.assertIn(b"dashboard.js", page.data)
        self.assertIn(b'aria-label="Produk"', page.data)
        self.assertIn(b'id="category"', page.data)

    def test_require_signed_recent_telegram_data(self):
        old = init_data(auth_date=int(time.time()) - 7200)
        current = init_data()
        tampered = current.replace("Ayu", "Rani")
        duplicates = current + "&auth_date=" + str(int(time.time()))
        with patch.object(database, "get_user_settings") as settings:
            for value in ("", old, tampered, duplicates):
                with self.subTest(value=value):
                    result = self.client.get("/mini/api/dashboard", headers={"Authorization": f"tma {value}"})
                    self.assertEqual(result.status_code, 401)
            settings.assert_not_called()

    def test_expired_subscription_does_not_load_stock(self):
        with (
            patch.object(database, "get_user_settings", return_value={}),
            patch.object(database, "read_snapshot") as snapshot,
        ):
            result = self.client.get("/mini/api/dashboard", headers=self.headers)
        self.assertEqual(result.status_code, 403)
        snapshot.assert_not_called()

    def test_dashboard_lists_snapshot_and_current_users_watchlist(self):
        watch = {"product_name": "Produk Navy SIZE Size L", "last_restock": "2026-11-19T12:00:00Z"}
        with (
            patch.object(database, "get_user_settings", return_value={"role": "admin"}),
            patch.object(database, "read_snapshot", return_value=self.snapshot),
            patch.object(database, "call_rpc", return_value=[watch]) as rpc,
        ):
            response = self.client.get("/mini/api/dashboard", headers=self.headers)
        data = response.get_json()
        product = data["watchlist"][0]["product"]
        self.assertEqual((product["name"], product["color"], product["size"]), ("Produk", "Navy", "L"))
        self.assertEqual(data["products"][1]["stock"], "Habis")
        self.assertIn("no-store", response.headers["Cache-Control"])
        rpc.assert_called_once_with("get_mini_watchlist", {"p_chat_id": "42"})

    def test_product_details_group_legacy_colors_and_prefer_erp_metadata(self):
        legacy = mini_app.product_details("(Glamloc) Arfina Set Rok Taupe ALL SIZE", {})
        self.assertEqual(
            (legacy["name"], legacy["color"], legacy["size"], legacy["category"]),
            ("Arfina Set Rok", "Taupe", "ALL SIZE", "Glamloc"),
        )
        self.assertEqual(mini_app.product_details("(Tunik Brukat) Ayu Navy SIZE M", {})["category"], "Tunik Brukat")
        self.assertEqual(mini_app.product_details("Ayu Navy SIZE M", {})["category"], "")
        synced = mini_app.product_details("Produk Juni Beryl SIZE L", {
            "product_name": "Produk Juni",
            "color": "Beryl",
        })
        self.assertEqual((synced["name"], synced["color"], synced["size"]), ("Produk Juni", "Beryl", "L"))

    def test_watchlist_mutations_use_the_signed_chat_id(self):
        product = "Produk Navy SIZE Size L"
        with (
            patch.object(database, "get_user_settings", return_value={"role": "admin"}),
            patch.object(database, "read_snapshot", return_value=self.snapshot),
            patch.object(database, "call_rpc", return_value=None) as rpc,
        ):
            added = self.client.post("/mini/api/watchlist", headers=self.headers, json={
                "product": product, "chat_id": "999",
            })
            self.assertEqual(added.get_json(), {"last_restock": None})
            rpc.assert_called_once_with("set_mini_watch", {
                "p_chat_id": "42", "p_product_name": product, "p_enabled": True,
            })
            rpc.reset_mock()
            removed = self.client.delete("/mini/api/watchlist", headers=self.headers, json={"product": product})
            self.assertEqual(removed.status_code, 204)
            rpc.assert_called_once_with("set_mini_watch", {
                "p_chat_id": "42", "p_product_name": product, "p_enabled": False,
            })

    def test_catalog_uses_saved_markup_and_checks_live_availability(self):
        settings = {"role": "admin", "markup_type": "nominal", "markup_value": 30000, "admin_fee": 0}
        with (
            patch.object(database, "get_user_settings", return_value=settings),
            patch.object(database, "read_snapshot", return_value=self.snapshot),
        ):
            ready = self.client.post("/mini/api/catalog", headers=self.headers, json={"products": ["Produk Navy SIZE Size L"]})
            sold_out = self.client.post("/mini/api/catalog", headers=self.headers, json={"products": ["Produk Navy SIZE XL"]})
        self.assertEqual(ready.status_code, 200)
        self.assertIn("Rp 131.300", ready.get_json()["text"])
        self.assertFalse(ready.get_json()["uses_cost"])
        self.assertEqual(sold_out.status_code, 409)

    def test_dashboard_command_sends_https_web_app_button(self):
        bot = Mock()
        handlers = []

        def register(**_):
            def decorate(handler):
                handlers.append(handler)
                return handler
            return decorate

        bot.message_handler.side_effect = register
        with (
            patch.object(config, "MINI_APP_URL", "https://example.com/mini/"),
            patch.object(mini_app.commands, "_check_subscription", return_value=True),
        ):
            mini_app.register_handlers(bot)
            handlers[0](SimpleNamespace(chat=SimpleNamespace(id=42, type="private")))
        keyboard = bot.reply_to.call_args.kwargs["reply_markup"]
        self.assertEqual(keyboard.to_dict()["inline_keyboard"][0][0]["web_app"]["url"], "https://example.com/mini/")

    def test_due_alerts_are_acknowledged_after_sending(self):
        pending = {"chat_id": "42", "event_id": 7, "product_name": "Produk Navy SIZE L"}
        bot = Mock()
        with (
            patch.object(database, "call_rpc", return_value=[pending]),
            patch.object(database.session, "delete") as delete,
            patch.object(mini_app.time, "sleep"),
        ):
            mini_app.send_restock_alerts(bot)
        self.assertIn(pending["product_name"], bot.send_message.call_args.args[1])
        self.assertEqual(delete.call_args.kwargs["params"], {"chat_id": "eq.42", "event_id": "eq.7"})
