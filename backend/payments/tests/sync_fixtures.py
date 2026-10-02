import base64
import json
import struct

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from django.contrib.auth.models import Permission
from django.contrib.auth import get_user_model
from django.test import TestCase

from payments.models import Order
from payments.protocol import message_signature
from payments.services import create_order, prepare_payment
from .test_preparation import PAYMENT_SETTINGS, fixtures


class SyncFixture(TestCase):
    def setUp(self):
        self.token, self.session, self.actor, self.product = fixtures()
        self.actor.user_permissions.add(*Permission.objects.filter(content_type__app_label__in=("payments", "entitlements")))
        self.actor = get_user_model().objects.get(pk=self.actor.pk)
        order_id = create_order(self.session.user, self.session, self.product.pk, "android", "sync-order-1")["order"]["id"]
        prepare_payment(self.session.user, self.session, order_id, "sync-prepare-1")
        self.order = Order.objects.get(pk=order_id)


def goods(order, **overrides):
    return {"MsgType": "event", "Event": "xpay_goods_deliver_notify", "CreateTime": 1714112445,
            "OpenId": order.identity.openid, "OutTradeNo": order.pk, "Env": order.environment,
            "GoodsInfo": {"ProductId": order.platform_product_id, "Quantity": 1, "OrigPrice": order.price_fen,
                          "ActualPrice": order.price_fen, "Attach": order.pk},
            "WeChatPayInfo": {"TransactionId": "txn-" + order.pk, "PaidTime": 1714112400}, **overrides}


def refund(order, **overrides):
    return {"MsgType": "event", "Event": "xpay_refund_notify", "CreateTime": 1714112545,
            "OpenId": order.identity.openid, "MchOrderId": order.pk,
            "WxOrderId": "wx-" + order.pk, "WxRefundId": "refund-" + order.pk,
            "MchRefundId": "merchant-refund-" + order.pk, "RefundFee": order.price_fen,
            "RefundStartTimestamp": 1714112500, "RefundSuccTimestamp": 1714112540,
            "RetCode": 0, **overrides}


def snapshot(order, status=2, **overrides):
    return {"order_id": order.pk, "status": status, "env_type": order.environment + 1,
            "order_type": 7 if order.channel == "ios" else 0, "order_fee": order.price_fen,
            "paid_fee": order.price_fen, "wx_order_id": "wx-" + order.pk,
            "wxpay_order_id": "txn-" + order.pk, "biz_meta": order.pk,
            "left_fee": 0 if status in (5, 8) else order.price_fen, "update_time": status + 1714112500, **overrides}


def encrypt(raw, *, app_id=PAYMENT_SETTINGS["WECHAT_APP_ID"], key=b"x" * 32):
    clear = b"fixture-random16" + struct.pack("!I", len(raw)) + raw + app_id.encode()
    count = 32 - len(clear) % 32
    encryptor = Cipher(algorithms.AES(key), modes.CBC(key[:16])).encryptor()
    return base64.b64encode(encryptor.update(clear + bytes([count]) * count) + encryptor.finalize()).decode()


def envelope(data, *, xml=False, app_id=PAYMENT_SETTINGS["WECHAT_APP_ID"]):
    raw = data if isinstance(data, bytes) else json.dumps(data).encode()
    encrypted = encrypt(raw, app_id=app_id)
    signature = message_signature(PAYMENT_SETTINGS["VIRTUAL_PAYMENT_CALLBACK_TOKEN"], "1714112445", "12345", encrypted)
    query = "?encrypt_type=aes&timestamp=1714112445&nonce=12345&msg_signature=" + signature
    body = ("<xml><Encrypt>" + encrypted + "</Encrypt></xml>").encode() if xml else json.dumps({"Encrypt": encrypted}).encode()
    return query, body
