from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TransactionTestCase, skipUnlessDBFeature

from common.errors import BusinessError
from entitlements.models import AuditEvent, EntitlementSource, OpeningReservation
from entitlements.services import check_new_opening, grant_entitlement, reserve_opening
from quality.connections import on_independent_connection


@skipUnlessDBFeature("has_select_for_update")
class EntitlementConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="permanent-race")
        self.other = get_user_model().objects.create_user(username="permanent-race-other")

    def run_pair(self, operations):
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(on_independent_connection, operation) for operation in operations]
            return [future.result(timeout=20) for future in futures]

    def test_two_new_activations_have_one_success_and_one_already_activated(self):
        barrier = Barrier(2)

        def activate(index):
            user = get_user_model().objects.get(pk=self.user.pk)
            barrier.wait(timeout=5)
            try:
                with transaction.atomic():
                    check_new_opening(user)
                    return str(grant_entitlement(user, "activation", f"race-code-{index}").pk)
            except BusinessError as error:
                return str(error.detail["error"]["code"])

        results = self.run_pair([lambda: activate(0), lambda: activate(1)])
        self.assertEqual(results.count("ALREADY_ACTIVATED"), 1)
        self.assertEqual(EntitlementSource.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.filter(kind="granted").count(), 1)

    def test_two_payment_preparations_keep_only_one_order_reservation(self):
        barrier = Barrier(2)

        def prepare(index):
            user = get_user_model().objects.get(pk=self.user.pk)
            barrier.wait(timeout=5)
            try:
                return reserve_opening(user, f"race-order-{index}").order_id
            except BusinessError as error:
                return str(error.detail["error"]["code"])

        results = self.run_pair([lambda: prepare(0), lambda: prepare(1)])
        self.assertEqual(results.count("PURCHASE_IN_PROGRESS"), 1)
        self.assertEqual(OpeningReservation.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.filter(kind="opening_reserved").count(), 1)

    def test_same_global_source_for_different_users_has_one_owner(self):
        barrier = Barrier(2)
        original_save = EntitlementSource.save

        def competing_insert(instance, *args, **kwargs):
            barrier.wait(timeout=5)
            return original_save(instance, *args, **kwargs)

        def grant(user_id):
            user = get_user_model().objects.get(pk=user_id)
            try:
                return str(grant_entitlement(user, "activation", "same-global-code").user_id)
            except BusinessError as error:
                return str(error.detail["error"]["code"])

        with patch.object(EntitlementSource, "save", new=competing_insert):
            results = self.run_pair([lambda: grant(self.user.pk), lambda: grant(self.other.pk)])
        self.assertEqual(results.count("SOURCE_CONFLICT"), 1)
        self.assertEqual(EntitlementSource.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.filter(kind="granted").count(), 1)

    def test_same_source_concurrent_replay_has_one_source_and_audit(self):
        barrier = Barrier(2)

        def grant():
            user = get_user_model().objects.get(pk=self.user.pk)
            barrier.wait(timeout=5)
            return grant_entitlement(user, "payment", "repeat-order").pk

        results = self.run_pair([grant, grant])
        self.assertEqual(results[0], results[1])
        self.assertEqual(EntitlementSource.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.filter(kind="granted").count(), 1)
