from django import forms
from unfold.widgets import UnfoldAdminIntegerFieldWidget, UnfoldAdminTextInputWidget


class GenerateBatchForm(forms.Form):
    quantity = forms.IntegerField(label="数量", min_value=1, max_value=500, initial=10, widget=UnfoldAdminIntegerFieldWidget)
    label = forms.CharField(label="用途说明", max_length=120, required=False, widget=UnfoldAdminTextInputWidget)
    idempotency_key = forms.CharField(widget=forms.HiddenInput)
