"""Human-readable answer editing with lossless JSON support for multiline answers."""

import json

from django import forms
from unfold.widgets import UnfoldAdminTextareaWidget

from .models import Question
from .services import ContentConflict, check_draft_token, make_draft_token


class AnswerListField(forms.CharField):
    def prepare_value(self, value):
        if isinstance(value, list):
            if any("\n" in answer or "\r" in answer for answer in value) or (
                value and value[0].lstrip().startswith("[")
            ):
                return json.dumps(value, ensure_ascii=False, indent=2)
            return "\n".join(value)
        return value

    def clean(self, value):
        value = super().clean(value)
        if value.lstrip().startswith("["):
            try:
                answers = json.loads(value)
            except json.JSONDecodeError as error:
                raise forms.ValidationError("JSON 格式有误；也可以直接按每行一个答案填写。") from error
        else:
            answers = value.splitlines()
        if not isinstance(answers, list) or not answers or any(
            not isinstance(answer, str) or not answer.strip() for answer in answers
        ):
            raise forms.ValidationError("每个答案必须是非空文字，答案之间不要留空行。")
        return [answer.strip() for answer in answers]


class QuestionAdminForm(forms.ModelForm):
    draft_token = forms.CharField(required=False, widget=forms.HiddenInput)
    answers = AnswerListField(
        label="答案（按占位符顺序）",
        widget=UnfoldAdminTextareaWidget(attrs={"rows": 7}),
        help_text="每行一个答案：第 1 行对应 {{0}}，第 2 行对应 {{1}}。含换行的答案可以填写 JSON 数组。",
    )

    def __init__(self, *args, request=None, **kwargs):
        self.request = request
        super().__init__(*args, **kwargs)
        if request is not None and not self.is_bound and not self.instance._state.adding:
            self.initial["draft_token"] = make_draft_token(self.instance, request.user)

    def clean(self):
        data = super().clean()
        if self.request is not None:
            forced = getattr(self.request, "_draft_conflict", None)
            if forced:
                raise forms.ValidationError(forced)
            if not self.instance._state.adding:
                try:
                    current = Question.objects.get(pk=self.instance.pk)
                    check_draft_token(data.get("draft_token"), current, self.request.user)
                except (ContentConflict, Question.DoesNotExist) as error:
                    message = "；".join(error.messages) if isinstance(error, ContentConflict) else "题目已不可用，请重新打开题目列表。"
                    self.request._draft_conflict = message
                    raise forms.ValidationError(message) from error
        return data

    class Meta:
        model = Question
        fields = (
            "id", "source", "category", "article", "type", "tag", "sort_order", "stem", "answers"
        )
        help_texts = {
            "id": "仅使用英文字母、数字、下划线和连字符；保存后编号不能修改。",
            "category": "文学常识必选；文言题留空。",
            "article": "文言题必选；文学常识留空。",
        }
