import base64
import json
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase, override_settings
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from common.errors import BusinessError
from payments import gateway
from payments.models import AccessTokenCache
from payments.protocol import decrypt_message, message_signature, parse_payload, verify_signature
from payments.signing import pay_signature
from .sync_fixtures import SyncFixture, encrypt, snapshot
from .test_preparation import PAYMENT_SETTINGS


class SafeMessageProtocolTests(SimpleTestCase):
    def test_official_safe_mode_vector_and_message_signature(self):
        encrypted = "+qdx1OKCy+5JPCBFWw70tm0fJGb2Jmeia4FCB7kao+/Q5c/ohsOzQHi8khUOb05JCpj0JB4RvQMkUyus8TPxLKJGQqcvZqzDpVzazhZv6JsXUnnR8XGT740XgXZUXQ7vJVnAG+tE8NUd4yFyjPy7GgiaviNrlCTj+l5kdfMuFUPpRSrfMZuMcp3Fn2Pede2IuQrKEYwKSqFIZoNqJ4M8EajAsjLY2km32IIjdf8YL/P50F7mStwntrA2cPDrM1kb6mOcfBgRtWygb3VIYnSeOBrebufAlr7F9mFUPAJGj04="
        # Fixed vector from the official mini-program safe-message page.
        self.assertEqual(message_signature("AAAAA", "1714112445", "415670741", encrypted), "046e02f8204d34f8ba5fa3b1db94908f3df2e9b3")
        payload = parse_payload(decrypt_message(encrypted, bytes(32), "wxba5fad812f8e6fb9"))
        self.assertEqual(payload["Event"], "debug_demo")
        self.assertEqual(payload["debug_str"], "hello world")
        verify_signature("AAAAA", {"timestamp": "1714112445", "nonce": "415670741", "msg_signature": "046e02f8204d34f8ba5fa3b1db94908f3df2e9b3"}, encrypted)

    def test_official_hmac_vector(self):
        body = '{"openid": "xxx", "user_ip": "127.0.0.1", "env": 0}'
        self.assertEqual(pay_signature("/xpay/query_user_balance", body, "12345"), "c37809f27c6d7fd1837ad2500a04512b66b34fd793a39a385fade56dca89a4b5")

    def test_bad_aes_padding_appid_length_and_signature_are_rejected(self):
        raw = b'{"Event":"safe"}'
        encrypted = encrypt(raw)
        for key, app in ((b"z" * 32, PAYMENT_SETTINGS["WECHAT_APP_ID"]), (b"x" * 32, "wrong-app")):
            with self.assertRaises(BusinessError):
                decrypt_message(encrypted, key, app)
        with self.assertRaises(BusinessError):
            decrypt_message("not-base64", b"x" * 32, PAYMENT_SETTINGS["WECHAT_APP_ID"])
        cipher = Cipher(algorithms.AES(b"x" * 32), modes.CBC(b"x" * 16)).encryptor()
        invalid_padding = base64.b64encode(cipher.update(b"x" * 31 + b"\x00") + cipher.finalize()).decode()
        with self.assertRaises(BusinessError):
            decrypt_message(invalid_padding, b"x" * 32, PAYMENT_SETTINGS["WECHAT_APP_ID"])
        with self.assertRaises(BusinessError):
            verify_signature("token", {"timestamp": "1", "nonce": "2", "signature": "0" * 40}, encrypted)

    def test_duplicate_nested_fields_dtd_entities_and_large_payload_rejected(self):
        for raw in (b'{"GoodsInfo":{"Quantity":1,"Quantity":2}}', b'<xml><Event>a</Event><Event>b</Event></xml>', b'<!DOCTYPE xml [<!ENTITY x "bad">]><xml><Event>&x;</Event></xml>', b'{"n":NaN}', b"x" * 65537, b"[]"):
            with self.subTest(raw=raw[:40]), self.assertRaises(BusinessError):
                parse_payload(raw)
        self.assertEqual(parse_payload(b"<xml><Event><![CDATA[valid]]></Event></xml>"), {"Event": "valid"})

    def test_http_adapter_rejects_redirect_timeout_overflow_and_bad_errcode(self):
        for operation in (TimeoutError(), gateway.HTTPError("https://api.weixin.qq.com", 302, "redirect", {}, None)):
            with patch("payments.gateway.build_opener") as opener:
                opener.return_value.open.side_effect = operation
                with self.assertRaises(BusinessError):
                    gateway.official_post("/xpay/query_order", "{}")
        for raw in (b"x" * 65537, b'{"errcode":40001,"errmsg":"SECRET"}', b'{"errcode":0,"errcode":1}'):
            response = Mock(status=200)
            response.read.return_value = raw
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            with patch("payments.gateway.build_opener") as opener:
                opener.return_value.open.return_value = response
                with self.assertRaises(BusinessError) as error:
                    gateway.official_post("/xpay/query_order", "{}")
                self.assertNotIn("SECRET", str(error.exception))
        response.read.return_value = b""
        with patch("payments.gateway.build_opener") as opener:
            opener.return_value.open.return_value = response
            self.assertEqual(gateway.official_post("/xpay/notify_provide_goods", "{}", allow_empty=True), {})


@override_settings(**PAYMENT_SETTINGS)
class OfficialGatewayTests(SyncFixture):
    def test_token_is_encrypted_cached_and_fixed_official_payload(self):
        with patch("payments.gateway.official_post", return_value={"access_token": "raw-test-platform-token", "expires_in": 7200}) as post:
            self.assertEqual(gateway.access_token(self.order.app_id), "raw-test-platform-token")
            self.assertEqual(gateway.access_token(self.order.app_id), "raw-test-platform-token")
            self.assertEqual(post.call_count, 1)
            path, body = post.call_args.args
            self.assertEqual(path, "/cgi-bin/stable_token")
            self.assertEqual(json.loads(body)["grant_type"], "client_credential")
            self.assertFalse(json.loads(body)["force_refresh"])
        cache = AccessTokenCache.objects.get()
        self.assertNotIn("raw-test-platform-token", cache.ciphertext)

    def test_query_exact_body_hmac_and_delivery_has_no_pay_sig(self):
        with patch("payments.gateway.access_token", return_value="test-token"), patch("payments.gateway.official_post", return_value={"errcode": 0, "order": snapshot(self.order)}) as post:
            gateway.query_order(self.order)
            path, body, query = post.call_args.args
            self.assertEqual(path, "/xpay/query_order")
            self.assertEqual(json.loads(body), {"openid": self.order.identity.openid, "env": 0, "order_id": self.order.pk})
            self.assertEqual(query["pay_sig"], pay_signature(path, body, PAYMENT_SETTINGS["VIRTUAL_PAYMENT_APP_KEY"]))
            self.assertEqual(query["access_token"], "test-token")
            gateway.notify_goods(self.order)
            path, body, query = post.call_args.args
            self.assertEqual(path, "/xpay/notify_provide_goods")
            self.assertEqual(query, {"access_token": "test-token"})
            self.assertTrue(post.call_args.kwargs["allow_empty"])

    def test_existing_transactions_sync_with_new_sales_disabled(self):
        with override_settings(VIRTUAL_PAYMENT_ENABLED=False), patch("payments.gateway.access_token", return_value="test-token"), patch("payments.gateway.official_post", return_value={"errcode": 0, "order": snapshot(self.order)}):
            self.assertEqual(gateway.query_order(self.order)["status"], 2)
