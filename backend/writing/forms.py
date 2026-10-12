from django import forms
from unfold.widgets import UnfoldAdminTextareaWidget

from .models import WritingContent
from .services import ContentConflict, check_draft_token, make_draft_token


class WritingContentAdminForm(forms.ModelForm):
    draft_token = forms.CharField(required=False, widget=forms.HiddenInput)
    body = forms.CharField(label="正文", widget=UnfoldAdminTextareaWidget(attrs={"rows": 14}))
    reference_answer = forms.CharField(
        label="参考答案", required=False,
        widget=UnfoldAdminTextareaWidget(attrs={"rows": 8}),
        help_text="仅专项练习填写；支持换行。",
    )

    def __init__(self, *args, request=None, **kwargs):
        self.request = request
        super().__init__(*args, **kwargs)
        if request is not None and not self.is_bound and not self.instance._state.adding:
            self.initial["draft_token"] = make_draft_token(self.instance, request.user)

    def clean(self):
        data = super().clean()
        if self.request is not None and not self.instance._state.adding:
            forced = getattr(self.request, "_draft_conflict", None)
            if forced:
                raise forms.ValidationError(forced)
            try:
                current = WritingContent.objects.get(pk=self.instance.pk)
                check_draft_token(data.get("draft_token"), current, self.request.user)
            except (ContentConflict, WritingContent.DoesNotExist) as error:
                message = "；".join(error.messages) if isinstance(error, ContentConflict) else "应用文已不可用，请重新打开内容列表。"
                raise forms.ValidationError(message) from error
        return data

    class Meta:
        model = WritingContent
        fields = (
            "id", "category", "genre", "kind", "title", "body", "reference_answer", "sort_order",
        )
        help_texts = {
            "id": "仅使用英文字母、数字、下划线和连字符；保存后编号不能修改。",
            "kind": "专项练习必须同时填写参考答案。",
        }
