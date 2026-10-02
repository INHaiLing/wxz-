from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.contrib.auth.models import Permission
from django.test import TransactionTestCase, override_settings, skipUnlessDBFeature

from accounts.models import StudentSession
from activation.models import ActivationCode
from activation.services import generate_batch, redeem_code, transition_batch
from common.errors import BusinessError
from entitlements.models import EntitlementSource, OpeningReservation
from payments.models import Order, PaymentTask
from payments.services import create_order, prepare_payment
from payments.tests.test_preparation import PAYMENT_SETTINGS, fixtures
from quality.connections import on_independent_connection


@skipUnlessDBFeature('has_select_for_update')
@override_settings(**PAYMENT_SETTINGS)
class PaymentPreparationConcurrencyTests(TransactionTestCase):
    def setUp(self):
        _,self.session,self.actor,self.product=fixtures()

    def compete(self,operations):
        barrier=Barrier(2)
        def perform(operation):
            session=StudentSession.objects.select_related('user').get(pk=self.session.pk)
            barrier.wait(timeout=5)
            try: return operation(session)
            except BusinessError as error: return str(error.detail['error']['code'])
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(on_independent_connection,lambda op=operation:perform(op)) for operation in operations]
            return [future.result(timeout=20) for future in futures]

    def create(self,key):
        return create_order(self.session.user,self.session,self.product.pk,'android',key)['order']['id']

    def test_same_create_key_has_one_order_and_no_reservation(self):
        results=self.compete([lambda session:create_order(session.user,session,self.product.pk,'android','same-create-key') for _ in range(2)])
        self.assertEqual(results[0]['order']['id'],results[1]['order']['id'])
        self.assertEqual(Order.objects.count(),1)
        self.assertFalse(OpeningReservation.objects.exists())

    def test_different_orders_cannot_export_two_payment_packets(self):
        first,second=self.create('first-create-key'),self.create('second-create-key')
        results=self.compete([lambda session:prepare_payment(session.user,session,first,'first-prepare-key'),
                              lambda session:prepare_payment(session.user,session,second,'second-prepare-key')])
        self.assertEqual(results.count('PURCHASE_IN_PROGRESS'),1)
        self.assertEqual(Order.objects.filter(status='preparing').count(),1)
        self.assertEqual(PaymentTask.objects.count(),1)
        self.assertEqual(OpeningReservation.objects.filter(released_at__isnull=True).count(),1)

    def test_same_prepare_key_replays_one_packet_and_task(self):
        first=self.create('first-create-key')
        results=self.compete([lambda session:prepare_payment(session.user,session,first,'same-prepare-key') for _ in range(2)])
        self.assertEqual(results[0]['payment'],results[1]['payment'])
        self.assertEqual(PaymentTask.objects.count(),1)
        self.assertEqual(OpeningReservation.objects.count(),1)

    def test_actual_activation_competes_with_packet_preparation(self):
        self.actor.user_permissions.set(Permission.objects.filter(content_type__app_label__in=('activation','entitlements')))
        self.actor=type(self.actor).objects.get(pk=self.actor.pk) # discard prior has_perm cache
        batch,codes=generate_batch(self.actor,1,'支付与兑换真实竞争','generate-race-key')
        transition_batch(batch.pk,self.actor,'receive'); transition_batch(batch.pk,self.actor,'enable')
        first=self.create('first-create-key')
        results=self.compete([lambda session:prepare_payment(session.user,session,first,'first-prepare-key'),
                              lambda session:redeem_code(session.user,codes[0],'redeem-race-key')])
        errors=[result for result in results if isinstance(result,str)]
        self.assertEqual(len(errors),1)
        self.assertIn(errors[0],('ALREADY_ACTIVATED','PURCHASE_IN_PROGRESS'))
        granted=EntitlementSource.objects.filter(status='active').count()
        reserved=OpeningReservation.objects.filter(released_at__isnull=True).count()
        self.assertEqual(granted+reserved,1)
        self.assertEqual(ActivationCode.objects.filter(state='redeemed').count(),granted)
        self.assertEqual(PaymentTask.objects.count(),reserved)
