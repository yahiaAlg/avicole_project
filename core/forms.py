"""
core/forms.py

Forms for company information, multi-branch management (v1.4, §3.5), and
user profile management.
"""

from __future__ import annotations

from django import forms
from django.db.models import Q
from django.contrib.auth.models import User
from django.contrib.auth.forms import PasswordChangeForm  # re-exported for convenience
from django.contrib.contenttypes.forms import generic_inlineformset_factory

from core.models import CompanyInfo, Branche, UserProfile, PieceJointe


class CompanyInfoForm(forms.ModelForm):
    """
    Edit the singleton CompanyInfo record.
    All fields optional except nom (enforced at model level).

    v1.4 note: CompanyInfo stays a single company-wide singleton even
    though the company can now run several Branches (§3.4) — unaffected
    by the multi-branch change.
    """

    class Meta:
        model = CompanyInfo
        exclude = ["id"]
        widgets = {
            "adresse": forms.Textarea(attrs={"rows": 3}),
            "pied_de_page": forms.Textarea(attrs={"rows": 3}),
            "taux_tva": forms.NumberInput(
                attrs={"step": "0.01", "min": "0", "max": "100"}
            ),
        }


# ---------------------------------------------------------------------------
# Branche (v1.4, §3.5) — admin-only master data
# ---------------------------------------------------------------------------


class BrancheForm(forms.ModelForm):
    """
    Create or edit a Branche (farm site/branch) — admin only (BR-BRA-06).

    BR-BRA-02: `chef_de_branche` must be a user whose profile role is
    'chef_branche'. The selectable queryset also excludes users already
    heading a *different* branch (the OneToOne would otherwise reject the
    save) while still allowing the branch's own current chef on edit.
    """

    class Meta:
        model = Branche
        fields = [
            "nom",
            "code",
            "wilaya",
            "adresse",
            "telephone",
            "chef_de_branche",
            "actif",
        ]
        widgets = {
            "adresse": forms.Textarea(attrs={"rows": 2}),
            "code": forms.TextInput(attrs={"placeholder": "مثال: EST, OUEST"}),
        }
        help_texts = {
            "code": "رمز قصير فريد يُستخدم في ترقيم الوثائق — يفضّل عدم تغييره بعد إصدار وثائق.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["wilaya"].required = False
        self.fields["adresse"].required = False
        self.fields["telephone"].required = False
        self.fields["chef_de_branche"].required = False

        exclude_pk = self.instance.pk if self.instance and self.instance.pk else None
        self.fields["chef_de_branche"].queryset = (
            User.objects.filter(profile__role=UserProfile.ROLE_CHEF_BRANCHE)
            .filter(Q(branche_dirigee__isnull=True) | Q(branche_dirigee__pk=exclude_pk))
            .order_by("first_name", "last_name", "username")
        )

    def clean_code(self):
        code = self.cleaned_data["code"]
        return code.strip().upper()

    def clean_chef_de_branche(self):
        """BR-BRA-02 — duplicated from Branche.clean() for a form-level error."""
        chef = self.cleaned_data.get("chef_de_branche")
        if (
            chef
            and hasattr(chef, "profile")
            and chef.profile.role != UserProfile.ROLE_CHEF_BRANCHE
        ):
            raise forms.ValidationError(
                "BR-BRA-02 : يجب أن يحمل المستخدم المعيّن رئيساً للفرع الدور 'رئيس فرع'."
            )
        return chef


class BrancheSwitchForm(forms.Form):
    """
    Non-model form backing the admin/comptable branch switcher (§3.5.4).

    Leaving `branche` blank selects **Vue Globale** — the aggregate,
    read-only mode across all branches (BR-BRA-04). Only roles with
    `profile.a_vue_globale` (admin, or comptable left unbound) should ever
    be shown this form; chef de branche/opérateur are locked to their own
    branche and never see a switcher (BR-BRA-02).
    """

    branche = forms.ModelChoiceField(
        queryset=Branche.objects.filter(actif=True).order_by("nom"),
        required=False,
        empty_label="🌐 Vue Globale (جميع الفروع)",
        label="الفرع النشط",
    )


# ---------------------------------------------------------------------------
# User management
# ---------------------------------------------------------------------------


def _clean_role_branche(role, branche):
    """
    Shared BR-BRA-02/03 validation, used by both UserCreateForm and
    UserUpdateForm: mirrors UserProfile.clean() so the error surfaces on
    the form field itself instead of only on instance.full_clean().
    """
    if role in UserProfile.ROLES_LIES_A_UNE_BRANCHE and not branche:
        raise forms.ValidationError(
            "BR-BRA-02 : هذا الدور (رئيس فرع / مشغّل) يتطلب تحديد فرع واحد إلزامياً."
        )
    if role == UserProfile.ROLE_ADMIN and branche:
        raise forms.ValidationError(
            "BR-BRA-03 : المدير غير مرتبط بفرع واحد — اترك حقل الفرع فارغاً "
            "(يتم تبديل الفرع النشط عبر الجلسة)."
        )


class UserCreateForm(forms.ModelForm):
    """
    Admin creates a new application user (Django User + UserProfile).
    Password is set explicitly; the form handles hashing via set_password().

    v1.4 — `branche` is required for chef_branche/opérateur, forbidden for
    admin, and optional for comptable (BR-BRA-02/03; see UserProfile.branche).
    """

    password1 = forms.CharField(
        label="كلمة المرور",
        widget=forms.PasswordInput,
        min_length=8,
    )
    password2 = forms.CharField(
        label="تأكيد كلمة المرور",
        widget=forms.PasswordInput,
    )

    # Profile fields embedded in the same form
    role = forms.ChoiceField(
        choices=UserProfile.ROLE_CHOICES,
        label="الدور",
    )
    branche = forms.ModelChoiceField(
        queryset=None,
        required=False,
        label="الفرع",
        help_text=(
            "إلزامي لرئيس الفرع والمشغّل (BR-BRA-02). اختياري للمحاسب "
            "(فارغ = رؤية شاملة). يُترك فارغاً للمدير (BR-BRA-03)."
        ),
    )
    telephone = forms.CharField(
        max_length=30,
        required=False,
        label="الهاتف",
    )
    notes = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 2}),
        required=False,
        label="ملاحظات",
    )

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from core.models import Branche

        self.fields["branche"].queryset = Branche.objects.filter(actif=True).order_by(
            "nom"
        )

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("password1")
        p2 = cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError(
                {"password2": "Les deux mots de passe ne correspondent pas."}
            )
        role = cleaned.get("role")
        branche = cleaned.get("branche")
        if role:
            try:
                _clean_role_branche(role, branche)
            except forms.ValidationError as exc:
                self.add_error("branche", exc)
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
                    "branche": self.cleaned_data.get("branche"),
                    "telephone": self.cleaned_data.get("telephone", ""),
                    "notes": self.cleaned_data.get("notes", ""),
                },
            )
        return user


class UserUpdateForm(forms.ModelForm):
    """
    Update an existing user's info and profile.
    Password is NOT changed here — use Django's PasswordChangeForm for that.

    v1.4 — `branche` follows the same BR-BRA-02/03 rule as UserCreateForm.
    """

    role = forms.ChoiceField(choices=UserProfile.ROLE_CHOICES, label="الدور")
    branche = forms.ModelChoiceField(
        queryset=None,
        required=False,
        label="الفرع",
        help_text=(
            "إلزامي لرئيس الفرع والمشغّل (BR-BRA-02). اختياري للمحاسب "
            "(فارغ = رؤية شاملة). يُترك فارغاً للمدير (BR-BRA-03)."
        ),
    )
    telephone = forms.CharField(max_length=30, required=False, label="الهاتف")
    notes = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 2}), required=False, label="ملاحظات"
    )

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from core.models import Branche

        self.fields["branche"].queryset = Branche.objects.filter(actif=True).order_by(
            "nom"
        )
        # Pre-populate profile fields if the user already has a profile.
        if self.instance and hasattr(self.instance, "profile"):
            profile = self.instance.profile
            self.fields["role"].initial = profile.role
            self.fields["branche"].initial = profile.branche
            self.fields["telephone"].initial = profile.telephone
            self.fields["notes"].initial = profile.notes

    def clean(self):
        cleaned = super().clean()
        role = cleaned.get("role")
        branche = cleaned.get("branche")
        if role:
            try:
                _clean_role_branche(role, branche)
            except forms.ValidationError as exc:
                self.add_error("branche", exc)
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit:
            UserProfile.objects.update_or_create(
                user=user,
                defaults={
                    "role": self.cleaned_data["role"],
                    "branche": self.cleaned_data.get("branche"),
                    "telephone": self.cleaned_data.get("telephone", ""),
                    "notes": self.cleaned_data.get("notes", ""),
                },
            )
        return user


# ---------------------------------------------------------------------------
# PieceJointe (v1.5 — generic document-proof model, core.models)
#
# Single source of truth for attachment validation and formset wiring, used
# by every app that carries proof documents (BL, facture, règlement/
# paiement, dépense, retrait, acompte, bulletin de paie, ...) instead of
# each app re-declaring its own `piece_jointe` FileField + validation.
# ---------------------------------------------------------------------------

ALLOWED_ATTACHMENT_TYPES = ["application/pdf", "image/jpeg", "image/png"]
MAX_ATTACHMENT_SIZE_MB = 5


class PieceJointeForm(forms.ModelForm):
    """
    Single-file form for one PieceJointe row. Used standalone (quick
    "add a proof" action) or as the `form=` of the generic formset below
    (multi-file attach/replace on a document's create/edit page).
    """

    class Meta:
        model = PieceJointe
        fields = ["fichier", "type_document", "description"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["type_document"].required = False
        self.fields["description"].required = False

    def clean_fichier(self):
        file = self.cleaned_data.get("fichier")
        if file and hasattr(file, "content_type"):
            if file.content_type not in ALLOWED_ATTACHMENT_TYPES:
                raise forms.ValidationError(
                    "Seuls les fichiers PDF, JPG et PNG sont acceptés."
                )
            if file.size > MAX_ATTACHMENT_SIZE_MB * 1024 * 1024:
                raise forms.ValidationError(
                    f"La taille du fichier ne doit pas dépasser {MAX_ATTACHMENT_SIZE_MB} Mo."
                )
        return file


def make_piece_jointe_formset(extra: int = 1, max_num: int | None = 10):
    """
    Build a generic-relation formset bound to PieceJointe for any parent
    model (BLFournisseur, FactureClient, Depense, ...).

    Usage in a view, mirroring a normal inline formset:
        FormSet = make_piece_jointe_formset(extra=2)
        formset = FormSet(request.POST or None, request.FILES or None, instance=bl)
        ...
        if header_form.is_valid() and formset.is_valid():
            bl = header_form.save()
            formset.instance = bl
            formset.save()

    Each app module below exposes a pre-built alias (e.g.
    `BLFournisseurPieceJointeFormSet`) purely for a shorter, self-documenting
    import at the call site — they all come from this same factory.
    """
    return generic_inlineformset_factory(
        PieceJointe,
        form=PieceJointeForm,
        ct_field="content_type",
        fk_field="object_id",
        extra=extra,
        can_delete=True,
        max_num=max_num,
        validate_max=bool(max_num),
    )


#: Default ready-to-use formset (extra=1) — fine for most single-proof cases.
PieceJointeFormSet = make_piece_jointe_formset()
