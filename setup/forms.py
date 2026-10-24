from django import forms
from django.contrib.auth.password_validation import validate_password

from accounts.models import User


class FirstSetupForm(forms.Form):
    company_name = forms.CharField(max_length=200)
    admin_name = forms.CharField(max_length=100, label="Your name")
    email = forms.EmailField(label="Admin email")
    password = forms.CharField(widget=forms.PasswordInput)
    confirm_password = forms.CharField(widget=forms.PasswordInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("This email is already used.")
        return email

    def clean_password(self):
        password = self.cleaned_data["password"]
        validate_password(password)
        return password

    def clean(self):
        data = super().clean()
        if data.get("password") and data.get("password") != data.get("confirm_password"):
            self.add_error("confirm_password", "Passwords do not match.")
        return data