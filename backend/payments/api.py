from rest_framework import serializers
from rest_framework.parsers import JSONParser
from rest_framework.response import Response
from common.api import StudentAPIView
from common.errors import BusinessError
from common.idempotency import require_key
from common.limits import check_rate
from content.models import LearningConfiguration
from .models import Order
from .services import create_order, order_payload, prepare_payment
from .synchronization import query_payment
from .protocol import MAX_BYTES, invalid, parse_payload


class StrictInput(serializers.Serializer):
    def to_internal_value(self,data):
        if not isinstance(data,dict) or set(data)-set(self.fields):
            raise serializers.ValidationError({'fields':'请求只允许契约中的 JSON 字段。'})
        return super().to_internal_value(data)


class CreateInput(StrictInput):
    productId=serializers.RegexField(r'^[A-Za-z0-9_-]{1,64}$')
    channel=serializers.ChoiceField(choices=('android','ios'))


def _query(request,allow_page=False):
    allowed={'page'} if allow_page else set()
    if set(request.query_params)-allowed or any(len(request.query_params.getlist(k))!=1 for k in request.query_params):
        raise BusinessError('INVALID_QUERY','查询参数无效。')
    page=request.query_params.get('page','1')
    if len(page)>6 or not page.isascii() or not page.isdecimal() or not 1<=int(page)<=100000:
        raise BusinessError('INVALID_QUERY','页码无效。')
    return int(page)


class OrderListView(StudentAPIView):
    parser_classes=(JSONParser,)

    def post(self,request):
        _query(request)
        check_rate('order-create',str(request.user.pk),10)
        data=CreateInput(data=request.data); data.is_valid(raise_exception=True)
        result=create_order(request.user,request.auth,data.validated_data['productId'],data.validated_data['channel'],require_key(request.headers.get('Idempotency-Key')))
        return Response(result,status=201,headers={'Cache-Control':'no-store, private'})

    def get(self,request):
        page=_query(request,True); capacity=LearningConfiguration.current().page_size
        queryset=Order.objects.filter(user=request.user).order_by('-created_at','id')
        count=queryset.count(); offset=(page-1)*capacity
        if page>1 and offset>=count: raise BusinessError('NOT_FOUND','页码不存在。',404)
        return Response({'count':count,'next':f'?page={page+1}' if offset+capacity<count else None,
            'previous':f'?page={page-1}' if page>1 else None,'results':[order_payload(o) for o in queryset[offset:offset+capacity]]},headers={'Cache-Control':'no-store, private'})


class OrderDetailView(StudentAPIView):
    def get(self,request,order_id):
        _query(request)
        try: order=Order.objects.get(pk=order_id,user=request.user)
        except Order.DoesNotExist as error: raise BusinessError('NOT_FOUND','订单不存在。',404) from error
        return Response(order_payload(order),headers={'Cache-Control':'no-store, private'})


class PaymentPrepareView(StudentAPIView):
    parser_classes=(JSONParser,)
    def post(self,request,order_id):
        _query(request)
        check_rate('payment-prepare',str(request.user.pk),10)
        data=StrictInput(data=request.data); data.is_valid(raise_exception=True)
        return Response(prepare_payment(request.user,request.auth,order_id,require_key(request.headers.get('Idempotency-Key'))),headers={'Cache-Control':'no-store, private'})


class QueryJSONParser(JSONParser):
    def parse(self, stream, media_type=None, parser_context=None):
        raw = stream.read(MAX_BYTES + 1)
        if not raw.lstrip().startswith(b"{"):
            raise invalid()
        return parse_payload(raw)


class PaymentQueryView(StudentAPIView):
    parser_classes = (QueryJSONParser,)

    def post(self, request, order_id):
        _query(request)
        check_rate("payment-query", request.user.pk, 10)
        data = StrictInput(data=request.data)
        data.is_valid(raise_exception=True)
        result = query_payment(request.user, order_id, require_key(request.headers.get("Idempotency-Key")))
        return Response(result, headers={"Cache-Control": "no-store, private"})
