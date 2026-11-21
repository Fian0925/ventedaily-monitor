import hashlib
import hmac
import json
import os
import runpy
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import urlencode

import requests
from flask import Flask
from telebot import TeleBot, types

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
        self.assertFalse(ready.get_json()["zero_profit"])
        self.assertEqual(sold_out.status_code, 409)

    def test_product_prices_use_each_accounts_settings_and_match_catalog(self):
        product = "Produk Navy SIZE Size L"
        reference = "Produk Beige SIZE M"
        self.snapshot["data"][reference] = {"stock": "Ready", "harga": "Rp 170.000", "catalog_price": True}
        accounts = {
            "42": {"role": "admin", "markup_type": "nominal", "markup_value": 30000, "admin_fee": 0},
            "99": {"role": "admin", "marketplace": "tokopedia", "markup_type": "nominal",
                   "markup_value": 15000, "admin_fee": 5},
        }
        with (
            patch.object(database, "get_user_settings", side_effect=accounts.__getitem__),
            patch.object(database, "read_snapshot", return_value=self.snapshot),
            patch.object(database, "call_rpc", return_value=[]),
        ):
            for user_id, price, fee, profit in ((42, 131300, 0, 30050), (99, 122400, 6120, 15030)):
                with self.subTest(user=user_id):
                    headers = {"Authorization": f"tma {init_data(user_id=user_id)}"}
                    dashboard = self.client.get("/mini/api/dashboard", headers=headers).get_json()
                    variants = {item["id"]: item for item in dashboard["products"]}
                    self.assertEqual(variants[product]["selling_price"], price)
                    self.assertEqual(variants[product]["price_source"], "reseller")
                    self.assertEqual(variants[reference]["price_source"], "catalog")
                    self.assertIsNone(variants[reference]["selling_price"])
                    self.assertEqual(set(dashboard["price_quotes"]), {"Rp 100.000"})
                    quote = dashboard["price_quotes"]["Rp 100.000"]
                    self.assertEqual((quote["harga_jual"], quote["admin_fee"], quote["actual_profit"]), (price, fee, profit))
                    self.assertEqual((quote["modal_vente"], quote["biaya_tambahan"]), (100000, config.BIAYA_TAMBAHAN))
                    catalog = self.client.post("/mini/api/catalog", headers=headers, json={
                        "products": [product, reference],
                    }).get_json()
                    self.assertIn(f"Rp {price:,}".replace(",", "."), catalog["text"])
                    self.assertIn("Tanya harga", catalog["text"])
                    self.assertNotIn("Rp 170.000", catalog["text"])

    def test_defect_variants_are_hidden_and_cannot_be_added(self):
        removed = {
            "[DEFECT] Produk Lime SIZE L": {"stock": "Aman", "harga": "Rp 75.000"},
            "Produk Lime SIZE XL": {"stock": " dEfEcT ", "harga": "Rp 75.000"},
            "Produk Pink SIZE M": {"stock": "Ready", "product_name": "(Defect) Produk", "harga": "Rp 75.000"},
        }
        self.snapshot["data"].update(removed)
        with (
            patch.object(database, "get_user_settings", return_value={"role": "admin"}),
            patch.object(database, "read_snapshot", return_value=self.snapshot),
            patch.object(database, "call_rpc", return_value=[{"product_name": name} for name in removed]),
        ):
            response = self.client.get("/mini/api/dashboard", headers=self.headers).get_json()
            self.assertEqual({item["stock"] for item in response["products"]}, {"Aman", "Habis"})
            self.assertEqual(response["watchlist"], [])
            for name in removed:
                with self.subTest(variant=name):
                    watch = self.client.post("/mini/api/watchlist", headers=self.headers, json={"product": name})
                    catalog = self.client.post("/mini/api/catalog", headers=self.headers, json={"products": [name]})
                    self.assertEqual(watch.status_code, 404)
                    self.assertEqual(catalog.status_code, 409)

    def test_pricing_settings_use_the_signed_user_and_keep_accounts_separate(self):
        accounts = {
            "42": {"role": "admin", "marketplace": "shopee", "markup_type": "nominal", "markup_value": 30000.0},
            "99": {"role": "admin", "marketplace": "lazada", "markup_value": 20, "admin_fee": 0},
        }
        path = "/mini/api/pricing/settings"
        other_headers = {"Authorization": f"tma {init_data(user_id=99)}"}

        def update(chat_id, pricing):
            accounts[chat_id].update(pricing)
            return accounts[chat_id]

        with (
            patch.object(database, "get_user_settings", side_effect=accounts.__getitem__),
            patch.object(database, "update_user_pricing", side_effect=update) as save,
        ):
            original = self.client.get(path, headers=self.headers)
            self.assertEqual(original.get_json()["settings"]["markup_value"], "30000")
            self.assertIn("no-store", original.headers["Cache-Control"])
            payload = {
                "marketplace": "tokopedia", "admin_fee": "5,25",
                "markup_type": "nominal", "markup_value": "40.000",
            }
            result = self.client.put(path, headers=self.headers, json={**payload, "chat_id": "99", "role": "user"})
            self.assertEqual(result.status_code, 200)
            saved = {**payload, "admin_fee": "5.25", "markup_value": "40000"}
            save.assert_called_once_with("42", saved)
            self.assertEqual(self.client.get(path, headers=self.headers).get_json()["settings"], saved)
            other = self.client.get(path, headers=other_headers).get_json()["settings"]
            self.assertEqual((other["marketplace"], other["markup_value"], other["admin_fee"]), ("lazada", "20", "0"))
            self.assertEqual(self.client.put(path, json=payload).status_code, 401)

    def test_pricing_preview_accepts_rupiah_formats_and_rounds_up(self):
        path = "/mini/api/pricing/calculate"
        payload = {"marketplace": "shopee", "admin_fee": "0,01", "markup_type": "nominal", "markup_value": "29.950"}
        with (
            patch.object(database, "get_user_settings", return_value={"role": "admin"}),
            patch.object(database, "update_user_pricing") as save,
        ):
            for cost in ("30000", "30,000", "30.000"):
                with self.subTest(cost=cost):
                    result = self.client.post(path, headers=self.headers, json={**payload, "cost": cost})
                    self.assertEqual(result.status_code, 200)
                    data = result.get_json()
                    self.assertEqual(data["harga_jual"], 61300)
                    self.assertGreaterEqual(data["actual_profit"], 29950)
            for invalid in ({"cost": "30,00"}, {"admin_fee": "100"}, {"markup_value": "NaN"}):
                result = self.client.post(path, headers=self.headers, json={**payload, "cost": "30000", **invalid})
                self.assertEqual(result.status_code, 400)
            save.assert_not_called()

    def test_pricing_update_requires_supabase_to_return_the_updated_account(self):
        values = {"marketplace": "tokopedia", "admin_fee": "0", "markup_type": "percent", "markup_value": "20"}
        with (
            patch.object(database, "get_user_settings", return_value={"role": "admin"}),
            patch.object(database.session, "patch") as update,
        ):
            update.return_value.json.return_value = []
            response = self.client.put("/mini/api/pricing/settings", headers=self.headers, json=values)
            self.assertEqual(response.status_code, 503)
            self.assertEqual(update.call_args.kwargs["params"], {"chat_id": "eq.42"})
            self.assertEqual(update.call_args.kwargs["json"], values)

    def test_render_url_is_used_when_no_override_is_set(self):
        with patch.dict(os.environ, {"MINI_APP_URL": "", "RENDER_EXTERNAL_URL": "https://bot.onrender.com/"}):
            settings = runpy.run_path(config.__file__)
            self.assertEqual(settings["MINI_APP_URL"], "https://bot.onrender.com/mini/")
        with patch.dict(os.environ, {
            "MINI_APP_URL": "https://dashboard.example/mini/",
            "RENDER_EXTERNAL_URL": "https://bot.onrender.com",
        }):
            self.assertEqual(runpy.run_path(config.__file__)["MINI_APP_URL"], "https://dashboard.example/mini/")

    def test_dashboard_waits_for_schema_before_showing_web_app_button(self):
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
            patch.object(database, "call_rpc", side_effect=[requests.HTTPError(), []]) as check,
        ):
            mini_app.register_handlers(bot)
            message = SimpleNamespace(chat=SimpleNamespace(id=42, type="private"))
            handlers[0](message)
            self.assertIn("Dashboard belum siap", bot.reply_to.call_args.args[1])
            self.assertNotIn("reply_markup", bot.reply_to.call_args.kwargs)
            check.assert_called_once_with("get_mini_watchlist", {"p_chat_id": "42"})

            handlers[0](message)
            keyboard = bot.reply_to.call_args.kwargs["reply_markup"]
            self.assertEqual(
                keyboard.to_dict()["inline_keyboard"][0][0]["web_app"]["url"],
                "https://example.com/mini/",
            )

    def test_dashboard_quick_button_opens_the_private_mini_app(self):
        bot = TeleBot("123:offline-test", threaded=False)
        mini_app.register_handlers(bot)
        mini_app.commands.register_handlers(bot)

        def message(text, chat_type="private"):
            return types.Message.de_json({
                "message_id": 1,
                "date": int(time.time()),
                "chat": {"id": 42, "type": chat_type},
                "from": {"id": 42, "first_name": "Ayu", "is_bot": False},
                "text": text,
            })

        shortcut = mini_app.commands.BTN_DASHBOARD
        order_shortcut = mini_app.commands.BTN_CEK_PESANAN
        with (
            patch.object(config, "MINI_APP_URL", "https://example.com/mini/"),
            patch.object(database, "get_user_settings", return_value={"role": "admin"}),
            patch.object(database, "call_rpc", return_value=[]) as rpc,
            patch.object(bot, "reply_to") as reply,
        ):
            bot.process_new_messages([message("/start")])
            keyboard = json.loads(reply.call_args.kwargs["reply_markup"].to_json())["keyboard"]
            self.assertEqual(keyboard[0][0]["text"], shortcut)
            self.assertEqual(keyboard[0][1]["text"], order_shortcut)

            bot.process_new_messages([message(shortcut)])
            button = reply.call_args.kwargs["reply_markup"].to_dict()["inline_keyboard"][0][0]
            self.assertEqual(button, {"text": "Buka Dashboard", "web_app": {"url": "https://example.com/mini/"}})
            rpc.assert_called_once_with("get_mini_watchlist", {"p_chat_id": "42"})

            bot.process_new_messages([message(shortcut, "group")])
            self.assertIn("chat pribadi", reply.call_args.args[1])
            rpc.assert_called_once()

            with patch.object(config, "MINI_APP_URL", "https://example.com/mini/?source=bot"):
                bot.process_new_messages([message(order_shortcut)])
            button = reply.call_args.kwargs["reply_markup"].to_dict()["inline_keyboard"][0][0]
            self.assertEqual(button, {
                "text": "Buka Cek Pesanan",
                "web_app": {"url": "https://example.com/mini/?source=bot&view=cek-pesanan"},
            })

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
