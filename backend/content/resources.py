"""Draft-only, transactional question imports with the import-export preview engine."""

from collections import Counter
import hashlib
import json
import re

from django.core.exceptions import ValidationError
from django.db import transaction
from import_export import fields, resources, widgets
from import_export.results import Error, Result

from .models import Article, Category, Question


MAX_IMPORT_ROWS = 5000
MAX_ANSWER_COLUMNS = 32
DEFAULT_ANSWER_COLUMNS = 7
BASE_HEADERS = (
    "题目ID", "来源", "分类ID", "篇目ID", "题型", "标签", "排序", "题干",
)
DRAFT_FIELDS = (
    "id", "source", "category_id", "article_id", "type", "tag", "sort_order",
    "stem", "answers",
)
SNAPSHOT_FIELDS = DRAFT_FIELDS + ("updated_at", "is_published", "published_revision_id")
ANSWER_HEADER = re.compile(r"答案([1-9][0-9]*)\Z")


def question_fingerprint(record):
    return hashlib.sha256(
        json.dumps(record, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def import_permission(user):
    return bool(
        user and user.is_active and user.is_staff
        and user.has_perm("content.change_question")
        and user.has_perm("content.import_question")
    )


class TextWidget(widgets.CharWidget):
    def __init__(self, *, strip=False, **kwargs):
        self.strip = strip
        super().__init__(**kwargs)

    def clean(self, value, row=None, **kwargs):
        text = super().clean(value, row=row, **kwargs)
        return text.strip() if self.strip else text


class OrderWidget(widgets.IntegerWidget):
    def clean(self, value, row=None, **kwargs):
        if value in (None, ""):
            return 0
        if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value).strip()):
            raise ValueError("排序必须为非负整数。")
        return int(value)


class ReferenceWidget(widgets.ForeignKeyWidget):
    def clean(self, value, row=None, **kwargs):
        try:
            return super().clean(value, row=row, **kwargs)
        except self.model.DoesNotExist as exc:
            raise ValueError(f"找不到{self.model._meta.verbose_name} ID：{value}；请参考模板中的“分类与篇目”表。") from exc


class AnswerField(fields.Field):
    """One visible spreadsheet column; aggregate assignment happens in import_instance."""

    def __init__(self, index):
        self.index = index
        super().__init__(
            attribute="answers", column_name=f"答案{index + 1}",
            widget=TextWidget(), readonly=True,
        )

    def get_value(self, instance):
        answers = instance.answers or []
        return answers[self.index] if len(answers) > self.index else ""


class QuestionResource(resources.ModelResource):
    id = fields.Field(attribute="id", column_name="题目ID", widget=TextWidget(strip=True))
    source = fields.Field(attribute="source", column_name="来源", widget=TextWidget(strip=True))
    category = fields.Field(
        attribute="category", column_name="分类ID", widget=ReferenceWidget(Category, "pk")
    )
    article = fields.Field(
        attribute="article", column_name="篇目ID", widget=ReferenceWidget(Article, "pk")
    )
    type = fields.Field(attribute="type", column_name="题型", widget=TextWidget(strip=True))
    tag = fields.Field(attribute="tag", column_name="标签", widget=TextWidget())
    sort_order = fields.Field(attribute="sort_order", column_name="排序", widget=OrderWidget())
    stem = fields.Field(attribute="stem", column_name="题干", widget=TextWidget())

    class Meta:
        model = Question
        name = "题目草稿（不会发布）"
        fields = ("id", "source", "category", "article", "type", "tag", "sort_order", "stem")
        import_id_fields = ("id",)
        use_transactions = True
        clean_model_instances = True
        skip_unchanged = True
        report_skipped = True
        store_instance = True
        use_bulk = False

    def __init__(self, *, user=None, request=None, expected_versions=None, **kwargs):
        super().__init__(**kwargs)
        self.user = user
        self.request = request
        self.expected_versions = expected_versions
        self._versions = {}
        self._duplicates = set()
        self._set_answer_columns(DEFAULT_ANSWER_COLUMNS)

    def _set_answer_columns(self, count):
        self.fields = {
            key: field for key, field in self.fields.items() if not key.startswith("answer_")
        }
        self.answer_count = count
        for index in range(count):
            self.fields[f"answer_{index + 1}"] = AnswerField(index)

    def _prepare_headers(self, dataset):
        headers = dataset.headers or []
        if len(headers) != len(set(headers)):
            raise ValidationError("表头重复，请使用模板中的唯一列名。")
        if not 0 < len(dataset) <= MAX_IMPORT_ROWS:
            raise ValidationError(f"题库必须包含 1–{MAX_IMPORT_ROWS} 行数据；空白模板请先填写。")
        missing = set(BASE_HEADERS) - set(headers)
        if missing:
            raise ValidationError("缺少必填列：" + "、".join(sorted(missing)))
        unknown = [header for header in headers if header not in BASE_HEADERS and not ANSWER_HEADER.fullmatch(str(header))]
        if unknown:
            raise ValidationError("不允许导入的列：" + "、".join(map(str, unknown)) + "。发布状态、版本和权限只能由后台操作。")
        index_strings = [ANSWER_HEADER.fullmatch(header)[1] for header in headers if ANSWER_HEADER.fullmatch(header)]
        if any(len(index) > len(str(MAX_ANSWER_COLUMNS)) for index in index_strings):
            raise ValidationError(f"答案列编号不能超过 {MAX_ANSWER_COLUMNS}。")
        indexes = sorted(int(index) for index in index_strings)
        if not indexes or max(indexes) > MAX_ANSWER_COLUMNS or indexes != list(range(1, max(indexes) + 1)):
            raise ValidationError(f"答案列须从 答案1 连续编号，最多 {MAX_ANSWER_COLUMNS} 列。")
        self._set_answer_columns(max(indexes))
        ids = [str(row.get("题目ID") or "").strip() for row in dataset.dict]
        self._duplicates = {key for key, count in Counter(ids).items() if key and count > 1}
        return ids

    def import_data(self, dataset, dry_run=False, raise_errors=False, **kwargs):
        """Force whole-batch rollback even if callers omit import-export's rollback flag."""
        try:
            ids = self._prepare_headers(dataset)
            self.user = self.user or kwargs.get("user")
            if not import_permission(self.user):
                raise ValidationError("需要有效管理员身份、题目编辑和题目导入权限。")
            with transaction.atomic():
                queryset = Question.objects.filter(pk__in=ids).order_by("pk")
                if not dry_run:
                    queryset = queryset.select_for_update()
                existing = {row["id"]: question_fingerprint(row) for row in queryset.values(*SNAPSHOT_FIELDS)}
                self._versions = {key: existing.get(key) for key in ids}
                if self.expected_versions is not None and self._versions != self.expected_versions:
                    changed = [key for key in ids if self._versions.get(key) != self.expected_versions.get(key)]
                    raise ValidationError("预览后题目发生新增、修改、发布或删除，请重新上传预检。冲突 ID：" + "、".join(changed[:10]))
                if self.request is not None:
                    self.request._question_import_versions = self._versions
                kwargs.pop("use_transactions", None)
                kwargs.pop("rollback_on_validation_errors", None)
                return super().import_data(
                    dataset, dry_run=dry_run, raise_errors=raise_errors,
                    use_transactions=True, rollback_on_validation_errors=True, **kwargs,
                )
        except ValidationError as exc:
            if raise_errors:
                raise
            result = Result()
            result.total_rows = len(dataset)
            result.diff_headers = self.get_diff_headers()
            result.append_base_error(Error(exc))
            return result

    def before_import_row(self, row, **kwargs):
        key = str(row.get("题目ID") or "").strip()
        row["题目ID"] = key
        if not key:
            raise ValidationError({"题目ID": "稳定 ID 必填；新增请填写新的唯一 ID，不能按题干猜测更新。"})
        if key in self._duplicates:
            raise ValidationError({"题目ID": f"文件内 ID 重复：{key}，请保留一行。"})
        if self._versions.get(key) is None and not self.user.has_perm("content.add_question"):
            raise ValidationError({"题目ID": "此行是新增题目，当前管理员没有新增题目权限。"})
        for header in ("来源", "题型", "分类ID", "篇目ID"):
            row[header] = str(row.get(header) or "").strip()

    def get_or_init_instance(self, instance_loader, row):
        if self._versions.get(row["题目ID"]) is None:
            return self.init_instance(row), True
        instance = self.get_instance(instance_loader, row)
        if instance is None:
            raise ValidationError("题目在导入期间被删除，请重新预检。")
        return instance, False

    def import_instance(self, instance, row, **kwargs):
        super().import_instance(instance, row, **kwargs)
        answers = [row.get(f"答案{index + 1}") for index in range(self.answer_count)]
        while answers and answers[-1] in (None, ""):
            answers.pop()
        if any(answer is None or not str(answer).strip() for answer in answers):
            raise ValidationError({"answers": "答案列中间不能留空；请与题干的 {{0}}、{{1}} 等顺序对应。"})
        instance.answers = [str(answer) for answer in answers]

    def skip_row(self, instance, original, row, import_validation_errors=None):
        if import_validation_errors:
            return False
        instance.full_clean()
        return bool(
            original and not original._state.adding
            and all(getattr(instance, field) == getattr(original, field) for field in DRAFT_FIELDS)
        )

    def do_instance_save(self, instance, is_create):
        # A new stable ID must never fall back to UPDATE if another writer inserts it.
        instance.save(force_insert=is_create, force_update=not is_create)

    def export(self, queryset=None, **kwargs):
        queryset = queryset if queryset is not None else self.get_queryset()
        largest = max((len(answers or []) for answers in queryset.values_list("answers", flat=True)), default=0)
        self._set_answer_columns(max(DEFAULT_ANSWER_COLUMNS, largest))
        return super().export(queryset=queryset, **kwargs)
