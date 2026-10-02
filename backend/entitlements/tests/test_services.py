from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import transaction
from django.test import TestCase

from common.errors import BusinessError
from entitlements.models import AuditEvent, EntitlementSource, OpeningReservation, Product
from entitlements.services import (
    SCOPE, check_new_opening, entitlement_snapshot, grant_entitlement,
    has_active_entitlement, lock_user, mark_product_synced, release_opening,
    reserve_opening, revoke_entitlement, revocation_preview,
)


class EntitlementServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="rights-user")
        self.other = get_user_model().objects.create_user(username="rights-other")
        self.admin = get_user_model().objects.create_user(username="rights-admin", is_staff=True)
        self.admin.user_permissions.set(Permission.objects.filter(
            content_type__app_label="entitlements",
            codename__in=("revoke_entitlementsource", "grant_entitlementsource", "sync_product"),
        ))

    def assert_business_error(self, code, operation):
        with self.assertRaises(BusinessError) as error:
            operation()
        self.assertEqual(str(error.exception.detail["error"]["code"]), code)

    def test_seed_product_is_non_destructive_and_changed_price_invalidates_sync(self):
        call_command("seed_product", verbosity=0)
        product = Product.objects.get(scope=SCOPE)
        self.assertEqual(product.price_fen, 1000)
        product.platform_product_id = "bank-001"
        product.save()
        mark_product_synced(product.pk, self.admin, price_fen=1000, platform_product_id="bank-001")
        product.refresh_from_db()
        self.assertEqual(product.platform_sync_state, "synced")
        product.price_fen = 2000
        product.save()
        call_command("seed_product", verbosity=0)
        product.refresh_from_db()
        self.assertEqual(product.price_fen, 2000)
        self.assertEqual(product.platform_sync_state, "pending")

    def test_permanent_source_replay_and_revoked_replay_never_reactivate(self):
        source = grant_entitlement(self.user, "activation", "code-1")
        self.assertTrue(has_active_entitlement(self.user))
        self.assertIsNone(source.expires_at)
        self.assertEqual(grant_entitlement(self.user, "activation", "code-1").pk, source.pk)
        revoke_entitlement(source.pk, self.admin, "误发激活码")
        replay = grant_entitlement(self.user, "activation", "code-1")
        self.assertEqual(replay.status, "revoked")
        self.assertFalse(has_active_entitlement(self.user))
        self.assertEqual(EntitlementSource.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.filter(kind="granted").count(), 1)
        self.assertEqual(AuditEvent.objects.filter(kind="revoked").count(), 1)

    def test_source_cannot_cross_user_and_multiple_sources_survive_target_revoke(self):
        source = grant_entitlement(self.user, "activation", "code-1")
        grant_entitlement(self.user, "payment", "order-1")
        self.assert_business_error("SOURCE_CONFLICT", lambda: grant_entitlement(self.other, "activation", "code-1"))
        preview = revocation_preview(source)
        self.assertTrue(preview["willRemainActive"])
        self.assertEqual(preview["remainingSourceCount"], 1)
        revoke_entitlement(source.pk, self.admin, "误发", expected_fingerprint=preview["fingerprint"])
        self.assertTrue(has_active_entitlement(self.user))

    def test_opening_blocking_and_only_verified_final_unpaid_release(self):
        reservation = reserve_opening(self.user, "order-1")
        self.assertEqual(reserve_opening(self.user, "order-1").pk, reservation.pk)
        self.assert_business_error("PURCHASE_IN_PROGRESS", lambda: check_new_opening(self.user))
        self.assert_business_error("PURCHASE_IN_PROGRESS", lambda: reserve_opening(self.user, "order-2"))
        self.assert_business_error("UNVERIFIED_RELEASE", lambda: release_opening(self.user, "order-1"))
        self.assert_business_error("RESERVATION_CONFLICT", lambda: release_opening(self.user, "order-2", verified_final_unpaid=True))
        release_opening(self.user, "order-1", verified_final_unpaid=True)
        release_opening(self.user, "order-1", verified_final_unpaid=True)
        check_new_opening(self.user)
        reserve_opening(self.user, "order-2")
        self.assertEqual(OpeningReservation.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.filter(kind="opening_released").count(), 1)

    def test_paid_grant_consumes_matching_reservation_and_active_blocks_new_opening(self):
        reserve_opening(self.user, "order-1")
        source = grant_entitlement(self.user, "payment", "order-1")
        reservation = OpeningReservation.objects.get(user=self.user)
        self.assertIsNotNone(reservation.released_at)
        self.assertEqual(reservation.release_reason, "fulfilled")
        self.assert_business_error("ALREADY_ACTIVATED", lambda: check_new_opening(self.user))
        self.assert_business_error("ALREADY_ACTIVATED", lambda: reserve_opening(self.user, "order-2"))
        revoke_entitlement(source.pk, self.admin, "退款来源撤销")
        check_new_opening(self.user)

    def test_revoke_requires_permission_reason_and_current_impact(self):
        source = grant_entitlement(self.user, "activation", "code-1")
        self.assert_business_error("FORBIDDEN", lambda: revoke_entitlement(source.pk, self.user, "误发"))
        self.assert_business_error("REASON_REQUIRED", lambda: revoke_entitlement(source.pk, self.admin, " "))
        old_preview = revocation_preview(source)
        grant_entitlement(self.user, "payment", "order-2")
        self.assert_business_error("VERSION_CONFLICT", lambda: revoke_entitlement(source.pk, self.admin, "误发", expected_fingerprint=old_preview["fingerprint"]))
        source.refresh_from_db()
        self.assertEqual(source.status, "active")
        revoke_entitlement(source.pk, self.admin, "误发")
        revoke_entitlement(source.pk, self.admin, "误发")
        self.assertEqual(AuditEvent.objects.filter(kind="revoked").count(), 1)

    def test_invalid_source_and_permanent_scope_are_rejected(self):
        for source_type, source_id, scope in (("manual", "fake", SCOPE), ("payment", "", SCOPE), ("activation", "code", "other")):
            with self.subTest(source_type=source_type, scope=scope):
                with self.assertRaises(BusinessError):
                    grant_entitlement(self.user, source_type, source_id, scope=scope)
        self.assertEqual(EntitlementSource.objects.count(), 0)
        self.assertEqual(entitlement_snapshot(self.user), {"scope": SCOPE, "active": False, "permanent": False, "expiresAt": None})

    def test_business_sources_and_audit_cannot_be_directly_edited_deleted_or_created(self):
        source = grant_entitlement(self.user, "activation", "code-1")
        source.status = "revoked"
        for operation in (
            source.save, source.delete,
            lambda: EntitlementSource.objects.filter(pk=source.pk).update(status="revoked"),
            lambda: EntitlementSource.objects.create(user=self.user, source_type="payment", source_id="fake"),
            lambda: AuditEvent.objects.all().update(details={}),
            lambda: AuditEvent.objects.all().delete(),
        ):
            with self.subTest(operation=operation):
                with self.assertRaises(ValidationError):
                    operation()
