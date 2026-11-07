from django.urls import path

from apps.assistant import views

app_name = "assistant"

urlpatterns = [
    path("assistant", views.assistant, name="assistant"),
    path("assistant/answer", views.assistant_answer, name="answer"),
    path("api/ask", views.api_ask, name="api_ask"),
]
