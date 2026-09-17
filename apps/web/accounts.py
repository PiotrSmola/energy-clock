from typing import Any

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.auth.views import LoginView, LogoutView
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.planner.models import Alert, AlertDelivery, Appliance, Household
from apps.web.forms import (
    AlertForm,
    EmailAuthenticationForm,
    HouseholdForm,
    RegistrationForm,
    UserApplianceForm,
)

RECENT_DELIVERIES = 10


class SignInView(LoginView):
    template_name = "account/login.html"
    authentication_form = EmailAuthenticationForm
    redirect_authenticated_user = True

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["nav"] = "account"
        return context


class SignOutView(LogoutView):
    pass


def signed_in_user(request: HttpRequest) -> User:
    user = request.user
    if not isinstance(user, User):
        raise PermissionDenied
    return user


def register(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("web:profile")
    form = RegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        messages.success(request, _("Welcome! Your account is ready."))
        return redirect("web:profile")
    return render(request, "account/register.html", {"form": form, "nav": "account"})


@login_required
def profile(request: HttpRequest) -> HttpResponse:
    user = signed_in_user(request)
    household, _created = Household.objects.get_or_create(user=user)
    household_form = HouseholdForm(instance=household)
    appliance_form = UserApplianceForm(user=user)
    alert_form = AlertForm()
    if request.method == "POST":
        section = request.POST.get("form", "")
        if section == "household":
            household_form = HouseholdForm(request.POST, instance=household)
            if household_form.is_valid():
                household_form.save()
                messages.success(request, _("Household saved."))
                return redirect("web:profile")
        elif section == "appliance":
            appliance_form = UserApplianceForm(request.POST, user=user)
            if appliance_form.is_valid():
                appliance = appliance_form.save(commit=False)
                appliance.user = user
                appliance.save()
                messages.success(request, _("Appliance added."))
                return redirect("web:profile")
        elif section == "alert":
            alert_form = AlertForm(request.POST)
            if alert_form.is_valid():
                alert = alert_form.save(commit=False)
                alert.user = user
                alert.save()
                messages.success(request, _("Alert added."))
                return redirect("web:profile")
    context = {
        "nav": "account",
        "household": household,
        "household_form": household_form,
        "appliance_form": appliance_form,
        "alert_form": alert_form,
        "appliances": Appliance.objects.filter(user=user),
        "alerts": Alert.objects.filter(user=user),
        "deliveries": AlertDelivery.objects.filter(alert__user=user).select_related("alert")[
            :RECENT_DELIVERIES
        ],
    }
    return render(request, "account/profile.html", context)


@login_required
@require_POST
def delete_appliance(request: HttpRequest, appliance_id: int) -> HttpResponseRedirect:
    appliance = get_object_or_404(Appliance, pk=appliance_id, user=signed_in_user(request))
    appliance.delete()
    messages.success(request, _("Appliance removed."))
    return redirect("web:profile")


@login_required
@require_POST
def toggle_alert(request: HttpRequest, alert_id: int) -> HttpResponseRedirect:
    alert = get_object_or_404(Alert, pk=alert_id, user=signed_in_user(request))
    alert.is_active = not alert.is_active
    alert.save(update_fields=["is_active"])
    messages.success(request, _("Alert enabled.") if alert.is_active else _("Alert paused."))
    return redirect("web:profile")


@login_required
@require_POST
def delete_alert(request: HttpRequest, alert_id: int) -> HttpResponseRedirect:
    alert = get_object_or_404(Alert, pk=alert_id, user=signed_in_user(request))
    alert.delete()
    messages.success(request, _("Alert removed."))
    return redirect("web:profile")
