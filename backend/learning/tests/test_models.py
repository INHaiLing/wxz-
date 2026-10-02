from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import RequestFactory, TestCase

from learning.admin import LearningPreferenceAdmin, QuestionStateAdmin
from learning.models import LearningActivity, LearningPreference, QuestionState
from .fixtures import build_fixture


class LearningPersistenceTests(TestCase):
    def setUp(self):
        build_fixture(self)

    def test_database_uniqueness_and_preference_validation_prevent_bulk_bypass(self):
        QuestionState.objects.create(user=self.student, question=self.first)
        LearningActivity.objects.create(user=self.student, question=self.first, study_date="2026-10-02")
        LearningPreference.objects.create(user=self.student)
        for model, values in (
            (QuestionState, {"user": self.student, "question": self.first}),
            (LearningActivity, {"user": self.student, "question": self.first, "study_date": "2026-10-02"}),
            (LearningPreference, {"user": self.student}),
        ):
            with self.assertRaises(IntegrityError), transaction.atomic():
                model.objects.create(**values)
        for values in ({"mode": "other"}, {"daily_target": 0}, {"daily_target": 1001}):
            with self.assertRaises(IntegrityError), transaction.atomic():
                LearningPreference.objects.filter(user=self.student).update(**values)

    def test_learning_admin_is_readonly_even_for_superuser_and_hides_draft_object_repr(self):
        request = RequestFactory().get("/admin/")
        request.user = self.operator
        for model, admin_class in ((QuestionState, QuestionStateAdmin), (LearningPreference, LearningPreferenceAdmin)):
            admin = admin_class(model, AdminSite())
            self.assertFalse(admin.has_add_permission(request))
            self.assertFalse(admin.has_change_permission(request))
            self.assertFalse(admin.has_delete_permission(request))
        state_admin = QuestionStateAdmin(QuestionState, AdminSite())
        self.assertIn("question_id", state_admin.readonly_fields)
        self.assertNotIn("question", state_admin.readonly_fields)
