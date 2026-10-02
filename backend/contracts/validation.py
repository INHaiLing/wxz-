"""A bounded validator for the schema vocabulary used by this contract.

This is not a general JSON Schema implementation. Unsupported keys fail the
contract check so no assertion is silently skipped when the vocabulary grows.
"""
import json
import re
import uuid
from datetime import date, datetime
from urllib.parse import urlsplit

from .document import DOCUMENT_PATH, build_document, routes


SCHEMA_KEYS = {"$ref", "type", "anyOf", "enum", "const", "properties", "required", "additionalProperties",
               "items", "minItems", "minProperties", "minimum", "maximum", "minLength", "maxLength",
               "pattern", "format", "description", "example"}


def load_document():
    return json.loads(DOCUMENT_PATH.read_text(encoding="utf-8"))


def resolve_ref(document, pointer):
    if not isinstance(pointer, str) or not pointer.startswith("#/"):
        raise ValueError("契约仅允许文档内部引用。")
    result = document
    for key in pointer[2:].split("/"):
        result = result[key.replace("~1", "/").replace("~0", "~")]
    return result


def validate_value(value, schema, document, path="$", depth=0):
    if depth > 40:
        raise ValueError(f"{path}: 引用嵌套超出限度")
    if "$ref" in schema:
        validate_value(value, resolve_ref(document, schema["$ref"]), document, path, depth + 1)
    if "anyOf" in schema:
        failures = []
        for choice in schema["anyOf"]:
            try:
                validate_value(value, choice, document, path, depth + 1)
                break
            except (ValueError, KeyError) as error:
                failures.append(str(error))
        else:
            raise ValueError(f"{path}: 不符合anyOf {failures}")
    kind = schema.get("type")
    types = {"null": lambda x: x is None, "object": lambda x: isinstance(x, dict),
             "array": lambda x: isinstance(x, list), "string": lambda x: isinstance(x, str),
             "integer": lambda x: isinstance(x, int) and not isinstance(x, bool),
             "number": lambda x: isinstance(x, (int, float)) and not isinstance(x, bool),
             "boolean": lambda x: isinstance(x, bool)}
    if kind is not None and (kind not in types or not types[kind](value)):
        raise ValueError(f"{path}: 应为 {kind}，实际 {type(value).__name__}")
    if "const" in schema and (value != schema["const"] or type(value) is not type(schema["const"])):
        raise ValueError(f"{path}: 常量不符")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path}: 枚举不符")
    if isinstance(value, dict):
        missing = set(schema.get("required", [])) - set(value)
        if missing:
            raise ValueError(f"{path}: 缺少字段 {sorted(missing)}")
        if len(value) < schema.get("minProperties", 0):
            raise ValueError(f"{path}: 字段数量不足")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False and set(value) - set(properties):
            raise ValueError(f"{path}: 额外字段 {sorted(set(value) - set(properties))}")
        for name, item in value.items():
            if name in properties:
                validate_value(item, properties[name], document, path + "." + name, depth + 1)
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise ValueError(f"{path}: 数组数量不足")
        if "items" in schema:
            for index, item in enumerate(value):
                validate_value(item, schema["items"], document, f"{path}[{index}]", depth + 1)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value < schema.get("minimum", float("-inf")) or value > schema.get("maximum", float("inf")):
            raise ValueError(f"{path}: 数值超出范围")
    if isinstance(value, str):
        if not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", float("inf")):
            raise ValueError(f"{path}: 字符串长度超出范围")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            raise ValueError(f"{path}: 字符串格式不符")
        try:
            if schema.get("format") == "uuid":
                uuid.UUID(value)
            elif schema.get("format") == "date":
                date.fromisoformat(value)
            elif schema.get("format") == "date-time":
                if datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is None:
                    raise ValueError("必须包含时区")
        except ValueError as error:
            raise ValueError(f"{path}: {schema.get('format')}格式不符") from error


def validate_document(document):
    errors = []
    if document.get("openapi") != "3.1.0":
        errors.append("OpenAPI必须为3.1.0")

    def walk(value, path="$", schema_context=False):
        if isinstance(value, dict):
            if schema_context:
                unknown = set(value) - SCHEMA_KEYS
                if unknown:
                    errors.append(f"{path}: 自查尚不支持schema关键字 {sorted(unknown)}")
                if "format" in value and value["format"] not in ("date", "date-time", "uuid"):
                    errors.append(f"{path}: 自查尚不支持format {value['format']}")
                if "additionalProperties" in value and not isinstance(value["additionalProperties"], bool):
                    errors.append(f"{path}: 自查仅支持boolean additionalProperties")
            if "$ref" in value:
                try:
                    resolve_ref(document, value["$ref"])
                except (KeyError, ValueError, TypeError) as error:
                    errors.append(f"{path}: 引用无效 {error}")
            if "schema" in value and "example" in value:
                try:
                    validate_value(value["example"], value["schema"], document, path)
                except (KeyError, ValueError) as error:
                    errors.append(str(error))
            for key, item in value.items():
                next_schema = key == "schema" or (schema_context and key in ("items",))
                if key in ("properties", "schemas"):
                    for name, child in item.items():
                        walk(child, path + "." + key + "." + name, True)
                elif key == "anyOf":
                    for index, child in enumerate(item):
                        walk(child, f"{path}.anyOf[{index}]", True)
                else:
                    walk(item, path + "." + key, next_schema)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]", schema_context)

    walk(document)
    actual = set(routes())
    documented = {(path, method) for path, methods in document.get("paths", {}).items() for method in methods}
    if actual != documented:
        errors.append(f"路由覆盖不一致：未记录{sorted(actual-documented)}；幽灵操作{sorted(documented-actual)}")
    try:
        expected = build_document()
        if document != expected:
            errors.append("契约与当前安全/输入/响应定义不一致，请检查变动后执行write_student_contract。")
    except (ValueError, ImportError, AttributeError) as error:
        errors.append(str(error))
    return {"operationCount": len(documented), "routeCount": len(actual), "errors": errors}


def assert_response(response, path, method, document=None):
    document = document or load_document()
    path = urlsplit(path).path
    for template, methods in document["paths"].items():
        expression = re.escape(template)
        expression = re.sub(r"\\\{[^}]+\\\}", "[^/]+", expression)
        if re.fullmatch(expression, path):
            operation = methods[method.lower()]
            result = operation["responses"].get(str(response.status_code))
            if result is None:
                raise ValueError(f"{method} {path}: 未记录HTTP {response.status_code}")
            if response.status_code == 204:
                if response.content:
                    raise ValueError("204不能有响应体")
            else:
                validate_value(response.data, result["content"]["application/json"]["schema"], document)
            if not response.get("X-Request-ID"):
                raise ValueError("缺少X-Request-ID")
            return response
    raise ValueError(f"未记录路径 {path}")
