from django.http import HttpResponse
from django.db import transaction
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.debug import sensitive_variables
from django.views.decorators.http import require_http_methods

from common.errors import BusinessError
from .protocol import callback_configuration, decrypt_message, invalid, parse_payload, verify_signature
from .synchronization import apply_callback


@transaction.non_atomic_requests
@csrf_exempt  # WeChat authenticates the body using msg_signature + AES, not cookies.
@require_http_methods(("GET", "POST"))
@sensitive_variables("data", "encrypted", "key", "clear")
def virtual_payment(request):
    try:
        if any(len(request.GET.getlist(name)) != 1 for name in request.GET):
            raise invalid()
        token, key, app_id = callback_configuration()
        if request.method == "GET":
            echo = request.GET.get("echostr", "")
            if not echo or len(echo) > 65536:
                raise invalid()
            if request.GET.get("msg_signature"):
                verify_signature(token, request.GET, echo)
                echo = decrypt_message(echo, key, app_id)
            else:
                verify_signature(token, request.GET)
            return HttpResponse(echo, content_type="text/plain", headers={"Cache-Control": "no-store"})
        if request.GET.get("encrypt_type") != "aes":
            raise invalid()
        data = parse_payload(request.read(65537))
        encrypted = data.get("Encrypt")
        if not isinstance(encrypted, str):
            raise invalid()
        verify_signature(token, request.GET, encrypted)
        clear = decrypt_message(encrypted, key, app_id)
        outcome = apply_callback(parse_payload(clear), app_id)
        if outcome in ("review", "uncertain"):
            return HttpResponse("platform_review_required", status=503)
        # atomic apply_callback has returned, so its transaction committed before
        # returning the official success acknowledgement.
        return HttpResponse("success", content_type="text/plain")
    except BusinessError as error:
        return HttpResponse(str(error.detail["error"]["code"]), status=error.status_code, content_type="text/plain")
