import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

import calculator
import commands
import database
import payments

with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "123:offline-test"}):
    with patch("config.TELEGRAM_BOT_TOKEN", "123:offline-test"):
        import monitor


class MonitorRegressionTests(unittest.TestCase):
    def test_stock_api_failure_keeps_reseller_price_but_updates_stock(self):
        product = "(Couple) Produk Blue SIZE L"
        catalog = [{"name": product, "status": "Aman", "selling_price": 150000}]
        previous = {product: {"stock": "Habis", "harga": "Rp 100.000"}}
        with patch.object(monitor, "_fetch_paginated", side_effect=[catalog, None]):
            current = monitor.fetch_all(previous)
        self.assertEqual(current[product], {"stock": "Aman", "harga": "Rp 100.000"})
        _, count, events = monitor.compare_data(previous, current)
        self.assertEqual(count, 1)
        self.assertEqual([event["event_type"] for event in events], ["restock"])

    def test_first_reseller_price_does_not_trigger_a_price_change(self):
        old = {"Produk": {"stock": "Aman", "harga": "Rp 150.000", "catalog_price": True}}
        new = {"Produk": {"stock": "Aman", "harga": "Rp 100.000"}}
        self.assertEqual(monitor.compare_data(old, new), ([], 0, []))

    def test_all_events_are_saved_before_broadcast(self):
        old = {f"Produk {i}": {"stock": "Habis", "harga": "Rp 10.000"} for i in range(101)}
        new = {name: dict(item, stock="Aman") for name, item in old.items()}
        with (
            patch.object(database, "read_snapshot", return_value={"data": old, "updated_at": "v1"}),
            patch.object(monitor, "fetch_all", return_value=new),
            patch.object(database, "save_snapshot", return_value=False) as save,
            patch.object(monitor, "broadcast_telegram_message") as broadcast,
        ):
            monitor.job()
        snapshot, version, events = save.call_args.args
        self.assertEqual(snapshot, new)
        self.assertEqual(version, "v1")
        self.assertEqual(len(events), 101)
        broadcast.assert_not_called()

    def test_reminder_writes_only_marker_for_original_expiry(self):
        until = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
        user = {"chat_id": "42", "valid_until": until, "reminder_sent": "1_day"}
        with (
            patch.object(database, "get_all_users", return_value=[user]),
            patch.object(monitor.bot, "send_message"),
            patch.object(database.session, "patch") as write,
        ):
            monitor.check_expirations_job()
        self.assertEqual(write.call_args.kwargs["json"], {"reminder_sent": "1_hour"})
        self.assertEqual(write.call_args.kwargs["params"]["valid_until"], f"eq.{until}")
        self.assertEqual(write.call_args.kwargs["params"]["reminder_sent"], "eq.1_day")

    def test_numeric_backup_can_be_searched_by_product_name(self):
        with patch.object(database, "load_snapshot", return_value=None):
            data = database.load_catalog_snapshot()
            name = next(name for name, row in data.items() if row["stock"] == "Aman")
            self.assertFalse(name.isdigit())
            bot = Mock()
            commands._run_search(bot, 42, name)
            self.assertIn("Hasil Pencarian", bot.send_message.call_args.args[1])
            with monitor.app.test_client() as client:
                self.assertIn(name, client.get("/api/data").get_json())


class CommandRegressionTests(unittest.TestCase):
    def test_search_and_catalog_keep_each_variants_price(self):
        stock = {
            "Produk Blue SIZE L": {"stock": "Aman", "harga": "Rp 100.000"},
            "Produk Red SIZE L": {"stock": "Aman", "harga": "Rp 150.000"},
        }
        with (
            patch.object(database, "load_catalog_snapshot", return_value=stock),
            patch.object(database, "get_user_settings", return_value={}),
            patch.object(commands.config, "BIAYA_TAMBAHAN", 0),
        ):
            for command in (commands._run_search, commands._run_katalog):
                with self.subTest(command=command.__name__):
                    bot = Mock()
                    command(bot, 42, "produk")
                    text = bot.send_message.call_args_list[0].args[1]
                    self.assertLess(text.index("100.000"), text.index("Blue"))
                    self.assertLess(text.index("Blue"), text.index("150.000"))
                    self.assertLess(text.index("150.000"), text.index("Red"))

    def test_trial_parses_equivalent_timestamps_and_reports_write_failure(self):
        settings = {"plan_type": "none", "valid_until": "2000-01-01T00:00:00+00:00"}
        self.assertTrue(commands._can_use_trial(settings))
        self.assertFalse(commands._can_use_trial(dict(settings, plan_type="suspended")))
        bot = Mock()
        with (
            patch.object(database, "get_user_settings", return_value=settings),
            patch.object(database, "activate_trial", side_effect=requests.Timeout),
        ):
            commands._activate_trial_for_user(bot, 42)
        self.assertIn("gagal", bot.send_message.call_args.args[1])

    def test_rupiah_profit_accepts_dot_and_comma_grouping(self):
        message = SimpleNamespace(chat=SimpleNamespace(id=42))
        for value in ("30000", "30,000", "30.000"):
            with self.subTest(value=value):
                self.assertEqual(calculator.parse_profit(value), ("nominal", 30000))
                with patch.object(database, "set_user_markup", return_value=True) as save:
                    commands._save_profit_setting(Mock(), message, value)
                save.assert_called_once_with(42, "nominal", 30000)

        self.assertEqual(calculator.parse_profit("Rp 30.000"), ("nominal", 30000))
        self.assertEqual(calculator.parse_profit("1,234,567"), ("nominal", 1234567))
        self.assertEqual(calculator.parse_profit("12,5%"), ("percent", 12.5))
        for invalid in ("30,00", "1,234.567", "nan%"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                calculator.parse_profit(invalid)

    def test_hitung_accepts_each_rupiah_format(self):
        stock = {"Produk SIZE L": {"stock": "Aman", "harga": "Rp 100.000"}}
        with (
            patch.object(database, "load_catalog_snapshot", return_value=stock),
            patch.object(database, "get_user_settings", return_value={}),
        ):
            for value in ("30000", "30,000", "30.000"):
                with self.subTest(value=value):
                    bot = Mock()
                    commands._run_hitung(bot, 42, f"produk {value}")
                    self.assertIn("Target Profit: <b>Rp 30.000</b>", bot.send_message.call_args.args[1])

    def test_history_reads_all_pages(self):
        pages = []
        for items in ([{"id": 3}, {"id": 2}], [{"id": 1}], []):
            response = Mock()
            response.json.return_value = items
            pages.append(response)
        with patch.object(database.session, "get", side_effect=pages) as query:
            self.assertEqual([event["id"] for event in database.get_events("restock")], [3, 2, 1])
        self.assertEqual([call.kwargs["params"][-1] for call in query.call_args_list],
                         [("offset", 0), ("offset", 2), ("offset", 3)])


class PaymentRegressionTests(unittest.TestCase):
    def test_payment_approval_uses_single_transaction(self):
        with patch.object(database, "call_rpc", return_value={"status": "paid"}) as rpc:
            self.assertEqual(payments.approve_payment("TEST", 42), {"status": "paid"})
        rpc.assert_called_once_with("approve_monitor_payment", {
            "p_invoice": "TEST", "p_admin_id": "42", "p_bonus_days": payments.config.REFERRAL_BONUS_DAYS,
        })

    def test_admin_notification_can_retry_after_telegram_failure(self):
        row = {"invoice": "TEST"}
        with patch.object(payments, "_patch", side_effect=[[row], [], [row], [], []]) as write:
            with patch.object(payments, "_notify_admin", side_effect=[TimeoutError, None]) as notify:
                bot = Mock()
                with self.assertRaises(TimeoutError):
                    payments._notify_admin_once(bot, "TEST")
                payments._notify_admin_once(bot, "TEST")
                payments._notify_admin_once(bot, "TEST")
        self.assertEqual(notify.call_count, 2)
        self.assertEqual(write.call_args_list[1].args[1], {"admin_notify_claimed_at": None})
        self.assertIn("admin_notified_at", write.call_args_list[3].args[1])


if __name__ == "__main__":
    unittest.main()
