from django.contrib.auth import login
from django.contrib.auth.views import LoginView as BaseLoginView
from django.urls import reverse_lazy
from django.views.generic import FormView

from .forms import StyledAuthenticationForm, StyledUserCreationForm


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
