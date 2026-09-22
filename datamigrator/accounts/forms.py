from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm

from .models import Profile


class BootstrapFormMixin:
    """Adds Bootstrap's form-control class to every field — Django doesn't
    do this itself, and this project has no form-rendering JS to do it client-side."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            existing = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"{existing} form-control".strip()


class StyledAuthenticationForm(BootstrapFormMixin, AuthenticationForm):
    """Sign-in for accounts that already exist. Django's backend refuses unknown and deactivated users; the error
    is the same generic one either way, so it doesn't reveal which usernames exist."""

    error_messages = {
        **AuthenticationForm.error_messages,
        "inactive": "Please enter a correct username and password. Note that both fields may be case-sensitive.",
    }


class StyledUserCreationForm(BootstrapFormMixin, UserCreationForm):
    pass


class ProfileForm(BootstrapFormMixin, forms.ModelForm):
    """avatar/company_icon carry FileExtensionValidator + a size check (see
    Profile) — going through a real ModelForm (is_valid()) is what actually
    runs those, unlike a bare instance.save() after assigning request.FILES
    directly."""

    class Meta:
        model = Profile
        fields = ["avatar", "company_name", "company_document", "company_icon"]

