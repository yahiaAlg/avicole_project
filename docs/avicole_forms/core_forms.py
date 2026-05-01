"""
core/forms.py

Forms for company information and user profile management.
"""

from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.forms import PasswordChangeForm  # re-exported for convenience

from core.models import CompanyInfo, UserProfile


class CompanyInfoForm(forms.ModelForm):
    """
    Edit the singleton CompanyInfo record.
    All fields optional except nom (enforced at model level).
    """

    class Meta:
        model = CompanyInfo
        exclude = ["id"]
        widgets = {
            "adresse": forms.Textarea(attrs={"rows": 3}),
            "pied_de_page": forms.Textarea(attrs={"rows": 3}),
            "taux_tva": forms.NumberInput(attrs={"step": "0.01", "min": "0", "max": "100"}),
        }


# ---------------------------------------------------------------------------
# User management
# ---------------------------------------------------------------------------

class UserCreateForm(forms.ModelForm):
    """
    Admin creates a new application user (Django User + UserProfile).
    Password is set explicitly; the form handles hashing via set_password().
    """

    password1 = forms.CharField(
        label="Mot de passe",
        widget=forms.PasswordInput,
        min_length=8,
    )
    password2 = forms.CharField(
        label="Confirmer le mot de passe",
        widget=forms.PasswordInput,
    )

    # Profile fields embedded in the same form
    role = forms.ChoiceField(
        choices=UserProfile.ROLE_CHOICES,
        label="Rôle",
    )
    telephone = forms.CharField(
        max_length=30,
        required=False,
        label="Téléphone",
    )
    notes = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 2}),
        required=False,
        label="Notes",
    )

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email", "is_active"]

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("password1")
        p2 = cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError(
                {"password2": "Les deux mots de passe ne correspondent pas."}
            )
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
            UserProfile.objects.update_or_create(
                user=user,
                defaults={
                    "role": self.cleaned_data["role"],
                    "telephone": self.cleaned_data.get("telephone", ""),
                    "notes": self.cleaned_data.get("notes", ""),
                },
            )
        return user


class UserUpdateForm(forms.ModelForm):
    """
    Update an existing user's info and profile.
    Password is NOT changed here — use Django's PasswordChangeForm for that.
    """

    role = forms.ChoiceField(choices=UserProfile.ROLE_CHOICES, label="Rôle")
    telephone = forms.CharField(max_length=30, required=False, label="Téléphone")
    notes = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 2}), required=False, label="Notes"
    )

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Pre-populate profile fields if the user already has a profile.
        if self.instance and hasattr(self.instance, "profile"):
            profile = self.instance.profile
            self.fields["role"].initial = profile.role
            self.fields["telephone"].initial = profile.telephone
            self.fields["notes"].initial = profile.notes

    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit:
            UserProfile.objects.update_or_create(
                user=user,
                defaults={
                    "role": self.cleaned_data["role"],
                    "telephone": self.cleaned_data.get("telephone", ""),
                    "notes": self.cleaned_data.get("notes", ""),
                },
            )
        return user
