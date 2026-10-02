"""Cross-module payment acceptance through actual HTTP and signed AES messages.

Only external WeChat code2session and query-order requests are replaced. The
backend authentication, signing, state machine, source grants and refund writes
remain the actual production services.
"""
import base64
import hashlib
import hmac
import json
import struct
from unittest.mock import patch

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from activation.models import ActivationCode
from entitlements.models import EntitlementSource, OpeningReservation, Product
from entitlements.services import mark_product_synced
from payments.models import Order, PaymentEvent, PaymentTask
from .test_flow import STUDENT_SETTINGS, StudentFlowFixture


PAYMENT_SETTINGS = {
    **STUDENT_SETTINGS,
    "VIRTUAL_PAYMENT_ENABLED": True,
    "VIRTUAL_PAYMENT_ANDROID_ENABLED": True,
    "VIRTUAL_PAYMENT_IOS_ENABLED": False,
    "VIRTUAL_PAYMENT_ENV": 0,
    "VIRTUAL_PAYMENT_OFFER_ID": "123",
    "VIRTUAL_PAYMENT_APP_KEY": "test-only-contract-appkey",
    "VIRTUAL_PAYMENT_SANDBOX_APP_KEY": "test-only-contract-sandbox-key",
    "VIRTUAL_PAYMENT_CALLBACK_TOKEN": "test-only-contract-callback-token",
    "VIRTUAL_PAYMENT_CALLBACK_AES_KEY": base64.b64encode(b"x" * 32).decode().rstrip("="),
}


@override_settings(**PAYMENT_SETTINGS)
class PaymentEndToEndTests(StudentFlowFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.product = Product.objects.create(platform_product_id="contract-permanent-bank")
        mark_product_synced(self.product.pk, self.operator,
                            price_fen=self.product.price_fen, platform_product_id=self.product.platform_product_id)
        self.login()

    def new_order(self, suffix="first"):
        result = self.call("post", "/api/student/v1/orders/",
                           {"productId": self.product.pk, "channel": "android"},
                           key="contract-order-" + suffix, status=201)
        return Order.objects.get(pk=result.data["order"]["id"])

    def prepare(self, order, suffix="first"):
        return self.call("post", f"/api/student/v1/orders/{order.pk}/payment/", {},
                         key="contract-prepare-" + suffix)

    def query(self, order, state, suffix="first", **changes):
        snapshot = {
            "order_id": order.pk, "status": state, "env_type": order.environment + 1,
            "order_type": 7 if order.channel == "ios" else 0, "order_fee": order.price_fen,
            "paid_fee": order.price_fen, "wx_order_id": "wx-" + order.pk,
            "wxpay_order_id": "txn-" + order.pk, "biz_meta": order.pk,
            "left_fee": 0 if state in (5, 8) else order.price_fen,
            "update_time": 1714112500 + state, **changes,
        }
        with patch("payments.gateway.query_order", return_value=snapshot) as query:
            response = self.call("post", f"/api/student/v1/orders/{order.pk}/query/", {},
                                 key="contract-query-" + suffix)
        return response, query.call_count

    def callback(self, clear_payload, *, status=200):
        # Independent sender-side construction; do not call backend protocol
        # helpers or bypass the public callback route.
        message = json.dumps(clear_payload, ensure_ascii=False, separators=(",", ":")).encode()
        clear = b"contract-rand-16" + struct.pack("!I", len(message)) + message + b"wx-contract-test"
        count = 32 - len(clear) % 32
        sender = Cipher(algorithms.AES(b"x" * 32), modes.CBC(b"x" * 16)).encryptor()
        encrypted = base64.b64encode(sender.update(clear + bytes([count]) * count) + sender.finalize()).decode()
        pieces = [PAYMENT_SETTINGS["VIRTUAL_PAYMENT_CALLBACK_TOKEN"], "1714112445", "12345", encrypted]
        signature = hashlib.sha1("".join(sorted(pieces)).encode()).hexdigest()
        route = "/integrations/wechat/virtual-payment/?encrypt_type=aes&timestamp=1714112445&nonce=12345&msg_signature=" + signature
        response = self.admin.post(route, json.dumps({"Encrypt": encrypted}), content_type="application/json")
        self.assertEqual(response.status_code, status, response.content)
        self.assertEqual(response.content, b"success" if status == 200 else b"platform_review_required")
        return response

    def paid_message(self, order):
        return {
            "MsgType": "event", "Event": "xpay_goods_deliver_notify", "CreateTime": 1714112445,
            "OpenId": order.identity.openid, "OutTradeNo": order.pk, "Env": order.environment,
            "GoodsInfo": {"ProductId": order.platform_product_id, "Quantity": 1,
                          "OrigPrice": order.price_fen, "ActualPrice": order.price_fen, "Attach": order.pk},
            "WeChatPayInfo": {"TransactionId": "txn-" + order.pk, "PaidTime": 1714112400},
        }

    def refund_message(self, order):
        return {
            "MsgType": "event", "Event": "xpay_refund_notify", "CreateTime": 1714112545,
            "OpenId": order.identity.openid, "MchOrderId": order.pk,
            "WxOrderId": "wx-" + order.pk, "WxTransactionId": "txn-" + order.pk,
            "WxRefundId": "refund-" + order.pk, "MchRefundId": "merchant-refund-" + order.pk,
            "Attach": order.pk, "RefundFee": order.price_fen,
            "RefundStartTimestamp": 1714112500, "RefundSuccTimestamp": 1714112540, "RetCode": 0,
        }

    def test_prepare_payment_blocks_code_then_callback_refund_and_late_replay(self):
        codes = self.activation_codes()
        order = self.new_order()
        packet = self.prepare(order).data["payment"]
        order.refresh_from_db()
        self.assertEqual(packet["signData"], order.sign_data)
        self.assertEqual(json.loads(packet["signData"])["goodsPrice"], 1000)
        expected = hmac.new(PAYMENT_SETTINGS["VIRTUAL_PAYMENT_APP_KEY"].encode(),
                            ("requestVirtualPayment&" + packet["signData"]).encode(), hashlib.sha256).hexdigest()
        self.assertEqual(packet["paySig"], expected)
        blocked = self.call("post", "/api/student/v1/activation/redeem/", {"code": codes[0]},
                            key="contract-during-payment", status=409)
        self.assertEqual(blocked.data["error"]["code"], "PURCHASE_IN_PROGRESS")
        self.assertEqual(ActivationCode.objects.filter(state="unused").count(), 2)
        self.assertTrue(OpeningReservation.objects.get().is_active)
        self.callback(self.paid_message(order))
        self.callback(self.paid_message(order))
        self.assertEqual(EntitlementSource.objects.filter(source_type="payment", status="active").count(), 1)
        self.assertFalse(OpeningReservation.objects.get().is_active)
        self.assertTrue(self.call("get", "/api/student/v1/me/entitlements/").data["permanent"])
        self.call("get", "/api/student/v1/questions/contract-3/")
        already = self.call("post", "/api/student/v1/activation/redeem/", {"code": codes[0]},
                            key="contract-after-payment", status=409)
        self.assertEqual(already.data["error"]["code"], "ALREADY_ACTIVATED")
        self.assertEqual(ActivationCode.objects.filter(state="unused").count(), 2)
        response, calls = self.query(order, 2)
        self.assertEqual((response.data["order"]["status"], calls), ("fulfilled", 1))
        self.callback(self.refund_message(order))
        self.callback(self.refund_message(order))
        self.callback(self.paid_message(order))
        replay = self.prepare(order).data
        self.assertEqual(replay["order"]["status"], "refunded")
        self.assertIsNone(replay["payment"])
        self.assertFalse(replay["entitlement"]["active"])
        self.call("get", "/api/student/v1/questions/contract-3/", status=403)
        self.assertEqual(EntitlementSource.objects.get(source_type="payment").status, "revoked")
        self.assertEqual(PaymentEvent.objects.filter(kind="xpay_goods_deliver_notify").count(), 1)
        self.assertEqual(PaymentEvent.objects.filter(kind="xpay_refund_notify").count(), 1)
        # A redeemed activation after refund is a separate valid source. Late
        # payment notifications still cannot revive the revoked payment source.
        self.call("post", "/api/student/v1/activation/redeem/", {"code": codes[0]}, key="contract-after-refund")
        self.callback(self.paid_message(order))
        self.callback(self.refund_message(order))
        self.assertTrue(self.call("get", "/api/student/v1/me/entitlements/").data["active"])
        self.assertEqual(EntitlementSource.objects.get(source_type="payment").status, "revoked")
        self.assertEqual(EntitlementSource.objects.get(source_type="activation").status, "active")

    def test_query_grants_once_replay_returns_current_refunded_state_and_owner_isolation(self):
        order = self.new_order()
        self.prepare(order)
        result, calls = self.query(order, 2)
        self.assertEqual((result.data["order"]["status"], calls), ("fulfilled", 1))
        self.assertTrue(PaymentTask.objects.filter(order=order, kind="deliver").exists())
        self.callback(self.paid_message(order))
        self.assertEqual(EntitlementSource.objects.filter(source_type="payment").count(), 1)
        refund, _ = self.query(order, 5, suffix="refund")
        self.assertEqual(refund.data["order"]["status"], "refunded")
        self.assertFalse(refund.data["entitlement"]["active"])
        old, calls = self.query(order, 2)
        self.assertEqual(calls, 0)
        self.assertEqual(old.data["order"]["status"], "refunded")
        self.assertFalse(old.data["entitlement"]["active"])
        self.call("get", "/api/student/v1/orders/")
        self.call("get", f"/api/student/v1/orders/{order.pk}/")
        second = APIClient()
        self.login(second, openid="different-contract-payer")
        self.call("get", f"/api/student/v1/orders/{order.pk}/", client=second, status=404)
        self.call("post", f"/api/student/v1/orders/{order.pk}/query/", {},
                  key="other-user-query", client=second, status=404)
        self.assertEqual(self.call("get", "/api/student/v1/orders/", client=second).data["count"], 0)

    def test_pending_payment_keeps_reservation_until_verified_final_unpaid_and_ios_closed(self):
        unavailable = self.call("post", "/api/student/v1/orders/",
                                {"productId": self.product.pk, "channel": "ios"},
                                key="contract-ios-closed", status=503)
        self.assertEqual(unavailable.data["error"]["code"], "PAYMENT_CHANNEL_UNAVAILABLE")
        self.assertFalse(Order.objects.exists())
        order = self.new_order()
        self.prepare(order)
        for state in (0, 1):
            result, _ = self.query(order, state, suffix="pending-" + str(state))
            self.assertEqual(result.data["order"]["status"], "preparing")
            self.assertTrue(OpeningReservation.objects.get().is_active)
        codes = self.activation_codes()
        self.call("post", "/api/student/v1/activation/redeem/", {"code": codes[0]},
                  key="contract-pending-code", status=409)
        final, _ = self.query(order, 6, suffix="unpaid-final")
        self.assertEqual(final.data["order"]["status"], "closed")
        self.assertFalse(OpeningReservation.objects.get().is_active)
        self.call("post", "/api/student/v1/activation/redeem/", {"code": codes[0]}, key="contract-final-code")
        self.callback(self.paid_message(order), status=503)
        self.assertFalse(EntitlementSource.objects.filter(source_type="payment").exists())
        self.assertTrue(self.call("get", "/api/student/v1/me/entitlements/").data["active"])
