import json

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from common.errors import BusinessError
from common.models import IdempotencyRecord
from entitlements.models import OpeningReservation
from entitlements.services import mark_product_synced
from payments.models import Order, PaymentTask
from payments.services import create_order, prepare_payment
from payments.synchronization import apply_query
from .sync_fixtures import snapshot
from .test_preparation import PAYMENT_SETTINGS, fixtures


@override_settings(**{**PAYMENT_SETTINGS, "VIRTUAL_PAYMENT_IOS_ENABLED": True})
class AppleLaunchGuardTests(TestCase):
    def setUp(self):
        self.token, self.session, self.actor, self.product = fixtures()
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + self.token)

    def sync_price(self, price_fen):
        self.product.price_fen = price_fen
        self.product.save()
        mark_product_synced(self.product.pk, self.actor, price_fen=price_fen,
                            platform_product_id=self.product.platform_product_id)
        self.product.refresh_from_db()

    def new_order(self, channel="ios", key="launch-order-key"):
        return create_order(self.session.user, self.session, self.product.pk, channel, key)["order"]["id"]

    def unavailable(self, function, *args):
        with self.assertRaises(BusinessError) as error:
            function(*args)
        self.assertEqual(error.exception.status_code, 503)
        self.assertEqual(str(error.exception.detail["error"]["code"]), "PAYMENT_CHANNEL_UNAVAILABLE")

    def no_new_transaction(self):
        self.assertFalse(Order.objects.exists())
        self.assertFalse(OpeningReservation.objects.exists())
        self.assertFalse(PaymentTask.objects.exists())
        self.assertFalse(IdempotencyRecord.objects.filter(operation__startswith="payment-").exists())

    @override_settings(VIRTUAL_PAYMENT_ENV=1)
    def test_ios_sandbox_hidden_and_http_creation_leaves_no_transaction(self):
        item = self.client.get("/api/student/v1/products/").data["results"][0]
        self.assertEqual(item["paymentChannels"], {"android": True, "ios": False})
        response = self.client.post("/api/student/v1/orders/",
                                    {"productId": self.product.pk, "channel": "ios"},
                                    format="json", HTTP_IDEMPOTENCY_KEY="launch-sandbox-http")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data["error"]["code"], "PAYMENT_CHANNEL_UNAVAILABLE")
        self.assertIn("no-store", response["Cache-Control"])
        self.no_new_transaction()

    def test_ios_below_one_yuan_is_hidden_and_http_creation_leaves_no_transaction(self):
        self.sync_price(99)
        item = self.client.get("/api/student/v1/products/").data["results"][0]
        self.assertEqual(item["paymentChannels"], {"android": True, "ios": False})
        with override_settings(VIRTUAL_PAYMENT_ANDROID_ENABLED=False):
            item = self.client.get("/api/student/v1/products/").data["results"][0]
            self.assertFalse(item["purchaseAvailable"])
            self.assertEqual(item["unavailableReason"], "PAYMENT_CHANNEL_UNAVAILABLE")
        response = self.client.post("/api/student/v1/orders/",
                                    {"productId": self.product.pk, "channel": "ios"},
                                    format="json", HTTP_IDEMPOTENCY_KEY="launch-low-http")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data["error"]["code"], "PAYMENT_CHANNEL_UNAVAILABLE")
        self.no_new_transaction()

    def test_live_one_yuan_boundary_prepares_once_and_replays_exact_packet(self):
        self.sync_price(100)
        self.assertTrue(self.client.get("/api/student/v1/products/").data["results"][0]["paymentChannels"]["ios"])
        order_id = self.new_order()
        result = prepare_payment(self.session.user, self.session, order_id, "launch-prepare-key")
        signed = json.loads(result["payment"]["signData"])
        self.assertEqual((signed["env"], signed["goodsPrice"]), (0, 100))
        self.assertEqual(result["payment"]["mode"], "short_series_goods")
        self.assertEqual(prepare_payment(self.session.user, self.session, order_id,
                                         "launch-prepare-key")["payment"], result["payment"])
        self.assertEqual(PaymentTask.objects.count(), 1)
        self.assertTrue(OpeningReservation.objects.get(user=self.session.user).is_active)

    @override_settings(VIRTUAL_PAYMENT_ENV=1)
    def test_android_sandbox_positive_price_rule_is_preserved(self):
        self.sync_price(1)
        order_id = self.new_order(channel="android")
        result = prepare_payment(self.session.user, self.session, order_id, "launch-android-prepare")
        signed = json.loads(result["payment"]["signData"])
        self.assertEqual((signed["env"], signed["goodsPrice"]), (1, 1))
        self.assertEqual(PaymentTask.objects.count(), 1)

    def test_created_order_after_environment_change_cannot_prepare_but_creation_replay_returns_history(self):
        order_id = self.new_order()
        with override_settings(VIRTUAL_PAYMENT_ENV=1):
            self.unavailable(prepare_payment, self.session.user, self.session, order_id, "launch-prepare-key")
            self.assertEqual(self.new_order(), order_id)
        order = Order.objects.get(pk=order_id)
        self.assertEqual(order.status, "created")
        self.assertEqual(order.sign_data, "")
        self.assertIsNone(order.prepared_at)
        self.assertFalse(OpeningReservation.objects.exists())
        self.assertFalse(PaymentTask.objects.exists())
        self.assertFalse(IdempotencyRecord.objects.filter(operation="payment-prepare").exists())

    def test_old_below_minimum_order_snapshot_cannot_prepare(self):
        order_id = self.new_order()
        order = Order.objects.get(pk=order_id)
        order.price_fen = 99  # A pre-guard snapshot must not regain a payable packet.
        order.save(_service=True)
        self.unavailable(prepare_payment, self.session.user, self.session, order_id, "launch-prepare-key")
        order.refresh_from_db()
        self.assertEqual(order.status, "created")
        self.assertFalse(order.sign_data)
        self.assertFalse(OpeningReservation.objects.exists())
        self.assertFalse(PaymentTask.objects.exists())
        self.assertFalse(IdempotencyRecord.objects.filter(operation="payment-prepare").exists())

    def test_prepared_replay_rechecks_environment_and_price_without_releasing_existing_reservation(self):
        order_id = self.new_order()
        original = prepare_payment(self.session.user, self.session, order_id, "launch-prepare-key")
        with override_settings(VIRTUAL_PAYMENT_ENV=1):
            self.unavailable(prepare_payment, self.session.user, self.session, order_id, "launch-prepare-key")
        self.sync_price(99)
        self.unavailable(prepare_payment, self.session.user, self.session, order_id, "launch-prepare-key")
        order = Order.objects.get(pk=order_id)
        self.assertEqual(order.status, "preparing")
        self.assertEqual(order.sign_data, original["payment"]["signData"])
        self.assertTrue(OpeningReservation.objects.get(user=self.session.user).is_active)
        self.assertEqual(PaymentTask.objects.count(), 1)
        self.assertEqual(IdempotencyRecord.objects.filter(operation="payment-prepare").count(), 1)
        self.assertEqual(self.new_order(), order_id)

    def test_closed_new_sales_do_not_block_verified_payment_refund_or_terminal_replay(self):
        order_id = self.new_order()
        prepare_payment(self.session.user, self.session, order_id, "launch-prepare-key")
        order = Order.objects.get(pk=order_id)
        with override_settings(VIRTUAL_PAYMENT_ENV=1, VIRTUAL_PAYMENT_IOS_ENABLED=False):
            paid = apply_query(order_id, snapshot(order, 2))
            self.assertTrue(paid["entitlement"]["active"])
            result = prepare_payment(self.session.user, self.session, order_id, "launch-prepare-key")
            self.assertIsNone(result["payment"])
            refunded = apply_query(order_id, snapshot(order, 8))
            self.assertEqual(refunded["order"]["status"], "refunded")
            self.assertFalse(refunded["entitlement"]["active"])
            replay = prepare_payment(self.session.user, self.session, order_id, "launch-prepare-key")
            self.assertIsNone(replay["payment"])
            self.assertFalse(replay["entitlement"]["active"])
            self.assertEqual(self.new_order(), order_id)
            self.assertEqual(self.client.get(f"/api/student/v1/orders/{order_id}/").status_code, 200)
