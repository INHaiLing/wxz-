from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase

from activation.models import ActivationBatch, ActivationCode, ActivationRedemption
from activation.services import generate_batch, redeem_code, transition_batch
from common.errors import BusinessError
from common.models import IdempotencyRecord
from entitlements.models import AuditEvent, EntitlementSource
from entitlements.services import grant_entitlement, reserve_opening, revoke_entitlement


class ActivationServiceTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.admin = users.objects.create_user(username="activation-manager", is_staff=True)
        self.admin.user_permissions.set(Permission.objects.filter(content_type__app_label__in=("activation", "entitlements")))
        self.user = users.objects.create_user(username="activation-student")
        self.other = users.objects.create_user(username="activation-other")

    def enabled_codes(self, quantity=2, key="generate-key-1"):
        batch, codes = generate_batch(self.admin, quantity, "测试批次", key)
        transition_batch(batch.pk, self.admin, "receive")
        transition_batch(batch.pk, self.admin, "enable")
        return batch, codes

    def assert_error(self, code, operation):
        with self.assertRaises(BusinessError) as error:
            operation()
        self.assertEqual(str(error.exception.detail["error"]["code"]), code)

    def test_generation_is_one_time_and_confirmation_required(self):
        batch, codes = generate_batch(self.admin, 2, "一次领取", "generate-key-1")
        self.assertEqual(len(codes), 2)
        again, repeated = generate_batch(self.admin, 2, "一次领取", "generate-key-1")
        self.assertEqual(again.pk, batch.pk)
        self.assertIsNone(repeated)
        self.assert_error("CODE_UNAVAILABLE", lambda: redeem_code(self.user, codes[0], "redeem-key-1"))
        transition_batch(batch.pk, self.admin, "receive")
        transition_batch(batch.pk, self.admin, "enable")
        result = redeem_code(self.user, codes[0], "redeem-key-1")
        self.assertTrue(result["entitlement"]["active"])
        self.assertEqual(ActivationRedemption.objects.count(), 1)

    def test_active_and_reserved_accounts_do_not_consume_new_code(self):
        _, codes = self.enabled_codes()
        grant_entitlement(self.user, "payment", "existing-order")
        self.assert_error("ALREADY_ACTIVATED", lambda: redeem_code(self.user, codes[0], "redeem-key-1"))
        reserve_opening(self.other, "preparing-order")
        self.assert_error("PURCHASE_IN_PROGRESS", lambda: redeem_code(self.other, codes[1], "redeem-key-2"))
        self.assertEqual(ActivationCode.objects.filter(state="unused").count(), 2)
        self.assertEqual(ActivationRedemption.objects.count(), 0)

    def test_success_replay_after_revoke_does_not_restore_or_consume_new_code(self):
        _, codes = self.enabled_codes()
        first = redeem_code(self.user, codes[0], "redeem-key-1")
        redemption = ActivationRedemption.objects.get()
        revoke_entitlement(redemption.source_id, self.admin, "误发")
        replay = redeem_code(self.user, codes[0], "redeem-key-1")
        self.assertEqual(replay["redemptionId"], first["redemptionId"])
        self.assertFalse(replay["entitlement"]["active"])
        self.assertEqual(EntitlementSource.objects.count(), 1)
        self.assertEqual(ActivationCode.objects.filter(state="redeemed").count(), 1)
        self.assert_error("IDEMPOTENCY_CONFLICT", lambda: redeem_code(self.user, codes[1], "redeem-key-1"))
        self.assertTrue(redeem_code(self.user, codes[1], "redeem-key-2")["entitlement"]["active"])
