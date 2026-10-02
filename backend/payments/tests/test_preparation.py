import base64
import hashlib
import hmac
import json
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.services import issue_student_session, revoke_session
from accounts.wechat import WeChatLogin
from common.errors import BusinessError
from common.models import IdempotencyRecord
from entitlements.models import Product, OpeningReservation
from entitlements.services import grant_entitlement, mark_product_synced
from payments.models import Order, PaymentTask
from payments.services import create_order, prepare_payment

PAYMENT_SETTINGS = {
    'STUDENT_SESSION_ENCRYPTION_KEYS':(Fernet.generate_key().decode(),),
    'WECHAT_APP_ID':'wx-payment-test','VIRTUAL_PAYMENT_ENABLED':True,
    'VIRTUAL_PAYMENT_ANDROID_ENABLED':True,'VIRTUAL_PAYMENT_IOS_ENABLED':False,
    'VIRTUAL_PAYMENT_ENV':0,'VIRTUAL_PAYMENT_OFFER_ID':'123',
    'VIRTUAL_PAYMENT_APP_KEY':'private-test-appkey','VIRTUAL_PAYMENT_SANDBOX_APP_KEY':'private-test-sandbox',
    'WECHAT_APP_SECRET':'disposable-test-app-secret','VIRTUAL_PAYMENT_CALLBACK_TOKEN':'disposable-token',
    'VIRTUAL_PAYMENT_CALLBACK_AES_KEY':base64.b64encode(b'x'*32).decode().rstrip('='),
}


def fixtures():
    token,session=issue_student_session(WeChatLogin('wx-payment-test','payer',base64.b64encode(b'device-one-key').decode()))
    actor=get_user_model().objects.create_user(username='payment-manager',is_staff=True)
    actor.user_permissions.add(Permission.objects.get(content_type__app_label='entitlements',codename='sync_product'))
    product=Product.objects.create(platform_product_id='permanent-bank')
    mark_product_synced(product.pk,actor,price_fen=1000,platform_product_id='permanent-bank')
    product.refresh_from_db()
    return token,session,actor,product


@override_settings(**PAYMENT_SETTINGS)
class PaymentPreparationTests(TestCase):
    def setUp(self):
        self.token,self.session,self.actor,self.product=fixtures()
        self.client=APIClient(); self.client.credentials(HTTP_AUTHORIZATION='Bearer '+self.token)

    def new_order(self,key='order-key-123'):
        return create_order(self.session.user,self.session,self.product.pk,'android',key)['order']['id']

    def test_create_snapshot_replay_does_not_reserve_or_duplicate(self):
        first=self.new_order()
        self.assertEqual(self.new_order(),first)
        self.assertEqual(Order.objects.count(),1)
        self.assertFalse(OpeningReservation.objects.exists())
        self.product.price_fen=2500; self.product.save()
        self.assertEqual(Order.objects.get(pk=first).price_fen,1000)
        self.assertEqual(self.new_order(),first)

    def test_exact_signatures_current_device_and_safe_idempotency_storage(self):
        first=self.new_order()
        # Latest identity is device-two; current request still comes from device-one.
        issue_student_session(WeChatLogin('wx-payment-test','payer',base64.b64encode(b'device-two-key').decode()))
        result=prepare_payment(self.session.user,self.session,first,'payment-key-123')
        packet=result['payment']; body=packet['signData']
        self.assertIsInstance(body,str)
        self.assertEqual(json.loads(body)['goodsPrice'],1000)
        self.assertEqual(packet['paySig'],hmac.new(b'private-test-appkey',('requestVirtualPayment&'+body).encode(),hashlib.sha256).hexdigest())
        self.assertEqual(packet['signature'],hmac.new(base64.b64encode(b'device-one-key'),body.encode(),hashlib.sha256).hexdigest())
        self.assertEqual(prepare_payment(self.session.user,self.session,first,'payment-key-123')['payment'],packet)
        self.assertEqual(PaymentTask.objects.count(),1)
        self.assertTrue(OpeningReservation.objects.get(user=self.session.user).is_active)
        self.assertNotIn(packet['signature'],repr(list(IdempotencyRecord.objects.values('response'))))

    def test_new_key_does_not_reissue_prepared_order_or_cancel_reservation(self):
        first=self.new_order(); prepare_payment(self.session.user,self.session,first,'payment-key-123')
        with self.assertRaises(BusinessError) as error:
            prepare_payment(self.session.user,self.session,first,'different-payment-key')
        self.assertEqual(str(error.exception.detail['error']['code']),'PAYMENT_ALREADY_PREPARED')
        self.assertTrue(OpeningReservation.objects.get(user=self.session.user).is_active)

    def test_corrupt_session_key_rolls_back_all_preparation(self):
        first=self.new_order(); self.session.encrypted_session_key='invalid'; self.session.save()
        with self.assertRaises(BusinessError): prepare_payment(self.session.user,self.session,first,'payment-key-123')
        self.assertFalse(OpeningReservation.objects.exists())
        self.assertFalse(PaymentTask.objects.exists())
        self.assertEqual(Order.objects.get(pk=first).status,'created')

    def test_disabled_channels_and_unsynchronized_or_changed_price(self):
        with override_settings(VIRTUAL_PAYMENT_ENABLED=False):
            with self.assertRaises(BusinessError): self.new_order()
        with self.assertRaises(BusinessError): create_order(self.session.user,self.session,self.product.pk,'ios','new-ios-order')
        first=self.new_order(); self.product.price_fen=2000; self.product.save()
        with self.assertRaises(BusinessError): prepare_payment(self.session.user,self.session,first,'payment-key-123')
        mark_product_synced(self.product.pk,self.actor,price_fen=2000,platform_product_id=self.product.platform_product_id)
        with self.assertRaises(BusinessError) as error: prepare_payment(self.session.user,self.session,first,'payment-key-123')
        self.assertEqual(str(error.exception.detail['error']['code']),'PRICE_CHANGED')
        self.assertFalse(OpeningReservation.objects.exists())

    def test_already_active_blocks_new_order_and_preparation(self):
        first=self.new_order(); grant_entitlement(self.session.user,'activation','external-code')
        for fn,args in ((self.new_order,('new-order-key',)),(prepare_payment,(self.session.user,self.session,first,'payment-key-123'))):
            with self.assertRaises(BusinessError) as error: fn(*args)
            self.assertEqual(str(error.exception.detail['error']['code']),'ALREADY_ACTIVATED')
        self.assertEqual(Order.objects.count(),1)

    def test_terminal_replay_returns_history_and_no_payment(self):
        first=self.new_order(); prepare_payment(self.session.user,self.session,first,'payment-key-123')
        order=Order.objects.get(pk=first); order.status='refunded'; order.save(_service=True)
        result=prepare_payment(self.session.user,self.session,first,'payment-key-123')
        self.assertEqual(result['order']['status'],'refunded')
        self.assertIsNone(result['payment']); self.assertFalse(result['entitlement']['active'])

    def test_same_key_different_parameters_rejected(self):
        self.new_order()
        with self.assertRaises(BusinessError) as error:
            create_order(self.session.user,self.session,self.product.pk,'ios','order-key-123')
        self.assertEqual(str(error.exception.detail['error']['code']),'IDEMPOTENCY_CONFLICT')

    def test_prepared_replay_rechecks_product_and_signing_configuration(self):
        first=self.new_order(); prepare_payment(self.session.user,self.session,first,'payment-key-123')
        with override_settings(VIRTUAL_PAYMENT_APP_KEY='changed-key'):
            with self.assertRaises(BusinessError): prepare_payment(self.session.user,self.session,first,'payment-key-123')
        self.product.is_active=False; self.product.save()
        with self.assertRaises(BusinessError): prepare_payment(self.session.user,self.session,first,'payment-key-123')
        self.assertTrue(OpeningReservation.objects.get(user=self.session.user).is_active)

    def test_preparation_rejects_other_wechat_identity_even_same_internal_owner(self):
        from accounts.models import WeChatIdentity
        first=self.new_order()
        second=WeChatIdentity.objects.create(user=self.session.user,app_id='wx-payment-test',openid='different-payer',encrypted_session_key=self.session.encrypted_session_key)
        self.session.identity=second; self.session.save()
        with self.assertRaises(BusinessError): prepare_payment(self.session.user,self.session,first,'payment-key-123')
        self.assertFalse(OpeningReservation.objects.exists())

    def test_revoked_session_is_rechecked_inside_transaction(self):
        first=self.new_order(); revoke_session(self.session)
        with self.assertRaises(BusinessError): prepare_payment(self.session.user,self.session,first,'payment-key-123')
        self.assertFalse(OpeningReservation.objects.exists())

    def test_http_owner_isolation_strict_input_and_staff_cookie(self):
        response=self.client.post('/api/student/v1/orders/',{'productId':self.product.pk,'channel':'android'},format='json',HTTP_IDEMPOTENCY_KEY='http-create-order')
        self.assertEqual(response.status_code,201)
        first=response.data['order']['id']
        response=self.client.post(f'/api/student/v1/orders/{first}/payment/',{},format='json',HTTP_IDEMPOTENCY_KEY='http-prepare-order')
        self.assertEqual(response.status_code,200)
        self.assertIn('no-store',response['Cache-Control'])
        for payload in ({'priceFen':1},[],{'channel':'ios'}):
            self.assertEqual(self.client.post(f'/api/student/v1/orders/{first}/payment/',payload,format='json',HTTP_IDEMPOTENCY_KEY='bad-http-payment').status_code,400)
        self.assertEqual(self.client.get('/api/student/v1/orders/?page_size=100').status_code,400)
        self.assertEqual(self.client.get('/api/student/v1/orders/?page='+'9'*5000).status_code,400)
        other_token,_=issue_student_session(WeChatLogin('wx-payment-test','other',base64.b64encode(b'other-key').decode()))
        self.client.credentials(HTTP_AUTHORIZATION='Bearer '+other_token)
        self.assertEqual(self.client.get(f'/api/student/v1/orders/{first}/').status_code,404)
        self.assertEqual(self.client.get('/api/student/v1/orders/').data['count'],0)
        self.client.credentials(); self.client.force_login(self.actor)
        self.assertEqual(self.client.get('/api/student/v1/orders/').status_code,401)

    def test_admin_cannot_edit_transaction_state_directly(self):
        self.actor.is_superuser=True; self.actor.save()
        first=self.new_order(); self.client.credentials(); self.client.force_login(self.actor)
        self.assertEqual(self.client.post(f'/admin/payments/order/{first}/change/',{'status':'fulfilled'}).status_code,403)
        with self.assertRaises(Exception): Order.objects.filter(pk=first).update(status='fulfilled')
