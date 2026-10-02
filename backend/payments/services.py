import json
from django.db import transaction
from django.utils import timezone
from common import idempotency
from common.errors import BusinessError
from entitlements.models import Product
from entitlements.services import check_new_opening, entitlement_snapshot, has_active_entitlement, lock_user, lock_reservation, reserve_opening
from .configuration import channel_configuration, require_channel_price, require_product_ready
from .models import Order, PaymentTask
from .signing import payment_packet


def order_payload(order):
    return {"id":order.pk, "productId":order.product_id,"productName":order.product_name,
            "priceFen":order.price_fen,"currency":"CNY","scope":order.scope,"permanent":True,
            "channel":order.channel,"status":order.status,"createdAt":order.created_at.isoformat(),
            "preparedAt":order.prepared_at.isoformat() if order.prepared_at else None,
            "paidAt":order.paid_at.isoformat() if order.paid_at else None,
            "fulfilledAt":order.fulfilled_at.isoformat() if order.fulfilled_at else None,
            "refundedAt":order.refunded_at.isoformat() if order.refunded_at else None}


def _session(session, user, configuration):
    session.refresh_from_db()
    if session.user_id != user.pk or not session.identity_id or session.identity.user_id != user.pk or session.identity.app_id != configuration['appId']:
        raise BusinessError("AUTH_REQUIRED", "微信身份与当前支付账号不一致，请重新登录。",401)
    if session.revoked_at or session.expires_at<=timezone.now() or not user.is_active or user.is_staff or user.is_superuser:
        raise BusinessError("AUTH_REQUIRED", "学员会话不可用，请重新登录。",401)


@transaction.atomic
def create_order(user, session, product_id, channel, key):
    locked=lock_user(user); lock_reservation(locked)
    if not locked.is_active or locked.is_staff or locked.is_superuser:
        raise BusinessError("AUTH_REQUIRED","学员账户不可用。",401)
    payload={"productId":product_id,"channel":channel}
    previous=idempotency.lookup(locked,'payment-create',key,payload)
    if previous:
        order=Order.objects.get(pk=previous.response['orderId'],user=locked)
        return {"order":order_payload(order),"entitlement":entitlement_snapshot(locked)}
    check_new_opening(locked)
    configuration=channel_configuration(channel); _session(session,locked,configuration)
    try: product=Product.objects.select_for_update().get(pk=product_id)
    except Product.DoesNotExist as error: raise BusinessError("NOT_FOUND","商品不存在。",404) from error
    require_product_ready(product, channel=channel)
    order=Order(user=locked,identity=session.identity,product=product,product_name=product.name,
                price_fen=product.price_fen,scope=product.scope,platform_product_id=product.platform_product_id,
                app_id=configuration['appId'],environment=configuration['environment'],channel=channel)
    order.save(_service=True)
    idempotency.remember(locked,'payment-create',key,payload,{"orderId":order.pk})
    return {"order":order_payload(order),"entitlement":entitlement_snapshot(locked)}


@transaction.atomic
def prepare_payment(user, session, order_id, key):
    locked=lock_user(user); lock_reservation(locked)
    if not locked.is_active or locked.is_staff or locked.is_superuser:
        raise BusinessError("AUTH_REQUIRED","学员账户不可用。",401)
    try: order=Order.objects.select_for_update().get(pk=order_id,user=locked)
    except Order.DoesNotExist as error: raise BusinessError("NOT_FOUND","订单不存在。",404) from error
    payload={"orderId":order.pk,"sessionId":session.pk}
    previous=idempotency.lookup(locked,'payment-prepare',key,payload)
    if previous and (order.status!='preparing' or has_active_entitlement(locked)):
        return {"order":order_payload(order),"payment":None,"entitlement":entitlement_snapshot(locked)}
    if not previous and has_active_entitlement(locked):
        raise BusinessError("ALREADY_ACTIVATED","已激活",409)
    if not previous and order.status!='created':
        raise BusinessError("PAYMENT_ALREADY_PREPARED","订单已进入平台处理，请查询原订单结果。",409,fields={"order":order_payload(order)})
    configuration=channel_configuration(order.channel); _session(session,locked,configuration)
    require_channel_price(order.channel, order.price_fen)
    if order.identity_id!=session.identity_id:
        raise BusinessError("AUTH_REQUIRED","订单与当前微信身份不一致。",401)
    configuration_digest=idempotency.payload_digest(configuration)
    if order.app_id!=configuration['appId'] or order.environment!=configuration['environment'] or (previous and order.configuration_digest!=configuration_digest):
        raise BusinessError("PAYMENT_CONFIGURATION_CHANGED","支付环境已变化，请联系管理员核查原订单。",409)
    product=Product.objects.select_for_update().get(pk=order.product_id)
    require_product_ready(product, channel=order.channel)
    if product.price_fen!=order.price_fen or product.platform_product_id!=order.platform_product_id:
        raise BusinessError("PRICE_CHANGED","商品已变更，请重新获取商品并创建订单。",409)
    if previous:
        return {"order":order_payload(order),"payment":payment_packet(order,session,configuration),"entitlement":entitlement_snapshot(locked)}
    check_new_opening(locked)
    reserve_opening(locked,order.pk)
    order.sign_data=json.dumps({"offerId":configuration['offerId'],"buyQuantity":1,
        "env":order.environment,"currencyType":"CNY","productId":order.platform_product_id,
        "goodsPrice":order.price_fen,"outTradeNo":order.pk,"attach":order.pk},ensure_ascii=False,separators=(',',':'))
    packet=payment_packet(order,session,configuration) # decryption failure rolls back reservation
    order.status='preparing'; order.prepared_at=timezone.now(); order.prepared_session=session
    order.configuration_digest=configuration_digest
    order.save(_service=True)
    task=PaymentTask(order=order,kind='query'); task.save(_service=True)
    idempotency.remember(locked,'payment-prepare',key,payload,{"orderId":order.pk})
    return {"order":order_payload(order),"payment":packet,"entitlement":entitlement_snapshot(locked)}
