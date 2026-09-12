from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView as BaseLoginView
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views.generic import FormView

from .forms import ProfileForm, StyledAuthenticationForm, StyledUserCreationForm
from .models import Profile


class LoginView(BaseLoginView):
    template_name = "accounts/login.html"
    authentication_form = StyledAuthenticationForm
    redirect_authenticated_user = True


class RegisterView(FormView):
    template_name = "accounts/register.html"
    form_class = StyledUserCreationForm
    success_url = reverse_lazy("home:index")

    def form_valid(self, form):
        user = form.save()
        login(self.request, user)
        return super().form_valid(form)


@login_required
def profile(request):
    """The whole app otherwise requires login via LoginRequiredMiddleware,
    but that middleware exempts everything under /accounts/ (so the login/
    register pages themselves stay reachable while logged out) — this page
    lives under that same prefix, so it needs its own @login_required."""
    profile_obj, _ = Profile.objects.get_or_create(user=request.user)

    if request.method == "POST":
        form = ProfileForm(request.POST, request.FILES, instance=profile_obj)
        if form.is_valid():
            form.save()
            user = request.user
            user.first_name = request.POST.get("first_name", "").strip()
            user.last_name = request.POST.get("last_name", "").strip()
            user.email = request.POST.get("email", "").strip()
            user.save(update_fields=["first_name", "last_name", "email"])

            messages.success(request, "Profile updated.")
            return redirect("accounts:profile")
    else:
        form = ProfileForm(instance=profile_obj)

    return render(request, "accounts/profile.html", {"profile": profile_obj, "form": form})
