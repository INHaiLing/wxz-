from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event, Lock, local
from unittest.mock import patch

from django.test import Client, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone

from accounts.crypto import decrypt_session_key
from entitlements.models import EntitlementSource, OpeningReservation
from payments import gateway
from payments.models import AccessTokenCache, Order, PaymentEvent, PaymentTask
from payments.refund_queries import KIND
from payments.services import create_order, prepare_payment
from payments.synchronization import apply_query
from payments.tasks import MAX_ATTEMPTS, claim_task
from quality.connections import on_independent_connection
from .sync_fixtures import envelope, snapshot
from .test_preparation import PAYMENT_SETTINGS, fixtures
from .test_resilience import cache_token, inquiry


@override_settings(**{**PAYMENT_SETTINGS, "VIRTUAL_PAYMENT_IOS_ENABLED": True})
@skipUnlessDBFeature("has_select_for_update")
class PaymentResilienceConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.token, self.session, self.actor, self.product = fixtures()
        order_id = create_order(self.session.user, self.session, self.product.pk, "ios", "resilience-race-order")["order"]["id"]
        prepare_payment(self.session.user, self.session, order_id, "resilience-race-prepare")
        self.order = Order.objects.select_related("identity").get(pk=order_id)

    def pair(self, operations):
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(on_independent_connection, operation) for operation in operations]
            return [future.result(timeout=20) for future in futures]

    def test_late_old_request_cannot_erase_new_token_from_an_independent_connection(self):
        cache_token()
        old_calls = Barrier(2)
        new_visible = Event()
        worker = local()
        mutex = Lock()
        requests = []

        def platform(path, body, query=None, **kwargs):
            with mutex:
                requests.append((path, body, dict(query or {})))
            if path == "/cgi-bin/stable_token":
                return {"access_token": "new-disposable-token", "expires_in": 7200}
            if query["access_token"] == "old-disposable-token":
                old_calls.wait(timeout=5)
                if worker.name == "late":
                    self.assertTrue(new_visible.wait(timeout=5))
                raise gateway.PlatformRequestFailure(40014)
            self.assertEqual(query["access_token"], "new-disposable-token")
            if worker.name == "fresh":
                new_visible.set()  # access_token's transaction has already committed.
            return {"errcode": 0, "order": snapshot(self.order)}

        def query(name):
            worker.name = name
            return gateway.query_order(self.order)

        with patch("payments.gateway.official_post", side_effect=platform):
            results = self.pair([lambda: query("fresh"), lambda: query("late")])
        self.assertTrue(all(result["status"] == 2 for result in results))
        self.assertEqual(sum(path == "/cgi-bin/stable_token" for path, _, _ in requests), 1)
        self.assertEqual(sum(path == "/xpay/query_order" for path, _, _ in requests), 4)
        self.assertEqual(decrypt_session_key(AccessTokenCache.objects.get().ciphertext), "new-disposable-token")

    def test_first_cache_insert_and_refresh_are_serialized_on_real_connections(self):
        first_insert = Barrier(2)
        original_save = AccessTokenCache.save

        def competing_insert(cache, *args, **kwargs):
            if not cache.ciphertext:
                first_insert.wait(timeout=5)
            return original_save(cache, *args, **kwargs)

        with patch.object(AccessTokenCache, "save", new=competing_insert), \
                patch("payments.gateway.official_post", return_value={"access_token": "first-disposable-token", "expires_in": 30}) as post:
            result = self.pair([lambda: gateway.access_token(self.order.app_id),
                                lambda: gateway.access_token(self.order.app_id)])
        self.assertEqual(result, ["first-disposable-token", "first-disposable-token"])
        self.assertEqual(post.call_count, 1)
        self.assertEqual(AccessTokenCache.objects.count(), 1)

    def test_first_same_apple_http_inquiry_race_is_one_audit_and_both_503(self):
        first_save = Barrier(2)
        original_save = PaymentEvent.save
        query, body = envelope(inquiry(self.order))

        def competing_insert(event, *args, **kwargs):
            if event.kind == KIND:
                first_save.wait(timeout=5)
            return original_save(event, *args, **kwargs)

        def callback():
            response = Client().post("/integrations/wechat/virtual-payment/" + query,
                                     body, content_type="application/json")
            return response.status_code, Order.objects.count()

        with patch.object(PaymentEvent, "save", new=competing_insert):
            self.assertEqual(self.pair([callback, callback]), [(503, 1), (503, 1)])
        event = PaymentEvent.objects.get(kind=KIND)
        self.assertEqual(event.order_id, self.order.pk)
        self.assertEqual(event.details["association"], "linked")
        self.assertEqual(Order.objects.get().status, "preparing")
        self.assertTrue(OpeningReservation.objects.get().is_active)
        self.assertFalse(EntitlementSource.objects.exists())

    def test_two_workers_skip_exhausted_head_and_claim_following_task_once(self):
        apply_query(self.order.pk, snapshot(self.order, 2))
        exhausted = PaymentTask.objects.get(kind="query")
        exhausted.status = "running"
        exhausted.attempts = MAX_ATTEMPTS
        exhausted.lease_token = "expired-lease"
        exhausted.lease_until = timezone.now() - timedelta(seconds=2)
        exhausted.next_run_at = timezone.now() - timedelta(seconds=3)
        exhausted.save(_service=True)
        start = Barrier(2)

        def claim():
            start.wait(timeout=5)
            return claim_task()

        results = self.pair([claim, claim])
        claimed = [item for item in results if item is not None]
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0][0], PaymentTask.objects.get(kind="deliver").pk)
        exhausted.refresh_from_db()
        self.assertEqual(exhausted.status, "failed")
        self.assertEqual(exhausted.last_error_code, "RETRIES_EXHAUSTED")
