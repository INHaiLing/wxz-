"""Student-facing applied-writing catalogue and detail endpoints."""

from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils.cache import patch_vary_headers
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from common.api import PublicStudentAPIView
from common.errors import BusinessError
from content.student_api import EmptyQuery
from content.models import LearningConfiguration
from content.serializers import validate_query_params

from .access import accessible_writings, free_writings, published_writings
from .models import WritingCategory, WritingGenre
from .serializers import WritingQuery, serialize_writing


class WritingPagination(PageNumberPagination):
    page_size_query_param = None
    last_page_strings = ()


class WritingView(PublicStudentAPIView):
    http_method_names = ("get", "head", "options")

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "private, no-store"
        patch_vary_headers(response, ("Authorization", "Cookie"))
        return response

    def _query(self, request):
        return validate_query_params(request.query_params, WritingQuery)

    def _paginate(self, request, queryset, page_size):
        paginator = WritingPagination()
        paginator.page_size = page_size
        page = paginator.paginate_queryset(queryset, request, view=self)
        response = paginator.get_paginated_response([serialize_writing(item) for item in page])
        return response


class WritingListView(WritingView):
    def get(self, request):
        query = self._query(request)
        category_id = query.get("categoryId")
        genre_id = query.get("genreId")
        kind = query.get("kind")
        if category_id:
            get_object_or_404(WritingCategory, pk=category_id, is_active=True)
        if genre_id:
            get_object_or_404(WritingGenre, pk=genre_id, is_active=True)

        config = LearningConfiguration.current()
        candidates = published_writings()
        if category_id:
            candidates = candidates.filter(published_revision__category_id=category_id)
        if genre_id:
            candidates = candidates.filter(published_revision__genre_id=genre_id)
        if kind:
            candidates = candidates.filter(published_revision__kind=kind)

        readable, activated = accessible_writings(request.user, candidates, page_size=config.page_size)
        total = candidates.count()
        free_count = free_writings(candidates, page_size=config.page_size).count()
        response = self._paginate(request, readable, config.page_size)
        response.data["access"] = {
            "activated": activated,
            "pageSize": config.page_size,
            "freeCount": free_count,
            "totalCount": total,
            "restrictedCount": max(0, total - readable.count()),
        }
        return response


class WritingDetailView(WritingView):
    def get(self, request, pk):
        validate_query_params(request.query_params, EmptyQuery)
        config = LearningConfiguration.current()
        published = published_writings()
        writing = published.filter(pk=pk).first()
        if writing is None:
            raise Http404
        readable, activated = accessible_writings(request.user, published.filter(pk=pk), page_size=config.page_size)
        if not readable.exists():
            raise BusinessError("ENTITLEMENT_REQUIRED", "请开通权益后继续学习。", status=403)
        data = serialize_writing(writing)
        data["access"] = {
            "activated": activated,
            "free": free_writings(published.filter(pk=pk), page_size=config.page_size).exists(),
            "pageSize": config.page_size,
        }
        return Response(data)
