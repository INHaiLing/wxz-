from unittest.mock import patch

from django.test import override_settings
from rest_framework.test import APIClient

from common.errors import BusinessError
from common.models import IdempotencyRecord
from entitlements.models import AuditEvent, EntitlementSource, OpeningReservation
from entitlements.services import grant_entitlement, revoke_entitlement
from payments.models import Order, PaymentEvent, PaymentTask
from payments.synchronization import apply_callback, apply_query
from .sync_fixtures import SyncFixture, envelope, goods, refund, snapshot
from .test_preparation import PAYMENT_SETTINGS


@override_settings(**PAYMENT_SETTINGS)
class PaymentSynchronizationTests(SyncFixture):
    def test_callback_payment_commit_replay_and_query_are_one_source(self):
        payload = goods(self.order)
        self.assertEqual(apply_callback(payload, self.order.app_id), "applied")
        self.assertEqual(apply_callback(payload, self.order.app_id), "applied")
        result = apply_query(self.order.pk, snapshot(self.order))
        self.assertEqual(result["order"]["status"], "fulfilled")
        self.assertTrue(result["entitlement"]["permanent"])
        self.assertEqual(EntitlementSource.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.filter(kind="granted").count(), 1)
        self.assertFalse(OpeningReservation.objects.get().is_active)
        self.assertTrue(PaymentTask.objects.filter(kind="deliver").exists())

    def test_refund_before_payment_and_late_paid_never_grants(self):
        self.assertEqual(apply_callback(refund(self.order), self.order.app_id), "applied")
        self.assertEqual(apply_callback(goods(self.order), self.order.app_id), "ignored")
        result = apply_query(self.order.pk, snapshot(self.order))
        self.assertEqual(result["order"]["status"], "refunded")
        self.assertFalse(result["entitlement"]["active"])
        self.assertEqual(EntitlementSource.objects.count(), 0)
        self.assertFalse(OpeningReservation.objects.get().is_active)

    def test_completed_refund_only_revokes_payment_source_and_failed_refund_does_not(self):
        apply_query(self.order.pk, snapshot(self.order))
        grant_entitlement(self.session.user, "activation", "additional-historical-code")
        self.assertEqual(apply_callback(refund(self.order, RetCode=1), self.order.app_id), "ignored")
        self.assertEqual(EntitlementSource.objects.filter(status="active").count(), 2)
        self.assertEqual(apply_callback(refund(self.order), self.order.app_id), "applied")
        apply_callback(refund(self.order), self.order.app_id)
        self.assertEqual(EntitlementSource.objects.get(source_type="payment").status, "revoked")
        self.assertEqual(EntitlementSource.objects.get(source_type="activation").status, "active")
        self.assertEqual(AuditEvent.objects.filter(kind="payment_refunded", source__isnull=False).count(), 1)
        self.assertTrue(all(item.actor_id is None for item in AuditEvent.objects.filter(kind="payment_refunded")))
        self.assertTrue(apply_query(self.order.pk, snapshot(self.order))["entitlement"]["active"])
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "refunded")

    def test_query_final_refund_and_partial_refund_or_failed_refund_states(self):
        apply_query(self.order.pk, snapshot(self.order))
        self.assertEqual(apply_query(self.order.pk, snapshot(self.order, 7))["order"]["status"], "fulfilled")
        partial = apply_query(self.order.pk, snapshot(self.order, 5, left_fee=500))
        self.assertEqual(partial["order"]["status"], "review")
        self.assertTrue(partial["entitlement"]["active"])
        completed = apply_query(self.order.pk, snapshot(self.order, 8))
        self.assertEqual(completed["order"]["status"], "refunded")
        self.assertFalse(completed["entitlement"]["active"])

    def test_pending_unknown_do_not_release_and_late_close_does_not_revoke_paid(self):
        for status in (0, 1, 99):
            apply_query(self.order.pk, snapshot(self.order, status))
            self.assertTrue(OpeningReservation.objects.get().is_active)
        apply_query(self.order.pk, snapshot(self.order))
        result = apply_query(self.order.pk, snapshot(self.order, 6))
        self.assertEqual(result["order"]["status"], "fulfilled")
        self.assertTrue(result["entitlement"]["active"])

    def test_final_unpaid_close_releases_only_matching_reservation(self):
        result = apply_query(self.order.pk, snapshot(self.order, 6))
        self.assertEqual(result["order"]["status"], "closed")
        self.assertFalse(OpeningReservation.objects.get().is_active)
        self.assertFalse(apply_query(self.order.pk, snapshot(self.order))["entitlement"]["active"])
        self.assertEqual(EntitlementSource.objects.count(), 0)

    def test_other_entitlement_conflict_records_review_and_can_resolve_after_revoke(self):
        source = grant_entitlement(self.session.user, "activation", "external-conflict")
        result = apply_query(self.order.pk, snapshot(self.order))
        self.assertEqual(result["order"]["status"], "review")
        self.assertFalse(EntitlementSource.objects.filter(source_type="payment").exists())
        revoke_entitlement(source.pk, self.actor, "完成核查后撤销误发来源")
        result = apply_query(self.order.pk, snapshot(self.order))
        self.assertEqual(result["order"]["status"], "fulfilled")
        self.assertEqual(PaymentEvent.objects.get(kind="query").details["initialOutcome"], "review")

    def test_unprepared_order_and_revoked_payment_cannot_regrant(self):
        self.order.prepared_at = None
        self.order.save(_service=True)
        self.assertEqual(apply_callback(goods(self.order), self.order.app_id), "review")
        self.assertEqual(EntitlementSource.objects.count(), 0)

    def test_revoked_payment_source_stays_revoked_on_new_paid_snapshot(self):
        apply_query(self.order.pk, snapshot(self.order))
        source = EntitlementSource.objects.get()
        revoke_entitlement(source.pk, self.actor, "验证撤销来源不能被迟到付款恢复")
        result = apply_query(self.order.pk, snapshot(self.order, 4))
        self.assertFalse(result["entitlement"]["active"])
        self.assertEqual(EntitlementSource.objects.count(), 1)
        source.refresh_from_db()
        self.assertEqual(source.status, "revoked")
        self.assertEqual(AuditEvent.objects.filter(kind="granted").count(), 1)

    def test_invalid_identity_env_price_product_attach_or_query_paid_fee_do_not_write(self):
        cases = [goods(self.order, OpenId="different-user"), goods(self.order, Env=1)]
        for field, value in (("Quantity", 2), ("ActualPrice", 1), ("ProductId", "other-product"), ("Attach", "other-order")):
            payload = goods(self.order)
            payload["GoodsInfo"][field] = value
            cases.append(payload)
        for payload in cases:
            with self.assertRaises(BusinessError):
                apply_callback(payload, self.order.app_id)
        for changes in ({"order_id": "unknown"}, {"env_type": 2}, {"order_fee": 1}, {"paid_fee": 1}, {"biz_meta": "wrong"}):
            with self.assertRaises(BusinessError):
                apply_query(self.order.pk, snapshot(self.order, **changes))
        self.assertEqual(EntitlementSource.objects.count(), 0)
        self.assertEqual(PaymentEvent.objects.count(), 0)
        self.assertTrue(OpeningReservation.objects.get().is_active)


@override_settings(**PAYMENT_SETTINGS)
class PaymentHTTPTests(SyncFixture):
    webhook = "/integrations/wechat/virtual-payment/"

    def callback(self, data, **kwargs):
        query, body = envelope(data, **kwargs)
        return self.client.post(self.webhook + query, data=body, content_type="application/json")

    def test_real_json_and_xml_callback_commit_and_retry_success(self):
        self.assertEqual(self.callback(goods(self.order)).content, b"success")
        self.assertEqual(self.callback(goods(self.order), xml=True).content, b"success")
        self.assertEqual(EntitlementSource.objects.count(), 1)
        self.assertEqual(self.callback(refund(self.order)).content, b"success")
        self.assertEqual(self.callback(goods(self.order)).content, b"success")
        self.assertEqual(EntitlementSource.objects.get().status, "revoked")

    def test_false_signature_wrong_appid_invalid_plain_unknown_order_and_rollback_never_ack(self):
        query, body = envelope(goods(self.order))
        self.assertEqual(self.client.post(self.webhook + query + "x", body, content_type="application/json").status_code, 400)
        self.assertEqual(self.callback(goods(self.order), app_id="wrong-app").status_code, 400)
        self.assertEqual(self.client.post(self.webhook, goods(self.order), content_type="application/json").status_code, 400)
        self.assertEqual(self.callback(goods(self.order, OutTradeNo="a" * 32)).status_code, 404)
        with patch("payments.models.PaymentEvent.save", side_effect=BusinessError("STORAGE_UNAVAILABLE", "写入失败。", 503)):
            response = self.callback(goods(self.order))
            self.assertEqual(response.status_code, 503)
            self.assertNotEqual(response.content, b"success")
        self.assertEqual(EntitlementSource.objects.count(), 0)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "preparing")
        self.assertTrue(OpeningReservation.objects.get().is_active)

    def test_standard_and_encrypted_handshake_match_official_protocol(self):
        from payments.protocol import message_signature
        from .sync_fixtures import encrypt
        token = PAYMENT_SETTINGS["VIRTUAL_PAYMENT_CALLBACK_TOKEN"]
        signature = message_signature(token, "1", "2")
        response = self.client.get(self.webhook, {"timestamp": "1", "nonce": "2", "signature": signature, "echostr": "hello"})
        self.assertEqual(response.content, b"hello")
        encrypted = encrypt(b"echo-verified")
        response = self.client.get(self.webhook, {"timestamp": "1", "nonce": "2", "msg_signature": message_signature(token, "1", "2", encrypted), "echostr": encrypted})
        self.assertEqual(response.content, b"echo-verified")
        self.assertEqual(self.client.get(self.webhook, {"timestamp": "1", "nonce": "2", "signature": "0" * 40, "echostr": "hello"}).status_code, 400)

    def test_ios_refund_query_is_recorded_but_no_business_recommendation(self):
        response = self.callback({"MsgType": "event", "Event": "xpay_subscribe_ios_refund_query_notify", "CreateTime": 1714112445})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(PaymentEvent.objects.get().outcome, "uncertain")
        self.assertEqual(EntitlementSource.objects.count(), 0)

    def test_student_owner_only_query_idempotency_rate_and_cookie_separation(self):
        client = APIClient()
        url = f"/api/student/v1/orders/{self.order.pk}/query/"
        self.assertEqual(client.post(url, {}, format="json", HTTP_IDEMPOTENCY_KEY="query-request-1").status_code, 401)
        client.force_login(self.actor)
        self.assertEqual(client.post(url, {}, format="json", HTTP_IDEMPOTENCY_KEY="query-request-1").status_code, 401)
        client.credentials(HTTP_AUTHORIZATION="Bearer " + self.token)
        self.assertEqual(client.post(url, {"price": 1}, format="json", HTTP_IDEMPOTENCY_KEY="query-request-1").status_code, 400)
        self.assertEqual(client.post(url, {}, format="json").status_code, 400)
        with patch("payments.gateway.query_order", return_value=snapshot(self.order)) as query:
            first = client.post(url, {}, format="json", HTTP_IDEMPOTENCY_KEY="query-request-1")
            self.assertEqual(first.status_code, 200)
            self.assertEqual(first.data["order"]["status"], "fulfilled")
            self.assertIn("no-store", first["Cache-Control"])
            self.assertEqual(client.post(url, {}, format="json", HTTP_IDEMPOTENCY_KEY="query-request-1").data, first.data)
            self.assertEqual(query.call_count, 1)
            self.assertEqual(client.post("/api/student/v1/orders/" + "f" * 32 + "/query/", {}, format="json", HTTP_IDEMPOTENCY_KEY="query-request-2").status_code, 404)
        with patch("common.limits.time.time", return_value=100):
            for _ in range(10):
                self.assertEqual(client.post(url, {"invalid": "field"}, format="json", HTTP_IDEMPOTENCY_KEY="query-request-1").status_code, 400)
            self.assertEqual(client.post(url, {}, format="json", HTTP_IDEMPOTENCY_KEY="query-request-1").status_code, 429)
