"""Business errors apply only to student views, preserving the staff contract."""
import uuid

from rest_framework.exceptions import APIException
from rest_framework.views import exception_handler


class BusinessError(APIException):
    def __init__(self, code, message, status=400, fields=None):
        self.status_code = status
        detail = {"error": {"code": code, "message": message}}
        if fields is not None:
            detail["error"]["fields"] = fields
        super().__init__(detail=detail, code=code)


def student_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is None:
        return None  # unexpected failures remain errors, not fabricated success
    request = context.get("request")
    request_id = getattr(request, "request_id", uuid.uuid4().hex)
    if isinstance(exc, BusinessError):
        data = dict(response.data)
    else:
        code = {
            400: "VALIDATION_ERROR", 401: "AUTH_REQUIRED", 403: "FORBIDDEN",
            404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 429: "RATE_LIMITED",
        }.get(response.status_code, "REQUEST_FAILED")
        detail = response.data
        message = detail.get("detail", "请求数据无效。") if isinstance(detail, dict) else "请求失败。"
        data = {"error": {"code": code, "message": str(message)}}
        if response.status_code == 400:
            data["error"]["fields"] = detail
    data["requestId"] = request_id
    response.data = data
    return response
