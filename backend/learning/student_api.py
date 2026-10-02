from django.utils.cache import patch_vary_headers
from rest_framework import serializers
from rest_framework.pagination import PageNumberPagination
from rest_framework.parsers import JSONParser
from rest_framework.response import Response

from common.api import StudentAPIView
from content.access import accessible_questions, published_questions
from content.models import LearningConfiguration
from content.serializers import PublishedQuestionSerializer, StrictPositiveIntegerField, validate_query_params
from .models import QuestionState
from .serializers import PreferenceInput, QuestionStateInput
from .services import preference_snapshot, read_question_state, state_snapshot, statistics, update_preferences, update_question_state


class NoQuery(serializers.Serializer):
    pass


class FavoriteQuery(serializers.Serializer):
    page = StrictPositiveIntegerField(min_value=1, max_value=1000000, required=False)


class LearningAPIView(StudentAPIView):
    parser_classes = (JSONParser,)

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "private, no-store"
        patch_vary_headers(response, ("Authorization", "Cookie"))
        return response


class QuestionStateView(LearningAPIView):
    def get(self, request, pk):
        validate_query_params(request.query_params, NoQuery)
        return Response(read_question_state(request.user, pk))

    def put(self, request, pk):
        validate_query_params(request.query_params, NoQuery)
        serializer = QuestionStateInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(update_question_state(request.user, pk, serializer.validated_data, request.headers.get("Idempotency-Key")))


class PreferenceView(LearningAPIView):
    def get(self, request):
        validate_query_params(request.query_params, NoQuery)
        return Response(preference_snapshot(request.user))

    def put(self, request):
        validate_query_params(request.query_params, NoQuery)
        serializer = PreferenceInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(update_preferences(request.user, serializer.validated_data, request.headers.get("Idempotency-Key")))


class StatisticsView(LearningAPIView):
    def get(self, request):
        validate_query_params(request.query_params, NoQuery)
        return Response(statistics(request.user))


class FavoritePagination(PageNumberPagination):
    page_size_query_param = None
    last_page_strings = ()


class FavoritesView(LearningAPIView):
    def get(self, request):
        validate_query_params(request.query_params, FavoriteQuery)
        config = LearningConfiguration.current()
        states = QuestionState.objects.filter(user=request.user, favorite=True).order_by(
            "question__published_revision__sort_order", "question_id",
        )
        paginator = FavoritePagination()
        paginator.page_size = config.page_size
        page = paginator.paginate_queryset(states, request, view=self)
        ids = [state.question_id for state in page]
        published = published_questions().filter(pk__in=ids)
        visible_ids = set(published.values_list("pk", flat=True))
        readable = {obj.pk: obj for obj in accessible_questions(request.user, published, page_size=config.page_size)}
        results = []
        for state in page:
            question = readable.get(state.question_id)
            results.append({
                "state": state_snapshot(state, state.question_id),
                "question": PublishedQuestionSerializer(question).data if question else None,
                "unavailableReason": None if question else (
                    "ENTITLEMENT_REQUIRED" if state.question_id in visible_ids else "CONTENT_UNAVAILABLE"
                ),
            })
        return paginator.get_paginated_response(results)
