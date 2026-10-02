from django.conf import settings
import base64
import binascii
from common.errors import BusinessError


def channel_configuration(channel):
    if channel not in ("android", "ios"):
        raise BusinessError("INVALID_CHANNEL", "支付渠道无效。")
    if not getattr(settings, "VIRTUAL_PAYMENT_ENABLED", False) or not getattr(settings, "VIRTUAL_PAYMENT_"+channel.upper()+"_ENABLED", False):
        raise BusinessError("PAYMENT_CHANNEL_UNAVAILABLE", "当前支付渠道未启用。", 503)
    environment = getattr(settings, "VIRTUAL_PAYMENT_ENV", 0)
    if type(environment) is not int or environment not in (0,1):
        raise BusinessError("PAYMENT_CONFIGURATION_ERROR", "支付配置不可用。", 503)
    app_key = getattr(settings, "VIRTUAL_PAYMENT_APP_KEY" if environment==0 else "VIRTUAL_PAYMENT_SANDBOX_APP_KEY", "")
    offer_id = getattr(settings, "VIRTUAL_PAYMENT_OFFER_ID", "")
    if not all(isinstance(v,str) and v for v in (app_key,offer_id,getattr(settings,"WECHAT_APP_ID",""),
            getattr(settings,"WECHAT_APP_SECRET",""),getattr(settings,"VIRTUAL_PAYMENT_CALLBACK_TOKEN",""),
            getattr(settings,"VIRTUAL_PAYMENT_CALLBACK_AES_KEY",""))):
        raise BusinessError("PAYMENT_CONFIGURATION_ERROR", "支付配置不可用。", 503)
    try:
        aes_key=base64.b64decode(settings.VIRTUAL_PAYMENT_CALLBACK_AES_KEY+'=',validate=True)
        if len(aes_key)!=32: raise ValueError('length')
    except (ValueError, binascii.Error):
        raise BusinessError("PAYMENT_CONFIGURATION_ERROR", "支付消息安全配置不可用。", 503) from None
    return {"environment":environment, "appKey":app_key,"offerId":offer_id,"appId":settings.WECHAT_APP_ID}


def available_channels():
    channels={}
    for channel in ('android','ios'):
        try: channel_configuration(channel)
        except BusinessError: channels[channel]=False
        else: channels[channel]=True
    return channels


def require_product_ready(product):
    if not product.is_active:
        raise BusinessError("PRODUCT_DISABLED", "商品当前不可购买。", 409)
    if not product.platform_product_id or product.platform_sync_state!='synced' or product.platform_synced_price_fen!=product.price_fen:
        raise BusinessError("PLATFORM_SYNC_REQUIRED", "平台商品和价格尚未确认同步。", 409)
