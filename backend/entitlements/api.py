from django.conf import settings
from rest_framework.response import Response

from common.api import PublicStudentAPIView, StudentAPIView
from .models import Product
from .services import entitlement_snapshot


def product_payload(product):
    synced = bool(product.platform_product_id and product.platform_sync_state == "synced" and product.platform_synced_price_fen == product.price_fen)
    configured = bool(getattr(settings, "VIRTUAL_PAYMENT_ENABLED", False))
    base = product.is_active and synced and configured
    channels = {
        "android": bool(base and getattr(settings, "VIRTUAL_PAYMENT_ANDROID_ENABLED", False)),
        "ios": bool(base and getattr(settings, "VIRTUAL_PAYMENT_IOS_ENABLED", False)),
    }
    available = any(channels.values())
    if available:
        reason = None
    elif not product.is_active:
        reason = "PRODUCT_DISABLED"
    elif not synced:
        reason = "PLATFORM_SYNC_REQUIRED"
    else:
        reason = "PAYMENT_CHANNEL_UNAVAILABLE"
    return {
        "id": product.pk, "scope": product.scope, "name": product.name,
        "priceFen": product.price_fen, "currency": "CNY", "permanent": True,
        "expiresAt": None, "purchaseAvailable": available, "paymentChannels": channels,
        "unavailableReason": reason,
    }


class ProductListView(PublicStudentAPIView):
    def get(self, request):
        results = [product_payload(product) for product in Product.objects.order_by("pk")]
        return Response({"count": len(results), "next": None, "previous": None, "results": results})


class MyEntitlementsView(StudentAPIView):
    def get(self, request):
        return Response(entitlement_snapshot(request.user), headers={"Cache-Control": "no-store, private"})
