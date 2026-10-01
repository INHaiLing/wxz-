"""Human-readable answer editing with lossless JSON support for multiline answers."""

import json

from django import forms
from unfold.widgets import UnfoldAdminTextareaWidget

from .models import Question


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
    answers = AnswerListField(
        label="答案（按占位符顺序）",
        widget=UnfoldAdminTextareaWidget(attrs={"rows": 7}),
        help_text="每行一个答案：第 1 行对应 {{0}}，第 2 行对应 {{1}}。含换行的答案可以填写 JSON 数组。",
    )

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
