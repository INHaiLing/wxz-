"""Explicit response contracts plus input metadata from the actual serializers.

This module only writes documentation; it registers no HTTP views or fake APIs.
"""
import importlib
import json
import re
from copy import deepcopy
from pathlib import Path

from django.conf import settings
from django.urls import URLResolver, get_resolver
from rest_framework import serializers
from rest_framework.permissions import AllowAny


PREFIX = "/api/student/v1/"
DOCUMENT_PATH = Path(settings.BASE_DIR).parent / "doc" / "api" / "student-v1.openapi.json"
PARAM_NAMES = {"pk": "id", "order_id": "id", "round_id": "roundId", "group_index": "groupIndex"}
METHODS = ("get", "post", "put", "patch", "delete")


def ref(name):
    return {"$ref": "#/components/schemas/" + name}


def nullable(schema):
    return {"anyOf": [schema, {"type": "null"}]}


def obj(properties, *, required=None, extra=False):
    return {"type": "object", "properties": properties,
            "required": list(properties) if required is None else required, "additionalProperties": extra}


STRING = {"type": "string"}
BOOL = {"type": "boolean"}
INTEGER = {"type": "integer", "minimum": 0}
POSITIVE = {"type": "integer", "minimum": 1}
DATE_TIME = {"type": "string", "format": "date-time"}
UUID = {"type": "string", "format": "uuid"}
SOURCE = {"type": "string", "enum": ["literature", "classical"]}
TYPE = {"type": "string", "enum": ["fact", "word", "translation"]}
MODE = {"type": "string", "enum": ["writing", "reciting"]}
CAPACITY = {"type": "integer", "minimum": 1, "maximum": 100}
SCOPE = {"source": SOURCE, "categoryId": nullable(STRING), "articleId": nullable(STRING), "type": nullable(TYPE)}
QUESTION = {
    "id": STRING, "source": SOURCE, "categoryId": nullable(STRING), "articleId": nullable(STRING),
    "type": TYPE, "tag": STRING, "stem": STRING, "answers": {"type": "array", "items": STRING, "minItems": 1},
    "sortOrder": INTEGER, "revision": POSITIVE,
}
EXAMPLE_QUESTION = {
    "id": "example-quanxue-001", "source": "classical", "categoryId": None, "articleId": "quanxue",
    "type": "word", "tag": "字词", "stem": "吾尝{{0}}而思矣。", "answers": ["终日"], "sortOrder": 1, "revision": 1,
}
EXAMPLES = {
    "Question": EXAMPLE_QUESTION,
    "QuestionDetail": {**EXAMPLE_QUESTION, "access": {"activated": False, "free": True, "pageSize": 20}},
    "Entitlement": {"scope": "all_chinese", "active": False, "permanent": False, "expiresAt": None},
    "Product": {"id": "all-chinese", "scope": "all_chinese", "name": "语文全题库永久权益", "priceFen": 1000,
                "currency": "CNY", "permanent": True, "expiresAt": None, "purchaseAvailable": False,
                "paymentChannels": {"android": False, "ios": False}, "unavailableReason": "PAYMENT_CHANNEL_UNAVAILABLE"},
    "Category": {"id": "literature", "name": "文学常识", "sortOrder": 1, "publishedCount": 30, "freeCount": 20},
    "Article": {"id": "quanxue", "title": "劝学", "sortOrder": 1, "publishedCount": 30, "freeCount": 20},
    "LearningConfig": {"pageSize": 20, "dailyTarget": 20, "examDate": None},
    "QuestionState": {"questionId": "example-quanxue-001", "favorite": True, "mastered": False, "version": 1},
    "Preference": {"mode": "writing", "dailyTarget": 20, "version": 1},
    "Resume": {"version": 0, "resume": None, "valid": False, "reason": "NO_RESUME", "start": None},
    "ResumePosition": {"source": "classical", "categoryId": None, "articleId": "quanxue", "type": None,
                       "mode": "writing", "questionId": "example-quanxue-001", "roundId": None, "groupIndex": None},
}


def page(item, **extra):
    return obj({"count": INTEGER, "next": nullable(STRING), "previous": nullable(STRING),
                "results": {"type": "array", "items": item}, **extra})


def schemas():
    definitions = {
        "StudentError": obj({"error": obj({"code": STRING, "message": STRING,
                    "fields": {"type": "object", "additionalProperties": True}}, required=["code", "message"]),
                    "requestId": STRING}),
        "LoginResult": obj({"token": STRING, "expiresAt": DATE_TIME, "user": obj({"id": POSITIVE})}),
        "LearningConfig": obj({"pageSize": CAPACITY, "dailyTarget": {"type": "integer", "minimum": 1, "maximum": 1000},
                    "examDate": nullable({"type": "string", "format": "date"})}),
        "Category": obj({"id": STRING, "name": STRING, "sortOrder": INTEGER, "publishedCount": INTEGER, "freeCount": INTEGER}),
        "Article": obj({"id": STRING, "title": STRING, "sortOrder": INTEGER, "publishedCount": INTEGER, "freeCount": INTEGER}),
        "Question": obj(QUESTION),
        "QuestionDetail": obj({**QUESTION, "access": obj({"activated": BOOL, "free": BOOL, "pageSize": CAPACITY})}),
        "QuestionPage": page(ref("Question"), access=obj({"activated": BOOL, "pageSize": CAPACITY,
                    "freeCount": INTEGER, "totalCount": INTEGER, "restrictedCount": INTEGER})),
        "Entitlement": obj({"scope": {"type": "string", "enum": ["all_chinese"]}, "active": BOOL,
                    "permanent": BOOL, "expiresAt": {"type": "null"}}),
        "Product": obj({"id": STRING, "scope": {"type": "string", "enum": ["all_chinese"]}, "name": STRING,
                    "priceFen": POSITIVE, "currency": {"type": "string", "enum": ["CNY"]},
                    "permanent": {"type": "boolean", "const": True}, "expiresAt": {"type": "null"},
                    "purchaseAvailable": BOOL, "paymentChannels": obj({"android": BOOL, "ios": BOOL}),
                    "unavailableReason": nullable({"type": "string", "enum": ["PRODUCT_DISABLED", "PLATFORM_SYNC_REQUIRED", "PAYMENT_CHANNEL_UNAVAILABLE"]})}),
        "Redemption": obj({"redemptionId": UUID, "redeemedAt": DATE_TIME, "entitlement": ref("Entitlement")}),
        "QuestionState": obj({"questionId": STRING, "favorite": BOOL, "mastered": BOOL, "version": INTEGER}),
        "Preference": obj({"mode": MODE, "dailyTarget": {"type": "integer", "minimum": 1, "maximum": 1000}, "version": INTEGER}),
        "Statistics": obj({"totalQuestions": INTEGER, "masteredQuestions": INTEGER,
                    "masteredPercent": {"type": "number", "minimum": 0, "maximum": 100},
                    "todayQuestions": INTEGER, "studyDays": INTEGER, "completedArticles": INTEGER,
                    "dailyTarget": {"type": "integer", "minimum": 1, "maximum": 1000}}),
        "FavoriteItem": obj({"state": ref("QuestionState"), "question": nullable(ref("Question")),
                    "unavailableReason": nullable({"type": "string", "enum": ["ENTITLEMENT_REQUIRED", "CONTENT_UNAVAILABLE"]})}),
        "Round": obj({"id": UUID, **SCOPE, "mode": MODE, "pageSize": CAPACITY,
                    "totalCount": INTEGER, "groupCount": INTEGER, "createdAt": DATE_TIME}),
        "RoundGroup": obj({"roundId": UUID, "groupIndex": POSITIVE, "groupCount": POSITIVE,
                    "totalCount": INTEGER, "pageSize": CAPACITY, "mode": MODE,
                    "results": {"type": "array", "items": obj({**QUESTION, "position": POSITIVE})},
                    "skipped": {"type": "array", "items": obj({"position": POSITIVE,
                    "reason": {"type": "string", "enum": ["UNAVAILABLE", "SCOPE_CHANGED", "ENTITLEMENT_REQUIRED"]}})}}),
        "ResumePosition": obj({**SCOPE, "mode": MODE, "questionId": STRING, "roundId": nullable(UUID), "groupIndex": nullable(POSITIVE)}),
        "Resume": obj({"version": INTEGER, "resume": nullable(ref("ResumePosition")), "valid": BOOL,
                    "reason": nullable({"type": "string", "enum": ["NO_RESUME", "UNAVAILABLE", "SCOPE_CHANGED", "ENTITLEMENT_REQUIRED", "ROUND_UNAVAILABLE"]}),
                    "start": nullable(ref("ResumePosition"))}),
        "ResumeSaved": obj({"version": INTEGER, "resume": ref("ResumePosition"), "current": ref("Resume")}),
        "Order": obj({"id": STRING, "productId": STRING, "productName": STRING, "priceFen": POSITIVE,
                    "currency": {"type": "string", "enum": ["CNY"]}, "scope": {"type": "string", "enum": ["all_chinese"]},
                    "permanent": {"type": "boolean", "const": True}, "channel": {"type": "string", "enum": ["android", "ios"]},
                    "status": {"type": "string", "enum": ["created", "preparing", "paid", "fulfilled", "closed", "refunded", "review"]},
                    "createdAt": DATE_TIME, "preparedAt": nullable(DATE_TIME), "paidAt": nullable(DATE_TIME),
                    "fulfilledAt": nullable(DATE_TIME), "refundedAt": nullable(DATE_TIME)}),
        "OrderResult": obj({"order": ref("Order"), "entitlement": ref("Entitlement")}),
        "PaymentPacket": obj({"mode": {"type": "string", "enum": ["short_series_goods"]},
                    "signData": {"type": "string", "description": "签名覆盖的原始 JSON 字符串，严禁解析后重新序列化。"},
                    "paySig": STRING, "signature": STRING}),
        "PaymentPrepared": obj({"order": ref("Order"), "payment": nullable(ref("PaymentPacket")), "entitlement": ref("Entitlement")}),
    }
    for name, item in (("CategoryPage", "Category"), ("ArticlePage", "Article"), ("ProductPage", "Product"),
                       ("Favorites", "FavoriteItem"), ("OrderPage", "Order")):
        definitions[name] = page(ref(item))
    return definitions


# View names are independent of worktree path and checked against the real URL resolver.
# Future module declarations enter the artifact only after that module is mounted.
# (response, success status, request serializer, query serializer, idempotency)
BINDINGS = {
    ("student-wechat-login", "post"): ("LoginResult", 200, "accounts.student_api.LoginInput", None, False),
    ("student-logout", "post"): (None, 204, None, None, False),
    ("student-config", "get"): ("LearningConfig", 200, None, "content.student_api.EmptyQuery", False),
    ("student-categories", "get"): ("CategoryPage", 200, None, "content.student_api.StudentPageQuery", False),
    ("student-articles", "get"): ("ArticlePage", 200, None, "content.student_api.StudentPageQuery", False),
    ("student-questions", "get"): ("QuestionPage", 200, None, "content.student_api.StudentQuestionQuery", False),
    ("student-question-detail", "get"): ("QuestionDetail", 200, None, "content.student_api.EmptyQuery", False),
    ("student-products", "get"): ("ProductPage", 200, None, None, False),
    ("student-entitlements", "get"): ("Entitlement", 200, None, None, False),
    ("student-activation-redeem", "post"): ("Redemption", 200, "activation.api.RedeemInput", None, True),
    ("student-question-state", "get"): ("QuestionState", 200, None, "learning.student_api.NoQuery", False),
    ("student-question-state", "put"): ("QuestionState", 200, "learning.serializers.QuestionStateInput", "learning.student_api.NoQuery", True),
    ("student-favorites", "get"): ("Favorites", 200, None, "learning.student_api.FavoriteQuery", False),
    ("student-statistics", "get"): ("Statistics", 200, None, "learning.student_api.NoQuery", False),
    ("student-preferences", "get"): ("Preference", 200, None, "learning.student_api.NoQuery", False),
    ("student-preferences", "put"): ("Preference", 200, "learning.serializers.PreferenceInput", "learning.student_api.NoQuery", True),
    ("student-practice-create", "post"): ("Round", 201, "practice.serializers.RoundRequest", "content.student_api.EmptyQuery", True),
    ("student-practice-group", "get"): ("RoundGroup", 200, None, "content.student_api.EmptyQuery", False),
    ("student-resume", "get"): ("Resume", 200, None, "content.student_api.EmptyQuery", False),
    ("student-resume", "put"): ("ResumeSaved", 200, "practice.serializers.ResumeRequest", "content.student_api.EmptyQuery", True),
    ("student-orders", "post"): ("OrderResult", 201, "payments.api.CreateInput", "empty", True),
    ("student-orders", "get"): ("OrderPage", 200, None, "order-page", False),
    ("student-order-detail", "get"): ("Order", 200, None, "empty", False),
    ("student-payment-prepare", "post"): ("PaymentPrepared", 200, "payments.api.StrictInput", "empty", True),
    ("student-payment-query", "post"): ("OrderResult", 200, "payments.api.StrictInput", "empty", True),
}


def import_serializer(path):
    module, name = path.rsplit(".", 1)
    return getattr(importlib.import_module(module), name)()


def field_schema(field):
    if isinstance(field, serializers.ChoiceField):
        values = list(field.choices)
        schema = {"type": "string", "enum": values}
    elif isinstance(field, serializers.BooleanField):
        schema = {"type": "boolean"}
    elif isinstance(field, serializers.IntegerField):
        schema = {"type": "integer"}
        if field.min_value is not None:
            schema["minimum"] = field.min_value
        if field.max_value is not None:
            schema["maximum"] = field.max_value
    elif isinstance(field, serializers.UUIDField):
        schema = dict(UUID)
    elif isinstance(field, serializers.CharField):
        schema = {"type": "string"}
        if field.max_length is not None:
            schema["maxLength"] = field.max_length
        if not field.allow_blank:
            schema["minLength"] = 1
        if isinstance(field, serializers.SlugField):
            schema["pattern"] = "^[A-Za-z0-9_-]+$"
        if isinstance(field, serializers.RegexField):
            regex = next((validator.regex for validator in field.validators if hasattr(validator, "regex")), None)
            if regex is not None:
                schema["pattern"] = regex.pattern
    else:
        raise ValueError(f"未定义请求字段类型 {type(field).__name__}，请补契约。")
    return nullable(schema) if field.allow_null else schema


def input_schema(path):
    serializer = import_serializer(path)
    result = obj({name: field_schema(field) for name, field in serializer.fields.items()},
                 required=[name for name, field in serializer.fields.items() if field.required])
    if path in ("learning.serializers.QuestionStateInput", "learning.serializers.PreferenceInput"):
        result["minProperties"] = 2
    return result


def query_parameters(path):
    if path is None or path == "empty":
        return []
    if path == "order-page":
        return [{"name": "page", "in": "query", "required": False,
                 "schema": {"type": "integer", "minimum": 1, "maximum": 100000}}]
    return [{"name": name, "in": "query", "required": field.required,
             "schema": field_schema(field), "description": "单次提供，空值或重复值会拒绝。"}
            for name, field in import_serializer(path).fields.items()]


def routes():
    found = {}

    def visit(patterns, prefix=""):
        for pattern in patterns:
            route = prefix + str(pattern.pattern)
            if isinstance(pattern, URLResolver):
                visit(pattern.url_patterns, route)
                continue
            absolute = "/" + route
            if not absolute.startswith(PREFIX):
                continue
            converters = re.findall(r"<(?:(\w+):)?(\w+)>", absolute)
            canonical = re.sub(r"<(?:(\w+):)?(\w+)>", lambda m: "{" + PARAM_NAMES.get(m[2], m[2]) + "}", absolute)
            cls = getattr(pattern.callback, "view_class", None)
            if cls is None:
                raise ValueError(f"未支持学生函数路由 {absolute}")
            for method in METHODS:
                if method in cls.http_method_names and hasattr(cls, method):
                    key = (canonical, method)
                    if key in found:
                        raise ValueError(f"重复学生路由 {key}")
                    found[key] = {"name": pattern.name, "view": cls,
                                  "public": AllowAny in cls.permission_classes,
                                  "params": [(PARAM_NAMES.get(name, name), converter or "str") for converter, name in converters]}
    visit(get_resolver().url_patterns)
    return found


def example(schema, definitions):
    if "$ref" in schema:
        name = schema["$ref"].split("/")[-1]
        if name in EXAMPLES:
            return deepcopy(EXAMPLES[name])
        return example(definitions[name], definitions)
    if "anyOf" in schema:
        if any(choice.get("type") == "null" for choice in schema["anyOf"]):
            return None
        return example(schema["anyOf"][0], definitions)
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if kind == "object":
        return {key: example(value, definitions) for key, value in schema.get("properties", {}).items() if key in schema.get("required", [])}
    if kind == "array":
        return [example(schema["items"], definitions)]
    if kind == "integer" or kind == "number":
        return schema.get("minimum", 0)
    if kind == "boolean":
        return False
    if kind == "null":
        return None
    if schema.get("format") == "date-time":
        return "2026-10-02T00:00:00+08:00"
    if schema.get("format") == "date":
        return "2026-10-02"
    if schema.get("format") == "uuid":
        return "00000000-0000-4000-8000-000000000001"
    return "test-only-example"


def build_document():
    definitions = schemas()
    paths = {}
    for (path, method), route in sorted(routes().items()):
        binding_key = (route["name"], method)
        if binding_key not in BINDINGS:
            raise ValueError(f"新路由尚无契约：{binding_key}")
        response_name, status, request_path, query_path, keyed = BINDINGS[binding_key]
        parameters = query_parameters(query_path)
        for name, converter in route["params"]:
            schema = POSITIVE if converter == "int" else UUID if converter == "uuid" else STRING
            parameters.append({"name": name, "in": "path", "required": True, "schema": schema})
        if keyed:
            parameters.append({"name": "Idempotency-Key", "in": "header", "required": True,
                               "schema": {"type": "string", "minLength": 8, "maxLength": 128, "pattern": "^[A-Za-z0-9._:-]+$"},
                               "description": "同成功操作重试保留原键／载荷；同键异载荷409；新操作生成新键。"})
        operation = {
            "operationId": route["name"].replace("-", "_") + "_" + method,
            "summary": route["name"], "tags": [route["view"].__module__.split(".")[0]],
            "security": [{}, {"studentBearer": []}] if route["public"] else [{"studentBearer": []}],
            "parameters": parameters,
            "x-backend-view": route["view"].__module__ + "." + route["view"].__name__,
            "x-request-serializer": request_path, "x-query-serializer": query_path,
            "x-query-policy": "ignored" if query_path is None else "strict", "x-idempotency": keyed,
            "responses": {str(status): {"description": "成功；示例为脱敏结构示例。"}},
        }
        if response_name:
            response_example = example(ref(response_name), definitions)
            if "results" in response_example and "count" in response_example:
                response_example.update(count=len(response_example["results"]), next=None, previous=None)
            if response_name == "QuestionPage":
                response_example["access"] = {"activated": False, "pageSize": 20, "freeCount": 1, "totalCount": 1, "restrictedCount": 0}
            operation["responses"][str(status)]["content"] = {"application/json": {
                "schema": ref(response_name), "example": response_example}}
        operation["responses"][str(status)]["headers"] = {"X-Request-ID": {"description": "服务端请求标识，用于定位联调问题。", "schema": STRING}}
        for code in (400, 401, 403, 404, 409, 429, 503):
            error_example = {"error": {"code": {400: "VALIDATION_ERROR", 401: "INVALID_TOKEN", 403: "ENTITLEMENT_REQUIRED",
                        404: "NOT_FOUND", 409: "VERSION_CONFLICT", 429: "RATE_LIMITED", 503: "CHANNEL_UNAVAILABLE"}[code],
                        "message": "脱敏错误示例"}, "requestId": "test-only-request-id"}
            if code == 409 and route["name"] in ("student-question-state", "student-preferences"):
                error_example["error"]["fields"] = {"current": example(ref(response_name), definitions)}
            if code == 409 and route["name"] in ("student-activation-redeem", "student-orders"):
                error_example["error"].update(code="ALREADY_ACTIVATED", message="已激活")
            operation["responses"][str(code)] = {"description": "认证／校验／业务错误；具体 code 见联调手册。",
                "content": {"application/json": {"schema": ref("StudentError"), "example": error_example}}}
        if request_path:
            request = input_schema(request_path)
            request_example = example(request, definitions)
            if request_path == "learning.serializers.QuestionStateInput":
                request_example["favorite"] = True
            elif request_path == "learning.serializers.PreferenceInput":
                request_example["mode"] = "writing"
            elif request_path == "activation.api.RedeemInput":
                request_example["code"] = "QM-" + "-".join(["AAAA"] * 13)
            if "source" in request_example:
                request_example["categoryId"] = "literature"
            operation["requestBody"] = {"required": True, "content": {"application/json": {"schema": request, "example": request_example}}}
        paths.setdefault(path, {})[method] = operation
    return {"openapi": "3.1.0", "info": {"title": "专升本语文学员后端接口", "version": "1.0.0",
             "description": "学员接口v1；公开接口显式非法Bearer仍401。真实渠道/甲方设备联调另行验收。结构示例均不可用于真实激活。"},
            "servers": [{"url": "http://127.0.0.1:8000", "description": "本机开发；生产替换为HTTPS域名"}],
            "paths": paths, "components": {"securitySchemes": {"studentBearer": {"type": "http", "scheme": "bearer",
             "description": "独立学员随机令牌；不使用后台Cookie；无效/过期401，请wx.login后重新换取。"}}, "schemas": definitions}}


def write_document():
    document = build_document()
    DOCUMENT_PATH.parent.mkdir(parents=True, exist_ok=True)
    DOCUMENT_PATH.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return document
