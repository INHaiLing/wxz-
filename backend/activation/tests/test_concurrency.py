from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TransactionTestCase, skipUnlessDBFeature

from activation.models import ActivationCode, ActivationRedemption
from activation.services import generate_batch, redeem_code, transition_batch
from common.errors import BusinessError
from common.models import IdempotencyRecord
from entitlements.models import AuditEvent, EntitlementSource, OpeningReservation
from entitlements.services import reserve_opening, revoke_entitlement
from quality.connections import on_independent_connection


@skipUnlessDBFeature("has_select_for_update")
class ActivationConcurrencyTests(TransactionTestCase):
    def setUp(self):
        users = get_user_model()
        self.admin = users.objects.create_user(username="activation-race-manager", is_staff=True)
        self.admin.user_permissions.set(Permission.objects.filter(content_type__app_label__in=("activation", "entitlements")))
        self.user = users.objects.create_user(username="activation-race-user")
        self.other = users.objects.create_user(username="activation-race-other")
        self.batch, self.raw = generate_batch(self.admin, 2, "真实事务竞争", "race-generate-1")
        transition_batch(self.batch.pk, self.admin, "receive")
        transition_batch(self.batch.pk, self.admin, "enable")

    def run_pair(self, operations):
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(on_independent_connection, operation) for operation in operations]
            return [future.result(timeout=20) for future in futures]

    def redeem(self, user_id, code, key, barrier):
        user = get_user_model().objects.get(pk=user_id)
        barrier.wait(timeout=5)
        try:
            return redeem_code(user, code, key)
        except BusinessError as error:
            return str(error.detail["error"]["code"])

    def test_same_code_multiple_people_has_one_redemption_and_one_source(self):
        barrier = Barrier(2)
        results = self.run_pair([
            lambda: self.redeem(self.user.pk, self.raw[0], "race-redeem-1", barrier),
            lambda: self.redeem(self.other.pk, self.raw[0], "race-redeem-2", barrier),
        ])
        self.assertEqual(results.count("CODE_UNAVAILABLE"), 1)
        self.assertEqual(ActivationRedemption.objects.count(), 1)
        self.assertEqual(EntitlementSource.objects.count(), 1)
        self.assertEqual(ActivationCode.objects.filter(state="redeemed").count(), 1)
        self.assertEqual(AuditEvent.objects.filter(kind="activation_redeemed").count(), 1)

    def test_same_person_multiple_codes_consumes_only_one_code(self):
        barrier = Barrier(2)
        results = self.run_pair([
            lambda: self.redeem(self.user.pk, self.raw[0], "race-redeem-1", barrier),
            lambda: self.redeem(self.user.pk, self.raw[1], "race-redeem-2", barrier),
        ])
        self.assertEqual(results.count("ALREADY_ACTIVATED"), 1)
        self.assertEqual(ActivationCode.objects.filter(state="unused").count(), 1)
        self.assertEqual(ActivationRedemption.objects.count(), 1)
        self.assertEqual(IdempotencyRecord.objects.filter(operation="activation.redeem").count(), 1)

    def test_purchase_reservation_and_redemption_share_one_opening_mutex(self):
        barrier = Barrier(2)

        def reserve():
            user = get_user_model().objects.get(pk=self.user.pk)
            barrier.wait(timeout=5)
            try:
                return reserve_opening(user, "race-prepared-order").order_id
            except BusinessError as error:
                return str(error.detail["error"]["code"])

        reserved, redeemed = self.run_pair([reserve, lambda: self.redeem(self.user.pk, self.raw[0], "race-redeem-1", barrier)])
        if reserved == "race-prepared-order":
            self.assertEqual(redeemed, "PURCHASE_IN_PROGRESS")
            self.assertEqual(ActivationRedemption.objects.count(), 0)
            self.assertEqual(EntitlementSource.objects.count(), 0)
            self.assertEqual(ActivationCode.objects.filter(state="unused").count(), 2)
            self.assertEqual(OpeningReservation.objects.filter(released_at__isnull=True).count(), 1)
        else:
            self.assertEqual(reserved, "ALREADY_ACTIVATED")
            self.assertTrue(redeemed["entitlement"]["active"])
            self.assertEqual(ActivationRedemption.objects.count(), 1)
            self.assertEqual(OpeningReservation.objects.filter(released_at__isnull=True).count(), 0)

    def test_revoke_then_replay_on_independent_connection_does_not_regrant(self):
        success = redeem_code(self.user, self.raw[0], "race-redeem-1")
        redemption = ActivationRedemption.objects.get()
        committed = Event()

        def revoke():
            actor = get_user_model().objects.get(pk=self.admin.pk)
            source = revoke_entitlement(redemption.source_id, actor, "误发撤权")
            committed.set()
            return source.status

        def replay():
            user = get_user_model().objects.get(pk=self.user.pk)
            self.assertTrue(committed.wait(timeout=5))
            return redeem_code(user, self.raw[0], "race-redeem-1")

        revoked, replayed = self.run_pair([revoke, replay])
        self.assertEqual(revoked, "revoked")
        self.assertEqual(replayed["redemptionId"], success["redemptionId"])
        self.assertFalse(replayed["entitlement"]["active"])
        self.assertEqual(EntitlementSource.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.filter(kind="granted").count(), 1)
        self.assertEqual(ActivationCode.objects.filter(state="redeemed").count(), 1)

    def test_same_admin_retry_delivers_raw_codes_only_once(self):
        barrier = Barrier(2)

        def generate():
            actor = get_user_model().objects.get(pk=self.admin.pk)
            barrier.wait(timeout=5)
            batch, raw = generate_batch(actor, 3, "管理员重复提交", "race-admin-generation")
            return str(batch.pk), raw is not None

        first, second = self.run_pair([generate, generate])
        self.assertEqual(first[0], second[0])
        self.assertEqual(sum((first[1], second[1])), 1)
        self.assertEqual(ActivationCode.objects.count(), 5)
        self.assertEqual(AuditEvent.objects.filter(kind="activation_generated").count(), 2)

    def test_batch_disable_and_redemption_do_not_deadlock_or_reverse_completed_rights(self):
        barrier = Barrier(2)

        def disable():
            actor = get_user_model().objects.get(pk=self.admin.pk)
            barrier.wait(timeout=5)
            return transition_batch(self.batch.pk, actor, "disable", reason="暂停使用").state

        disabled, redeemed = self.run_pair([disable, lambda: self.redeem(self.user.pk, self.raw[0], "race-redeem-1", barrier)])
        self.assertEqual(disabled, "disabled")
        if redeemed == "CODE_UNAVAILABLE":
            self.assertEqual(ActivationRedemption.objects.count(), 0)
            self.assertEqual(EntitlementSource.objects.count(), 0)
        else:
            self.assertTrue(redeemed["entitlement"]["active"])
            self.assertEqual(ActivationRedemption.objects.count(), 1)
            self.assertEqual(EntitlementSource.objects.filter(status="active").count(), 1)
