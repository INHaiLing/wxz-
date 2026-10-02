import uuid

from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.views import APIView
from django.utils.cache import patch_vary_headers

from .errors import student_exception_handler


class StudentAPIView(APIView):
    permission_classes = (IsAuthenticated,)
    renderer_classes = (JSONRenderer,)

    def get_authenticators(self):
        # Lazy import lets the common infrastructure exist before P03 is registered.
        from accounts.authentication import StudentBearerAuthentication
        return [StudentBearerAuthentication()]

    def initial(self, request, *args, **kwargs):
        request.request_id = uuid.uuid4().hex
        return super().initial(request, *args, **kwargs)

    def get_exception_handler(self):
        return student_exception_handler

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["X-Request-ID"] = getattr(request, "request_id", uuid.uuid4().hex)
        response["Cache-Control"] = "no-store, private"
        patch_vary_headers(response, ("Authorization", "Cookie"))
        return response


class PublicStudentAPIView(StudentAPIView):
    permission_classes = (AllowAny,)
