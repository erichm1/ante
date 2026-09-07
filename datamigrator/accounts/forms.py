from django.contrib.auth.forms import AuthenticationForm, UserCreationForm


class BootstrapFormMixin:
    """Adds Bootstrap's form-control class to every field — Django doesn't
    do this itself, and this project has no form-rendering JS to do it client-side."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            existing = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"{existing} form-control".strip()


class StyledAuthenticationForm(BootstrapFormMixin, AuthenticationForm):
    pass


class StyledUserCreationForm(BootstrapFormMixin, UserCreationForm):
    pass
