from rest_framework.parsers import JSONParser
from rest_framework.response import Response

from common.api import StudentAPIView

from .serializers import FeedbackInput
from .services import submit_feedback


class FeedbackCreateView(StudentAPIView):
    parser_classes = (JSONParser,)

    def post(self, request):
        serializer = FeedbackInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = submit_feedback(request.user, serializer.validated_data, request.headers.get("Idempotency-Key"))
        return Response(result, status=201, headers={"Cache-Control": "no-store, private"})
