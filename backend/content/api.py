"""Read-only staff API exposing active catalogs and published question snapshots."""

from django.db.models import F, Q
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from rest_framework.authentication import SessionAuthentication
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.pagination import PageNumberPagination

from .models import Article, Category, Question
from .permissions import StaffContentReadPermission
from .serializers import (
    ArticleSerializer,
    CategorySerializer,
    PaginationQuerySerializer,
    PublishedQuestionSerializer,
    QuestionQuerySerializer,
    validate_query_params,
)


class ContentPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100
    last_page_strings = ()


class ProtectedContentView:
    authentication_classes = (SessionAuthentication,)
    permission_classes = (StaffContentReadPermission,)
    pagination_class = ContentPagination
    http_method_names = ("get", "head", "options")


class CategoryListView(ProtectedContentView, ListAPIView):
    permission_model = "category"
    serializer_class = CategorySerializer

    def get_queryset(self):
        validate_query_params(self.request.query_params, PaginationQuerySerializer)
        return Category.objects.filter(is_active=True).order_by("sort_order", "pk")


class ArticleListView(ProtectedContentView, ListAPIView):
    permission_model = "article"
    serializer_class = ArticleSerializer

    def get_queryset(self):
        validate_query_params(self.request.query_params, PaginationQuerySerializer)
        return Article.objects.filter(is_active=True).order_by("sort_order", "pk")


class PublishedQuestionView(ProtectedContentView):
    permission_model = "question"
    serializer_class = PublishedQuestionSerializer

    def get_queryset(self):
        query = validate_query_params(self.request.query_params, QuestionQuerySerializer)
        queryset = Question.objects.filter(
            is_published=True,
            published_revision__isnull=False,
            published_revision__question_id=F("pk"),
        ).filter(
            Q(published_revision__category__isnull=True)
            | Q(published_revision__category__is_active=True),
            Q(published_revision__article__isnull=True)
            | Q(published_revision__article__is_active=True),
        ).select_related("published_revision")

        fields = {
            "source": "published_revision__source",
            "categoryId": "published_revision__category_id",
            "articleId": "published_revision__article_id",
            "type": "published_revision__type",
        }
        for parameter, field in fields.items():
            if parameter in query:
                queryset = queryset.filter(**{field: query[parameter]})
        return queryset.order_by("published_revision__sort_order", "pk")


class QuestionListView(PublishedQuestionView, ListAPIView):
    pass


class QuestionDetailView(PublishedQuestionView, RetrieveAPIView):
    pass


@require_GET
def healthz(request):
    """Process liveness only: deliberately contains no settings or database data."""
    return JsonResponse({"status": "ok"})
