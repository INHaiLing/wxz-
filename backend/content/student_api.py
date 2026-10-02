"""Read-only student content; protected answers never depend on client masking."""

from django.db.models import Count, F, IntegerField, OuterRef, Subquery, Value
from django.db.models.functions import Coalesce, Least
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils.cache import patch_vary_headers
from rest_framework import serializers
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from common.api import PublicStudentAPIView
from common.errors import BusinessError
from .access import free_questions, published_questions, question_access, scope_questions
from .models import Article, Category, LearningConfiguration
from .serializers import PublishedQuestionSerializer, StrictPositiveIntegerField, validate_query_params


class EmptyQuery(serializers.Serializer):
    pass


class StudentPageQuery(serializers.Serializer):
    page = StrictPositiveIntegerField(min_value=1, max_value=1000000, required=False)


class StudentQuestionQuery(StudentPageQuery):
    source = serializers.ChoiceField(choices=("literature", "classical"))
    categoryId = serializers.SlugField(max_length=64, required=False)
    articleId = serializers.SlugField(max_length=64, required=False)
    type = serializers.ChoiceField(choices=("fact", "word", "translation"), required=False)

    def validate(self, values):
        source = values["source"]
        required = "categoryId" if source == "literature" else "articleId"
        excluded = "articleId" if source == "literature" else "categoryId"
        if required not in values or excluded in values:
            raise serializers.ValidationError({required: "必须指定与来源一致的唯一分类／篇目。"})
        if source == "literature" and values.get("type", "fact") != "fact":
            raise serializers.ValidationError({"type": "文学分类只支持 fact。"})
        return values


class StudentContentPagination(PageNumberPagination):
    page_size_query_param = None
    last_page_strings = ()


class StudentContentView(PublicStudentAPIView):
    http_method_names = ("get", "head", "options")

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "private, no-store"
        patch_vary_headers(response, ("Authorization", "Cookie"))
        return response

    def paginated(self, request, queryset, page_size, serializer):
        paginator = StudentContentPagination()
        paginator.page_size = page_size
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(serializer(page))


class LearningConfigView(StudentContentView):
    def get(self, request):
        validate_query_params(request.query_params, EmptyQuery)
        config = LearningConfiguration.current()
        return Response({
            "pageSize": config.page_size,
            "dailyTarget": config.daily_target,
            "examDate": config.exam_date.isoformat() if config.exam_date else None,
        })


class StudentCatalogView(StudentContentView):
    model = None
    revision_field = None
    label_field = None

    def get(self, request):
        validate_query_params(request.query_params, StudentPageQuery)
        config = LearningConfiguration.current()
        counts = published_questions().filter(**{self.revision_field: OuterRef("pk")}).order_by().values(
            self.revision_field,
        ).annotate(total=Count("pk")).values("total")[:1]
        queryset = self.model.objects.filter(is_active=True).annotate(
            published_count=Coalesce(Subquery(counts, output_field=IntegerField()), Value(0)),
        ).annotate(free_count=Least(F("published_count"), Value(config.page_size))).order_by("sort_order", "pk")

        def serialize(page):
            return [{
                "id": item.pk,
                self.label_field: getattr(item, self.label_field),
                "sortOrder": item.sort_order,
                "publishedCount": item.published_count,
                "freeCount": item.free_count,
            } for item in page]

        return self.paginated(request, queryset, config.page_size, serialize)


class StudentCategoryListView(StudentCatalogView):
    model = Category
    revision_field = "published_revision__category_id"
    label_field = "name"


class StudentArticleListView(StudentCatalogView):
    model = Article
    revision_field = "published_revision__article_id"
    label_field = "title"


class StudentQuestionListView(StudentContentView):
    def get(self, request):
        query = validate_query_params(request.query_params, StudentQuestionQuery)
        source = query["source"]
        scope_id = query["categoryId"] if source == "literature" else query["articleId"]
        model = Category if source == "literature" else Article
        get_object_or_404(model, pk=scope_id, is_active=True)
        config = LearningConfiguration.current()
        queryset = scope_questions(source, scope_id)
        if "type" in query:
            queryset = queryset.filter(published_revision__type=query["type"])
        total = queryset.count()
        free_count = free_questions(queryset, page_size=config.page_size).count()
        readable, activated = question_access(request.user, queryset, page_size=config.page_size)
        response = self.paginated(
            request, readable, config.page_size,
            lambda page: PublishedQuestionSerializer(page, many=True).data,
        )
        response.data["access"] = {
            "activated": activated,
            "pageSize": config.page_size,
            "freeCount": free_count,
            "totalCount": total,
            "restrictedCount": max(0, total - response.data["count"]),
        }
        return response


class StudentQuestionDetailView(StudentContentView):
    def get(self, request, pk):
        validate_query_params(request.query_params, EmptyQuery)
        config = LearningConfiguration.current()
        readable, activated = question_access(
            request.user, published_questions(), page_size=config.page_size,
        )
        question = readable.filter(pk=pk).first()
        if question is None:
            # Distinguish a published paid question from unpublished/nonexistent
            # without serializing its content into an error response.
            if not published_questions().filter(pk=pk).exists():
                raise Http404
            raise BusinessError("ENTITLEMENT_REQUIRED", "请开通权益后继续学习。", status=403)
        data = dict(PublishedQuestionSerializer(question).data)
        data["access"] = {
            "activated": activated,
            "free": free_questions(published_questions().filter(pk=pk), page_size=config.page_size).exists(),
            "pageSize": config.page_size,
        }
        return Response(data)
