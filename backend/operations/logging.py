"""Do not serialize request bodies, raw paths, headers or exception values."""
import json
import logging
import time
import uuid
from datetime import datetime, timezone


class SafeJSONFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "event": "http_request" if record.name == "operations.http" else "application_error",
            "requestId": getattr(record, "request_id", None),
            "route": getattr(record, "route", None),
            "status": getattr(record, "status_code", None),
            "durationMs": getattr(record, "duration_ms", None),
        }, ensure_ascii=False)


class RequestLoggingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        started = time.monotonic()
        request.request_id = str(uuid.uuid4())
        response = self.get_response(request)
        response["X-Request-ID"] = request.request_id
        match = getattr(request, "resolver_match", None)
        logging.getLogger("operations.http").info("http_request", extra={
            "request_id": request.request_id,
            "route": match.view_name if match else "unmatched",
            "status_code": response.status_code,
            "duration_ms": round((time.monotonic() - started) * 1000, 1),
        })
        return response
