from django.urls import path

from .views import PullView, PushView, StatusView

urlpatterns = [
    path("pull/", PullView.as_view(), name="sync-pull"),
    path("push/", PushView.as_view(), name="sync-push"),
    path("status/", StatusView.as_view(), name="sync-status"),
]
