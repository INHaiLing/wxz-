import hashlib
import secrets
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone

from accounts.models import StudentSession
from content.models import Article, Category, LearningConfiguration, Question, QuestionRevision


def publish(question):
    revision = QuestionRevision.objects.create(
        question=question, version=question.revisions.count() + 1, **question.content_payload(),
    )
    question.is_published = True
    question.published_revision = revision
    question.save()
    return revision


def make_question(pk, article, *, order=0, published=True):
    question = Question.objects.create(
        id=pk, source="classical", type="word", article=article,
        stem=f"{pk}题干{{{{0}}}}。", answers=[f"{pk}收费答案"], sort_order=order,
    )
    if published:
        publish(question)
    return question


def make_session(user):
    token = secrets.token_urlsafe(32)
    StudentSession.objects.create(
        user=user, token_digest=hashlib.sha256(token.encode()).hexdigest(),
        encrypted_session_key="learning-fixture-not-decrypted",
        expires_at=timezone.now() + timedelta(days=7),
    )
    return token


def build_fixture(test):
    test.student = get_user_model().objects.create_user(username="learning-student")
    test.other_student = get_user_model().objects.create_user(username="learning-other")
    test.operator = get_user_model().objects.create_superuser(username="learning-operator", password="test-only-password")
    test.article = Article.objects.create(id="quanxue", title="劝学")
    test.other_article = Article.objects.create(id="shishuo", title="师说")
    test.empty_article = Article.objects.create(id="empty", title="空篇目")
    test.category = Category.objects.create(id="literature", name="文学常识")
    test.config = LearningConfiguration.objects.create(page_size=2, daily_target=27)
    test.first = make_question("first", test.article, order=1)
    test.second = make_question("second", test.article, order=2)
    test.paid = make_question("paid", test.article, order=3)
    test.other = make_question("other", test.other_article, order=1)
    test.draft = make_question("draft", test.article, published=False)
    test.token = make_session(test.student)
