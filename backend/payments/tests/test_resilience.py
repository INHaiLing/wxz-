import json
from datetime import timedelta
from io import StringIO
from unittest.mock import Mock, patch

from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.crypto import decrypt_session_key, encrypt_session_key
from common.errors import BusinessError
from entitlements.models import EntitlementSource, OpeningReservation
from payments import gateway
from payments.models import AccessTokenCache, Order, PaymentEvent, PaymentTask
from payments.refund_queries import KIND
from payments.services import create_order, prepare_payment
from payments.synchronization import apply_query
from payments.tasks import MAX_ATTEMPTS, claim_task
from .sync_fixtures import SyncFixture, envelope, snapshot
from .test_preparation import PAYMENT_SETTINGS, fixtures


def cache_token(value="old-disposable-token"):
    cache = AccessTokenCache.objects.filter(pk=PAYMENT_SETTINGS["WECHAT_APP_ID"]).first()
    cache = cache or AccessTokenCache(app_id=PAYMENT_SETTINGS["WECHAT_APP_ID"])
    cache.ciphertext = encrypt_session_key(value)
    cache.expires_at = timezone.now() + timedelta(hours=1)
    cache.save(_service=True)
    return cache


def inquiry(order, **overrides):
    return {"MsgType": "event", "Event": KIND, "CreateTime": 1714112445,
            "refund_time": "1714112445", "order_time": "1714112400",
            "channel_bill": "disposable-apple-receipt", "bundleid": "com.tencent.xin",
            "product_id": order.platform_product_id, "p_count": "1",
            "refund_request_reason": "测试退款问询", "provide_status": "1",
            "pay_order_id": order.pk, **overrides}


@override_settings(**PAYMENT_SETTINGS)
class TokenRecoveryTests(SyncFixture):
    def test_all_official_token_failures_refresh_once_with_false_and_exact_query(self):
        for code in (40001, 40014, 42001):
            with self.subTest(code=code):
                cache_token()
                with patch("payments.gateway.official_post", side_effect=[
                    gateway.PlatformRequestFailure(code),
                    {"access_token": "new-disposable-token", "expires_in": 7200},
                    {"errcode": 0, "order": snapshot(self.order)},
                ]) as post:
                    self.assertEqual(gateway.query_order(self.order)["status"], 2)
                first, refresh, second = post.call_args_list
                self.assertEqual((first.args[0], refresh.args[0], second.args[0]),
                                 ("/xpay/query_order", "/cgi-bin/stable_token", "/xpay/query_order"))
                self.assertEqual(first.args[1], second.args[1])
                self.assertEqual(first.args[2]["pay_sig"], second.args[2]["pay_sig"])
                self.assertEqual(first.args[2]["access_token"], "old-disposable-token")
                self.assertEqual(second.args[2]["access_token"], "new-disposable-token")
                self.assertIs(json.loads(refresh.args[1])["force_refresh"], False)
                cache = AccessTokenCache.objects.get()
                self.assertEqual(decrypt_session_key(cache.ciphertext), "new-disposable-token")
                self.assertNotIn("new-disposable-token", cache.ciphertext)

    def test_delivery_recovery_has_no_pay_sig_and_preserves_exact_body(self):
        cache_token()
        with patch("payments.gateway.official_post", side_effect=[
            gateway.PlatformRequestFailure(42001),
            {"access_token": "new-disposable-token", "expires_in": 7200}, {},
        ]) as post:
            gateway.notify_goods(self.order)
        first, _, second = post.call_args_list
        self.assertEqual(first.args[1], second.args[1])
        self.assertEqual(set(first.args[2]), {"access_token"})
        self.assertEqual(set(second.args[2]), {"access_token"})
        self.assertTrue(first.kwargs["allow_empty"])
        self.assertTrue(second.kwargs["allow_empty"])

    def test_network_non_token_and_malformed_errors_do_not_refresh_cache(self):
        for error in (BusinessError("PLATFORM_UNAVAILABLE", "safe", 503),
                      gateway.PlatformRequestFailure(268490003),
                      gateway.PlatformRequestFailure(-1)):
            with self.subTest(error=type(error).__name__):
                cache = cache_token()
                original = cache.ciphertext
                with patch("payments.gateway.official_post", side_effect=error) as post:
                    with self.assertRaises(BusinessError):
                        gateway.query_order(self.order)
                self.assertEqual(post.call_count, 1)
                cache.refresh_from_db()
                self.assertEqual(cache.ciphertext, original)

    def test_second_rejection_stops_after_two_xpay_calls_and_safe_public_error(self):
        cache_token()
        with patch("payments.gateway.official_post", side_effect=[
            gateway.PlatformRequestFailure(40014),
            {"access_token": "new-disposable-token", "expires_in": 7200},
            gateway.PlatformRequestFailure(42001),
        ]) as post:
            with self.assertRaises(BusinessError) as error:
                gateway.query_order(self.order)
        self.assertEqual(post.call_count, 3)
        self.assertEqual(str(error.exception.detail["error"]["code"]), "PLATFORM_UNAVAILABLE")
        for sensitive in ("42001", "new-disposable-token", "pay_sig", "access_token"):
            self.assertNotIn(sensitive, str(error.exception.detail))

    def test_stable_token_failure_never_recursively_refreshes(self):
        cache_token()
        with patch("payments.gateway.official_post", side_effect=[
            gateway.PlatformRequestFailure(40014), gateway.PlatformRequestFailure(40001),
        ]) as post:
            with self.assertRaises(BusinessError):
                gateway.query_order(self.order)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(post.call_args_list[-1].args[0], "/cgi-bin/stable_token")

    def test_late_old_request_preserves_an_already_replaced_cache(self):
        cache_token()
        calls = []

        def platform(path, body, query=None, **kwargs):
            calls.append((path, query))
            if len(calls) == 1:
                cache_token("new-disposable-token")
                raise gateway.PlatformRequestFailure(40014)
            return {"errcode": 0, "order": snapshot(self.order)}

        with patch("payments.gateway.official_post", side_effect=platform):
            self.assertEqual(gateway.query_order(self.order)["status"], 2)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(path == "/xpay/query_order" for path, _ in calls))
        self.assertEqual(calls[-1][1]["access_token"], "new-disposable-token")

    def test_short_positive_stable_token_has_positive_early_cache_lifetime(self):
        now = timezone.now()
        with patch("payments.gateway.official_post", return_value={"access_token": "short-disposable-token", "expires_in": 30}), \
                patch("payments.gateway.timezone.now", return_value=now):
            self.assertEqual(gateway.access_token(self.order.app_id), "short-disposable-token")
        cache = AccessTokenCache.objects.get()
        self.assertEqual((cache.expires_at - now).total_seconds(), 15)

    def test_invalid_stable_lifetimes_roll_back_without_cache_or_success(self):
        for seconds in (0, -1, True, 1.5, 7201, None):
            with self.subTest(seconds=seconds), patch("payments.gateway.official_post", return_value={
                    "access_token": "disposable-token", "expires_in": seconds}):
                with self.assertRaises(BusinessError):
                    gateway.access_token(self.order.app_id)
                self.assertFalse(AccessTokenCache.objects.exists())

    def test_numeric_failure_is_internal_and_platform_errmsg_never_survives(self):
        response = Mock(status=200)
        response.read.return_value = b'{"errcode":40014,"errmsg":"private-platform-details"}'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch("payments.gateway.build_opener") as opener:
            opener.return_value.open.return_value = response
            with self.assertRaises(gateway.PlatformRequestFailure) as error:
                gateway.official_post("/xpay/query_order", "{}")
        self.assertEqual(error.exception.platform_code, 40014)
        self.assertEqual(str(error.exception.detail["error"]["code"]), "PLATFORM_UNAVAILABLE")
        self.assertNotIn("private-platform-details", str(error.exception.detail))
        self.assertNotIn("40014", str(error.exception.detail))


@override_settings(**{**PAYMENT_SETTINGS, "VIRTUAL_PAYMENT_IOS_ENABLED": True})
class AppleInquiryAuditTests(TestCase):
    def setUp(self):
        self.token, self.session, self.actor, self.product = fixtures()
        order_id = create_order(self.session.user, self.session, self.product.pk, "ios", "inquiry-create")['order']['id']
        prepare_payment(self.session.user, self.session, order_id, "inquiry-prepare")
        self.order = Order.objects.get(pk=order_id)
        self.webhook = "/integrations/wechat/virtual-payment/"

    def callback(self, data):
        query, body = envelope(data)
        return self.client.post(self.webhook + query, body, content_type="application/json")

    def unchanged_business(self):
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "preparing")
        self.assertTrue(OpeningReservation.objects.get(user=self.session.user).is_active)
        self.assertEqual(EntitlementSource.objects.count(), 0)

    def test_known_ios_local_reference_is_audited_once_without_receipt_or_business_change(self):
        data = inquiry(self.order, OpenId="ignored-private-openid", unknown="ignored-private-field")
        self.assertEqual(self.callback(data).status_code, 503)
        self.assertEqual(self.callback(data).status_code, 503)
        event = PaymentEvent.objects.get(kind=KIND)
        self.assertEqual(event.order_id, self.order.pk)
        self.assertEqual(event.external_id, self.order.pk)
        self.assertEqual(event.details["association"], "linked")
        self.assertEqual(event.details["policy"], "platform_uncertain")
        self.assertIn("channel_bill_digest", event.details["info"])
        for private in (data["channel_bill"], data["OpenId"], data["unknown"]):
            self.assertNotIn(private, json.dumps(event.details))
        self.unchanged_business()

    def test_verified_platform_reference_also_links_and_admin_remains_readonly(self):
        apply_query(self.order.pk, snapshot(self.order, 1))
        self.order.refresh_from_db()
        data = inquiry(self.order, pay_order_id=self.order.platform_order_id)
        self.assertEqual(self.callback(data).status_code, 503)
        event = PaymentEvent.objects.get(kind=KIND)
        self.assertEqual(event.order_id, self.order.pk)
        self.actor.user_permissions.add(Permission.objects.get(content_type__app_label="payments", codename="view_paymentevent"))
        self.client.force_login(self.actor)
        url = reverse("admin:payments_paymentevent_change", args=(event.pk,))
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertNotContains(self.client.get(url), data["channel_bill"])
        self.assertEqual(self.client.post(url, {"outcome": "applied"}).status_code, 403)
        self.unchanged_business()

    def test_legacy_public_only_fields_preserve_uncertain_and_explain_missing_info(self):
        self.assertEqual(self.callback({"MsgType": "event", "Event": KIND, "CreateTime": 1714112445}).status_code, 503)
        event = PaymentEvent.objects.get()
        self.assertIsNone(event.order_id)
        self.assertEqual(event.details["association"], "insufficient_info")
        self.assertEqual(event.error_code, "REFUND_QUERY_INCOMPLETE")
        self.unchanged_business()

    def test_unknown_order_product_and_quantity_mismatches_never_guess_an_order(self):
        variants = (({"pay_order_id": "unknown-provider-order"}, "unknown_order"),
                    ({"product_id": "wrong-product"}, "product_mismatch"),
                    ({"p_count": "2"}, "quantity_mismatch"))
        for fields, association in variants:
            with self.subTest(association=association):
                self.assertEqual(self.callback(inquiry(self.order, **fields)).status_code, 503)
                event = PaymentEvent.objects.filter(kind=KIND).latest("created_at")
                self.assertIsNone(event.order_id)
                self.assertEqual(event.details["association"], association)
        self.unchanged_business()

    def test_wrong_app_or_android_order_does_not_link(self):
        for index, changes in enumerate(({"app_id": "different-app"}, {"channel": "android"})):
            self.order.app_id = PAYMENT_SETTINGS["WECHAT_APP_ID"]
            self.order.channel = "ios"
            for name, value in changes.items():
                setattr(self.order, name, value)
            self.order.save(_service=True)
            self.assertEqual(self.callback(inquiry(self.order, refund_time=str(1714112445 + index))).status_code, 503)
            event = PaymentEvent.objects.filter(kind=KIND).latest("created_at")
            self.assertIsNone(event.order_id)
            self.assertEqual(event.details["association"], "order_mismatch")
        self.unchanged_business()

    def test_malformed_or_oversize_fields_are_not_saved_or_linked(self):
        variants = ({"channel_bill": "private-long-receipt" * 500}, {"product_id": "p" * 129},
                    {"p_count": True}, {"provide_status": "9"}, {"order_time": "not-a-time"},
                    {"refund_request_reason": "private-long-reason" * 100}, {"bundleid": {"secret": "private-nested-value"}},
                    {"refund_request_reason": "invalid\ud800text"},
                    {"pay_order_id": "invalid\nidentifier"})
        for fields in variants:
            with self.subTest(fields=list(fields)):
                data = inquiry(self.order, **fields)
                self.assertEqual(self.callback(data).status_code, 503)
                event = PaymentEvent.objects.filter(kind=KIND).latest("created_at")
                self.assertIsNone(event.order_id)
                self.assertEqual(event.details["association"], "invalid_fields")
                self.assertEqual(event.details["invalid_fields"], list(fields))
                self.assertTrue(all(field not in event.details["info"] for field in fields))
                for marker in ("private-long", "private-nested"):
                    self.assertNotIn(marker, json.dumps(event.details))
        self.unchanged_business()


@override_settings(**PAYMENT_SETTINGS)
class ExhaustedHeadScanTests(SyncFixture):
    def test_worker_skips_expired_exhausted_head_and_delivers_next_due_task(self):
        apply_query(self.order.pk, snapshot(self.order, 2))
        exhausted = PaymentTask.objects.get(kind="query")
        exhausted.status = "running"
        exhausted.attempts = MAX_ATTEMPTS
        exhausted.lease_token = "expired-lease"
        exhausted.lease_until = timezone.now() - timedelta(seconds=2)
        exhausted.next_run_at = timezone.now() - timedelta(seconds=3)
        exhausted.save(_service=True)
        with patch("payments.gateway.notify_goods") as delivered:
            call_command("run_payment_tasks", limit=1, stdout=StringIO())
        delivered.assert_called_once()
        exhausted.refresh_from_db()
        self.assertEqual(exhausted.status, "failed")
        self.assertEqual(exhausted.last_error_code, "RETRIES_EXHAUSTED")
        self.assertFalse(exhausted.lease_token)
        self.assertIsNone(exhausted.lease_until)
        self.assertEqual(PaymentTask.objects.get(kind="deliver").status, "done")

    def test_scan_cap_stops_boundedly_and_next_round_claims_remaining_due_task(self):
        apply_query(self.order.pk, snapshot(self.order, 2))
        now = timezone.now()
        for index, task in enumerate(PaymentTask.objects.order_by("pk")):
            task.status = "running"
            task.attempts = MAX_ATTEMPTS
            task.lease_token = "expired-lease"
            task.lease_until = now - timedelta(seconds=1)
            task.next_run_at = now - timedelta(seconds=3 - index)
            task.save(_service=True)
        from accounts.services import issue_student_session
        from accounts.wechat import WeChatLogin
        import base64
        _, other = issue_student_session(WeChatLogin(self.order.app_id, "scan-second-user", base64.b64encode(b"scan-key").decode()))
        order_id = create_order(other.user, other, self.product.pk, "android", "scan-other-order")["order"]["id"]
        prepare_payment(other.user, other, order_id, "scan-other-prepare")
        with patch("payments.tasks.CLAIM_SCAN_LIMIT", 2):
            self.assertIsNone(claim_task())
        self.assertEqual(PaymentTask.objects.filter(status="failed").count(), 2)
        self.assertEqual(PaymentTask.objects.get(order_id=order_id).status, "pending")
        claimed = claim_task()
        self.assertEqual(claimed[0], PaymentTask.objects.get(order_id=order_id).pk)
