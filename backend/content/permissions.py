"""Content access policy shared by every read API.

The first version is for staff only. Student entitlements can later be added
here without duplicating access decisions across the content endpoints.
"""

from rest_framework.permissions import BasePermission


def can_read_content(user, model_name):
    """Require an active staff account and the specific model view permission."""
    if model_name not in {"category", "article", "question"}:
        return False
    return bool(
        user
        and user.is_authenticated
        and user.is_active
        and user.is_staff
        and user.has_perm(f"content.view_{model_name}")
    )


class StaffContentReadPermission(BasePermission):
    message = "需要启用的后台账号及对应的内容查看权限。"

    def has_permission(self, request, view):
        return can_read_content(request.user, getattr(view, "permission_model", None))
