from django import forms
from django.contrib.auth.password_validation import validate_password


class LoginForm(forms.Form):
    email = forms.EmailField()
    password = forms.CharField(widget=forms.PasswordInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"

    def clean_email(self):
        return self.cleaned_data["email"].lower()


class ChangePasswordForm(forms.Form):
    current_password = forms.CharField(widget=forms.PasswordInput, label="Current (or temporary) password")
    new_password = forms.CharField(widget=forms.PasswordInput)
    confirm_password = forms.CharField(widget=forms.PasswordInput)

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"

    def clean_current_password(self):
        value = self.cleaned_data["current_password"]
        if not self.user.check_password(value):
            raise forms.ValidationError("Current password is incorrect.")
        return value

    def clean_new_password(self):
        value = self.cleaned_data["new_password"]
        validate_password(value, self.user)
        return value

    def clean(self):
        data = super().clean()
        new = data.get("new_password")
        if new and new != data.get("confirm_password"):
            self.add_error("confirm_password", "Passwords do not match.")
        if new and new == data.get("current_password"):
            self.add_error("new_password", "New password must be different from the current one.")
        return data
