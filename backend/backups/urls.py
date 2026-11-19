from django.urls import path

from . import views

urlpatterns = [
    path("status/", views.StatusView.as_view()),
    path("records/", views.RecordListView.as_view()),
    path("run/", views.RunView.as_view()),
    path("settings/", views.SettingsView.as_view()),
]
