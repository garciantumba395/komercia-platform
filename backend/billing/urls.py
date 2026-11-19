from django.urls import path

from . import views

urlpatterns = [
    path("plans/", views.PlanListView.as_view()),
    path("subscription/", views.SubscriptionView.as_view()),
    path("installation/", views.InstallationView.as_view()),
    path("license/activate/", views.LicenseActivateView.as_view()),
    path("subscribe/", views.SubscribeView.as_view()),
    path("payments/", views.PaymentListView.as_view()),
    path("payments/<uuid:pk>/receipt/", views.PaymentReceiptView.as_view()),
    path("change-plan/", views.ChangePlanView.as_view()),
    path("change-plan/preview/", views.ChangePlanPreviewView.as_view()),
    path("cancel/", views.CancelView.as_view()),
    path("resume/", views.ResumeView.as_view()),
    path("notices/", views.NoticeListView.as_view()),
    path("notices/<uuid:pk>/read/", views.NoticeReadView.as_view()),
    path("sandbox/confirm/", views.SandboxConfirmView.as_view()),
    path("webhooks/<slug:provider>/", views.WebhookView.as_view()),
]
