from django.utils.cache import patch_vary_headers
from rest_framework.parsers import JSONParser
from rest_framework.response import Response

from common.api import StudentAPIView
from common.idempotency import require_key
from common.limits import check_rate
from content.serializers import validate_query_params
from content.student_api import EmptyQuery
from .serializers import ResumeRequest, RoundRequest
from .services import create_round, read_group, resume_snapshot, save_resume


class PracticeView(StudentAPIView):
    parser_classes = (JSONParser,)
    http_method_names = ("get", "post", "put", "head", "options")

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        validate_query_params(request.query_params, EmptyQuery)

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "private, no-store"
        patch_vary_headers(response, ("Authorization", "Cookie"))
        return response


class RoundCreateView(PracticeView):
    def post(self, request):
        check_rate("practice.round.create", str(request.user.pk), limit=10)
        key = require_key(request.headers.get("Idempotency-Key"))
        serializer = RoundRequest(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(create_round(request.user, serializer.validated_data, key), status=201)


class RoundGroupView(PracticeView):
    def get(self, request, round_id, group_index):
        check_rate("practice.round.read", str(request.user.pk), limit=120)
        return Response(read_group(request.user, round_id, group_index))


class ResumeView(PracticeView):
    def get(self, request):
        return Response(resume_snapshot(request.user))

    def put(self, request):
        check_rate("practice.resume.save", str(request.user.pk), limit=60)
        key = require_key(request.headers.get("Idempotency-Key"))
        serializer = ResumeRequest(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(save_resume(request.user, serializer.validated_data, key))
