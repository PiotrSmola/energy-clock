from typing import Any

from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _

from apps.assistant.agent import MAX_QUESTION_CHARS, AssistantReply, ask
from apps.assistant.law import DEFAULT_ACT, LAW_ACTS

EXAMPLE_QUESTIONS = (
    _("My 2 kW washing machine runs for 2 hours and must finish by 7:00. When should I start it?"),
    _("What is the cheapest hour today and what is the price right now?"),
    _("Would a dynamic tariff pay off for 250 kWh a month at 0.62 PLN/kWh, used mostly at night?"),
    _("Does my supplier have to offer me a contract with a dynamic electricity price?"),
)


def question_from(request: HttpRequest) -> str:
    return request.GET.get("q", "").strip()[:MAX_QUESTION_CHARS]


def assistant_context(request: HttpRequest) -> dict[str, Any]:
    question = question_from(request)
    return {
        "nav": "assistant",
        "question": question,
        "reply": ask(question) if question else None,
        "examples": [str(example) for example in EXAMPLE_QUESTIONS],
        "act_title": LAW_ACTS[DEFAULT_ACT],
        "max_question_chars": MAX_QUESTION_CHARS,
    }


def assistant(request: HttpRequest) -> HttpResponse:
    return render(request, "assistant/assistant.html", assistant_context(request))


def assistant_answer(request: HttpRequest) -> HttpResponse:
    return render(request, "assistant/partials/answer.html", assistant_context(request))


def reply_payload(reply: AssistantReply) -> dict[str, Any]:
    return {
        "question": reply.question,
        "answer": reply.answer,
        "error": reply.error,
        "rejected": reply.rejected,
        "rewritten": reply.rewritten,
        "tool_calls": [call.as_json() for call in reply.tool_calls],
        "sources": [
            {"act": chunk.act_title, "article": chunk.article, "heading": chunk.heading}
            for chunk in reply.sources
        ],
        "duration_ms": reply.duration_ms,
        "prompt_tokens": reply.prompt_tokens,
        "completion_tokens": reply.completion_tokens,
    }


def api_ask(request: HttpRequest) -> HttpResponse:
    question = question_from(request)
    if not question:
        return HttpResponseBadRequest("q must not be empty")
    reply = ask(question)
    # 503: the model is not reachable; 502: it answered, but in a script that could not be shown
    status = 503 if reply.error else 502 if reply.rejected else 200
    return JsonResponse(
        reply_payload(reply), status=status, json_dumps_params={"ensure_ascii": False}
    )
