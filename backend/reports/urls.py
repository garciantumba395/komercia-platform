from django.urls import path

from .views import DailyReportView, ExportView, SummaryReportView

urlpatterns = [
    path("daily/", DailyReportView.as_view(), name="reports-daily"),
    path("summary/", SummaryReportView.as_view(), name="reports-summary"),
    path("export/", ExportView.as_view(), name="reports-export"),
]
