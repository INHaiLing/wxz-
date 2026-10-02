from copy import deepcopy
from io import StringIO

from django.core.management import call_command
from django.test import SimpleTestCase

from contracts.validation import load_document, validate_document, validate_value
from contracts.document import import_serializer


class ContractStructureTests(SimpleTestCase):
    def test_actual_routes_authentication_inputs_and_examples_match_versioned_contract(self):
        report = validate_document(load_document())
        self.assertEqual(report["errors"], [])
        self.assertGreaterEqual(report["operationCount"], 15)
        output = StringIO()
        call_command("check_student_contract", stdout=output)
        self.assertIn("路由完全覆盖", output.getvalue())

    def test_missing_path_wrong_security_or_changed_enum_is_detected(self):
        original = load_document()
        candidates = [deepcopy(original) for _ in range(3)]
        del candidates[0]["paths"]["/api/student/v1/me/preferences/"]
        candidates[1]["paths"]["/api/student/v1/config/"]["get"]["security"] = [{"studentBearer": []}]
        candidates[2]["paths"]["/api/student/v1/me/preferences/"]["put"]["requestBody"]["content"]["application/json"]["schema"]["properties"]["mode"]["enum"] = ["recite"]
        for document in candidates:
            self.assertTrue(validate_document(document)["errors"])

    def test_invalid_reference_and_unsupported_schema_keyword_are_not_silently_ignored(self):
        document = load_document()
        document["components"]["schemas"]["Broken"] = {"$ref": "#/components/schemas/missing"}
        self.assertTrue(any("引用无效" in error for error in validate_document(document)["errors"]))
        document = load_document()
        document["components"]["schemas"]["QuestionState"]["unsupportedAssertion"] = True
        self.assertTrue(any("尚不支持" in error for error in validate_document(document)["errors"]))

    def test_documented_request_examples_pass_actual_serializer_validation(self):
        document = load_document()
        for methods in document["paths"].values():
            for operation in methods.values():
                if not operation["x-request-serializer"]:
                    continue
                serializer = type(import_serializer(operation["x-request-serializer"]))(data=
                    operation["requestBody"]["content"]["application/json"]["example"])
                self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_response_validation_rejects_type_drift_extra_private_content_and_bad_dates(self):
        document = load_document()
        state = document["components"]["schemas"]["QuestionState"]
        for value in (
            {"questionId": "first", "favorite": "true", "mastered": False, "version": 1},
            {"questionId": "first", "favorite": True, "mastered": False, "version": True},
            {"questionId": "first", "favorite": True, "mastered": False, "version": 1, "stem": "protected"},
        ):
            with self.assertRaises(ValueError):
                validate_value(value, state, document)
        with self.assertRaises(ValueError):
            validate_value("no-date", {"type": "string", "format": "date-time"}, document)
