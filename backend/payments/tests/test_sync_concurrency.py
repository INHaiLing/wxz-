import base64
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.test import TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone
from datetime import timedelta

from accounts.services import issue_student_session
from accounts.wechat import WeChatLogin
from entitlements.models import AuditEvent, EntitlementSource
from payments.models import Order, PaymentEvent, PaymentTask
from payments.services import create_order, prepare_payment
from payments.synchronization import apply_callback, apply_query, query_payment
from payments.tasks import claim_task, finish_task
from quality.connections import on_independent_connection
from .sync_fixtures import goods, refund, snapshot
from .test_preparation import PAYMENT_SETTINGS, fixtures


@override_settings(**PAYMENT_SETTINGS)
@skipUnlessDBFeature("has_select_for_update")
class PaymentSynchronizationConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.token, self.session, self.actor, self.product = fixtures()
        order_id = create_order(self.session.user, self.session, self.product.pk, "android", "race-order-1")["order"]["id"]
        prepare_payment(self.session.user, self.session, order_id, "race-prepare-1")
        self.order = Order.objects.select_related("identity").get(pk=order_id)

    def pair(self, operations):
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(on_independent_connection, operation) for operation in operations]
            return [future.result(timeout=20) for future in futures]

    def test_callback_and_query_race_grant_once(self):
        barrier = Barrier(2)

        def callback():
            barrier.wait(timeout=5)
            return apply_callback(goods(self.order), self.order.app_id)

        def query():
            barrier.wait(timeout=5)
            return apply_query(self.order.pk, snapshot(self.order))

        self.pair([callback, query])
        self.assertEqual(EntitlementSource.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.filter(kind="granted").count(), 1)
        self.assertEqual(PaymentEvent.objects.count(), 2)
        self.assertEqual(Order.objects.get().status, "fulfilled")

    def test_same_callback_concurrent_retry_has_one_event_and_source(self):
        barrier = Barrier(2)

        def callback():
            barrier.wait(timeout=5)
            return apply_callback(goods(self.order), self.order.app_id)

        self.assertEqual(self.pair([callback, callback]), ["applied", "applied"])
        self.assertEqual(PaymentEvent.objects.count(), 1)
        self.assertEqual(EntitlementSource.objects.count(), 1)

    def test_paid_refund_race_finishes_refunded_without_active_payment(self):
        barrier = Barrier(2)

        def synchronize(payload):
            barrier.wait(timeout=5)
            return apply_callback(payload, self.order.app_id)

        self.pair([lambda: synchronize(goods(self.order)), lambda: synchronize(refund(self.order))])
        self.assertEqual(Order.objects.get().status, "refunded")
        self.assertEqual(EntitlementSource.objects.filter(status="active").count(), 0)
        self.assertLessEqual(EntitlementSource.objects.count(), 1)

    def test_two_query_replays_share_one_event_source_and_idempotency_record(self):
        barrier = Barrier(2)

        def platform(order):
            barrier.wait(timeout=5)
            return snapshot(order)

        with patch("payments.gateway.query_order", side_effect=platform):
            results = self.pair([lambda: query_payment(self.session.user, self.order.pk, "query-same-key"), lambda: query_payment(self.session.user, self.order.pk, "query-same-key")])
        self.assertTrue(all(item["entitlement"]["active"] for item in results))
        self.assertEqual(EntitlementSource.objects.count(), 1)
        self.assertEqual(PaymentEvent.objects.count(), 1)
        from common.models import IdempotencyRecord
        self.assertEqual(IdempotencyRecord.objects.filter(operation="payment-query").count(), 1)

    def test_cross_user_platform_id_unique_race_persists_review_without_dirty_ids(self):
        _, session = issue_student_session(WeChatLogin(self.order.app_id, "race-other-payer", base64.b64encode(b"second-device-key").decode()))
        other_id = create_order(session.user, session, self.product.pk, "android", "race-other-order")["order"]["id"]
        prepare_payment(session.user, session, other_id, "race-other-prepare")
        other = Order.objects.select_related("identity").get(pk=other_id)
        barrier = Barrier(2)
        original_save = Order.save
        first_attempt = set()

        def competing_insert(order, *args, **kwargs):
            if order.platform_order_id == "global-platform-id" and order.status == "preparing" and order.pk not in first_attempt:
                first_attempt.add(order.pk)
                barrier.wait(timeout=5)
            return original_save(order, *args, **kwargs)

        with patch.object(Order, "save", new=competing_insert):
            results = self.pair([lambda: apply_query(self.order.pk, snapshot(self.order, wx_order_id="global-platform-id", wxpay_order_id="")), lambda: apply_query(other.pk, snapshot(other, wx_order_id="global-platform-id", wxpay_order_id=""))])
        self.assertEqual(sorted(item["order"]["status"] for item in results), ["fulfilled", "review"])
        self.assertEqual(Order.objects.filter(platform_order_id="global-platform-id").count(), 1)
        self.assertEqual(Order.objects.get(status="review").platform_order_id, "")
        self.assertEqual(EntitlementSource.objects.count(), 1)

    def test_two_workers_claim_one_task_and_expired_lease_takeover_is_exclusive(self):
        barrier = Barrier(2)

        def claim():
            barrier.wait(timeout=5)
            return claim_task()

        results = self.pair([claim, claim])
        self.assertEqual(sum(item is not None for item in results), 1)
        old_id, old_lease = next(item for item in results if item is not None)
        task = PaymentTask.objects.get(pk=old_id)
        task.lease_until = timezone.now() - timedelta(seconds=1)
        task.save(_service=True)
        barrier = Barrier(2)
        results = self.pair([claim, claim])
        self.assertEqual(sum(item is not None for item in results), 1)
        new_id, new_lease = next(item for item in results if item is not None)
        self.assertEqual(new_id, old_id)
        self.assertNotEqual(old_lease, new_lease)
        self.assertFalse(finish_task(old_id, old_lease))
        self.assertTrue(finish_task(new_id, new_lease))

    def test_query_close_and_paid_race_never_closes_fulfilled_order(self):
        barrier = Barrier(2)

        def synchronize(status):
            barrier.wait(timeout=5)
            return apply_query(self.order.pk, snapshot(self.order, status))

        self.pair([lambda: synchronize(6), lambda: synchronize(2)])
        order = Order.objects.get()
        if order.status == "fulfilled":
            self.assertEqual(EntitlementSource.objects.filter(status="active").count(), 1)
        else:
            self.assertEqual(order.status, "closed")
            self.assertEqual(order.review_reason, "LATE_PAID_AFTER_CLOSED")
            self.assertEqual(EntitlementSource.objects.count(), 0)
