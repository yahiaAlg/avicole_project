"""
depenses/forms.py

Forms for operational expense tracking:
  CategorieDepense, Depense.
Forms for the two special expense families:
  Associe, RetraitAssocie  (stakeholder withdrawals)
  Employe, Pointage, CongeEmploye, AcompteEmploye, BulletinPaie  (RH/payroll)

Business rules enforced here:
  BR-DEP-01  Goods-type facture fournisseur NEVER linked to a dépense.
  BR-DEP-03  Only Service-type supplier invoices may be optionally linked.
  BR-DEP-04  Dépenses may optionally be attributed to a lot for profitability.
  BR-ASSOC-02  Retraits are always manual.
  BR-RH-01..05 see depenses/models.py module docstring.

v1.4 multi-branch notes (§3.5):
  BR-BRA-01  Depense.branche is a required FK — explicit field on DepenseForm.
             Pass `branche=<Branche instance>` from the view when the
             current user is locked to one branch (chef de branche /
             opérateur, BR-BRA-02) to pre-select/lock the field and to
             scope the optional `lot` / `facture_liee` choices to that
             same branche.
  BR-BRA-08  Associe / RetraitAssocie are intentionally NEVER branche-scoped
             — equity withdrawals belong to the company as a whole. No
             `branche` field, no `branche` kwarg, on either form.
  BR-BRA-09  Employe.branche (and the branche of everything chained off an
             employee — Pointage, CongeEmploye, AcompteEmploye,
             BulletinPaie) is DERIVED from `employe.batiment.branche`, not
             stored. None of those models gain a `branche` field; instead,
             every form below that exposes an `employe` or `batiment`
             picker accepts an optional `branche=<Branche instance>` kwarg
             to scope that picker's queryset (via the `batiment__branche`
             join for employee-based pickers).
"""

import datetime
from django import forms
from django.core.exceptions import ValidationError

from depenses.models import (
    CategorieDepense,
    Depense,
    Associe,
    RetraitAssocie,
    Employe,
    Pointage,
    CongeEmploye,
    AcompteEmploye,
    BulletinPaie,
)
from elevage.models import LotElevage
from core.forms import make_piece_jointe_formset


class CategorieDepenseForm(forms.ModelForm):
    class Meta:
        model = CategorieDepense
        fields = ["code", "libelle", "description", "ordre", "actif"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 2}),
            "code": forms.TextInput(attrs={"placeholder": "مثال: ENERGIE"}),
        }


class DepenseForm(forms.ModelForm):
    """
    Record an operational expense.

    BR-BRA-01: every dépense belongs to exactly one branche (required FK).
    Pass `branche=<Branche instance>` from the view when the current user
    is locked to one branch (chef de branche / opérateur, BR-BRA-02) to
    pre-select and lock the field. The optional `lot` and `facture_liee`
    choices are scoped to that same branche, and clean() duplicates
    Depense.clean()'s same-branche guard for a friendlier form-level error.

    BR-DEP-03: facture_liee is optional and restricted to Service-type invoices
               only.  The queryset is filtered accordingly.
    BR-DEP-04: lot attribution is optional.
    """

    class Meta:
        model = Depense
        fields = [
            "date",
            "branche",
            "categorie",
            "description",
            "montant",
            "mode_paiement",
            "reference_document",
            "lot",
            "facture_liee",
            "notes",
        ]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "montant": forms.NumberInput(attrs={"step": "0.01", "min": "0.01"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
            "reference_document": forms.TextInput(
                attrs={"placeholder": "رقم الوثيقة المثبتة"}
            ),
        }

    def __init__(self, *args, branche=None, **kwargs):
        super().__init__(*args, **kwargs)
        from core.models import Branche

        # Active categories only, ordered for display.
        self.fields["categorie"].queryset = CategorieDepense.objects.filter(
            actif=True
        ).order_by("ordre", "libelle")

        self.fields["branche"].queryset = Branche.objects.filter(actif=True).order_by(
            "nom"
        )
        self._branche = branche
        if branche:
            self.fields["branche"].initial = branche
            self.fields["branche"].widget = forms.HiddenInput()

        # BR-DEP-04 / BR-BRA-01: lot attribution is informational, but must
        # stay within the dépense's own branche — scope the choices when
        # the branche is locked so the user can't even pick a bad one.
        lot_qs = LotElevage.objects.order_by("-date_ouverture")
        if branche:
            lot_qs = lot_qs.filter(branche=branche)
        self.fields["lot"].queryset = lot_qs
        self.fields["lot"].required = False

        # BR-DEP-03 / BR-BRA-01: only Service-type supplier invoices may be
        # linked, scoped to this branche when locked (same reasoning as lot).
        from achats.models import FactureFournisseur

        facture_qs = FactureFournisseur.objects.filter(
            type_facture=FactureFournisseur.TYPE_SERVICE
        )
        if branche:
            facture_qs = facture_qs.filter(branche=branche)
        self.fields["facture_liee"].queryset = facture_qs.order_by("-date_facture")
        self.fields["facture_liee"].required = False
        self.fields["facture_liee"].help_text = (
            "اختياري — للفواتير من النوع خدمة فقط (BR-DEP-03)."
        )

        self.fields["reference_document"].required = False
        self.fields["notes"].required = False

    def clean_date(self):
        date = self.cleaned_data["date"]
        if date > datetime.date.today():
            raise ValidationError(
                "La date de la dépense ne peut pas être dans le futur."
            )
        return date

    def clean_montant(self):
        montant = self.cleaned_data["montant"]
        if montant <= 0:
            raise ValidationError("Le montant de la dépense doit être supérieur à 0.")
        return montant

    def clean(self):
        cleaned = super().clean()
        branche = cleaned.get("branche") or self._branche
        lot = cleaned.get("lot")
        facture_liee = cleaned.get("facture_liee")

        # BR-DEP-01 / BR-DEP-03: double-guard — only Service invoices allowed.
        if facture_liee is not None:
            from achats.models import FactureFournisseur

            if facture_liee.type_facture != FactureFournisseur.TYPE_SERVICE:
                raise ValidationError(
                    {
                        "facture_liee": (
                            "BR-DEP-01 / BR-DEP-03 : seules les factures de type 'Service' "
                            "peuvent être liées à une dépense. Une facture de marchandises "
                            "ne génère jamais une dépense."
                        )
                    }
                )

        # BR-BRA-01: the optional lot / facture_liee links must stay within
        # this dépense's own branche (mirrors Depense.clean()).
        if branche and lot and lot.branche_id != branche.pk:
            raise ValidationError(
                {
                    "lot": (
                        "BR-BRA-01 : la dépense et le lot attribué doivent "
                        "appartenir à la même branche."
                    )
                }
            )
        if branche and facture_liee and facture_liee.branche_id != branche.pk:
            raise ValidationError(
                {
                    "facture_liee": (
                        "BR-BRA-01 : la dépense et la facture fournisseur liée "
                        "doivent appartenir à la même branche."
                    )
                }
            )
        return cleaned


# ---------------------------------------------------------------------------
# Filter form for the dépenses list view
# ---------------------------------------------------------------------------


class DepenseFilterForm(forms.Form):
    """
    Non-model form used to filter the dépenses list view.
    All fields are optional.

    Pass `branche=<Branche instance>` from the view to scope the `lot`
    choices to that branche (BR-BRA-01) — the dépenses themselves are
    already filtered by the active branche at the queryset level in the view.
    """

    categorie = forms.ModelChoiceField(
        queryset=CategorieDepense.objects.filter(actif=True).order_by(
            "ordre", "libelle"
        ),
        required=False,
        empty_label="كل الفئات",
        label="الفئة",
    )
    date_debut = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        label="تاريخ البداية",
    )
    date_fin = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        label="تاريخ النهاية",
    )
    lot = forms.ModelChoiceField(
        queryset=LotElevage.objects.order_by("-date_ouverture"),
        required=False,
        empty_label="كل الدفعات",
        label="الدفعة المخصصة",
    )
    mode_paiement = forms.ChoiceField(
        choices=[("", "كل طرق الدفع")] + list(Depense.MODE_CHOICES),
        required=False,
        label="طريقة الدفع",
    )

    def __init__(self, *args, branche=None, **kwargs):
        super().__init__(*args, **kwargs)
        if branche:
            self.fields["lot"].queryset = self.fields["lot"].queryset.filter(
                branche=branche
            )

    def clean(self):
        cleaned = super().clean()
        date_debut = cleaned.get("date_debut")
        date_fin = cleaned.get("date_fin")
        if date_debut and date_fin and date_debut > date_fin:
            raise ValidationError(
                "La date de début doit être antérieure ou égale à la date de fin."
            )
        return cleaned


# ---------------------------------------------------------------------------
# Filter form for the dépenses dashboard
# ---------------------------------------------------------------------------


class DashboardFilterForm(forms.Form):
    """
    Date-range filter for the dépenses dashboard.
    Both fields are optional; defaults are applied in the view.

    No `branche` field: the dashboard is scoped to the currently active
    branche (or Vue Globale) by the view via depenses.utils helpers, not
    by this form — mirrors the rest of the app (§3.5.4/3.5.5).
    """

    date_debut = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        label="من",
    )
    date_fin = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        label="إلى",
    )

    def clean(self):
        cleaned = super().clean()
        date_debut = cleaned.get("date_debut")
        date_fin = cleaned.get("date_fin")
        if date_debut and date_fin and date_debut > date_fin:
            raise forms.ValidationError(
                "La date de début doit être antérieure ou égale à la date de fin."
            )
        return cleaned


# ===========================================================================
# Associés — Stakeholders & withdrawals  (BR-ASSOC-01 / BR-ASSOC-02)
#
# v1.4 note: per BR-BRA-08, Associe and RetraitAssocie are intentionally
# NEVER branche-scoped — equity withdrawals belong to the company as a
# whole. Neither form below takes a `branche` kwarg or exposes a `branche`
# field; this is deliberate, not an oversight (mirrors VoyageLivraison /
# PrixMarche in clients/forms.py, and CategorieDepense above).
# ===========================================================================


class AssocieForm(forms.ModelForm):
    class Meta:
        model = Associe
        fields = ["nom", "telephone", "pourcentage_parts", "actif", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 2})}


class RetraitAssocieForm(forms.ModelForm):
    """
    Record a stakeholder withdrawal.
    BR-ASSOC-02: always a manual, deliberate entry — no automation here.
    """

    class Meta:
        model = RetraitAssocie
        fields = [
            "associe",
            "date",
            "montant",
            "mode_paiement",
            "motif",
            "reference_document",
            "notes",
        ]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "montant": forms.NumberInput(attrs={"step": "0.01", "min": "0.01"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["associe"].queryset = Associe.objects.filter(actif=True).order_by(
            "nom"
        )
        self.fields["motif"].required = False
        self.fields["reference_document"].required = False
        self.fields["notes"].required = False

    def clean_date(self):
        date = self.cleaned_data["date"]
        if date > datetime.date.today():
            raise ValidationError("تاريخ السحب لا يمكن أن يكون في المستقبل.")
        return date

    def clean_montant(self):
        montant = self.cleaned_data["montant"]
        if montant <= 0:
            raise ValidationError("يجب أن يكون المبلغ أكبر من 0.")
        return montant


class RetraitFilterForm(forms.Form):
    """Non-model filter form for the retraits list view. Global — BR-BRA-08."""

    associe = forms.ModelChoiceField(
        queryset=Associe.objects.order_by("nom"),
        required=False,
        empty_label="كل الشركاء",
        label="الشريك",
    )
    date_debut = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"}), label="من"
    )
    date_fin = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"}), label="إلى"
    )

    def clean(self):
        cleaned = super().clean()
        date_debut = cleaned.get("date_debut")
        date_fin = cleaned.get("date_fin")
        if date_debut and date_fin and date_debut > date_fin:
            raise ValidationError("تاريخ البداية يجب أن يسبق تاريخ النهاية.")
        return cleaned


# ===========================================================================
# RH — Employees  (BR-RH-01 / BR-RH-02 / BR-BRA-09)
# ===========================================================================


class EmployeForm(forms.ModelForm):
    """
    BR-BRA-09: Employe.branche is DERIVED from `batiment.branche`, not
    stored — there is no `branche` field here. Pass `branche=<Branche
    instance>` from the view when the current user is locked to one
    branch (chef de branche / opérateur, BR-BRA-02) to scope the
    `batiment` and `binome` choices to that branche.
    """

    class Meta:
        model = Employe
        fields = [
            "matricule",
            "nom_complet",
            "fonction",
            "telephone",
            "date_embauche",
            "batiment",
            "jour_repos_habituel",
            "binome",
            "salaire_base_mensuel",
            "heures_normales_jour",
            "taux_majoration_heure_sup",
            "actif",
            "notes",
        ]
        widgets = {
            "date_embauche": forms.DateInput(attrs={"type": "date"}),
            "salaire_base_mensuel": forms.NumberInput(attrs={"step": "0.01"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, branche=None, **kwargs):
        super().__init__(*args, **kwargs)
        from intrants.models import Batiment

        batiment_qs = Batiment.objects.filter(actif=True).order_by("nom")
        if branche:
            # BR-BRA-09: scope the building picker so the employee's
            # derived branche can only land in the locked branche.
            batiment_qs = batiment_qs.filter(branche=branche)
        self.fields["batiment"].queryset = batiment_qs
        self.fields["batiment"].required = False

        binome_qs = Employe.objects.filter(actif=True).order_by("nom_complet")
        if self.instance and self.instance.pk:
            binome_qs = binome_qs.exclude(pk=self.instance.pk)
        if branche:
            # The rest-day partner naturally works at the same branche.
            binome_qs = binome_qs.filter(batiment__branche=branche)
        self.fields["binome"].queryset = binome_qs
        self.fields["binome"].required = False

    def clean(self):
        cleaned = super().clean()
        binome = cleaned.get("binome")
        if binome is not None and self.instance and binome.pk == self.instance.pk:
            raise ValidationError({"binome": "لا يمكن أن يكون العامل بديلاً لنفسه."})
        return cleaned


# ===========================================================================
# RH — Attendance (Pointage)  (BR-RH-05 / BR-BRA-09)
# ===========================================================================


class PointageForm(forms.ModelForm):
    """
    Single-day attendance entry/correction.

    BR-BRA-09: Pointage.branche is inherited from employe.branche — pass
    `branche=<Branche instance>` from the view when the current user is
    locked to one branch to scope the `employe` choices.
    """

    class Meta:
        model = Pointage
        fields = ["employe", "date", "statut", "heures_supplementaires", "notes"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "heures_supplementaires": forms.NumberInput(
                attrs={"step": "0.25", "min": "0"}
            ),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, branche=None, **kwargs):
        super().__init__(*args, **kwargs)
        employe_qs = Employe.objects.filter(actif=True).order_by("nom_complet")
        if branche:
            # BR-BRA-09: employe.branche is derived via batiment — filter
            # through the join since there is no stored column to match on.
            employe_qs = employe_qs.filter(batiment__branche=branche)
        self.fields["employe"].queryset = employe_qs
        self.fields["notes"].required = False
        # BR-RH-06: rest days are auto-computed from jour_repos_habituel —
        # HR should never create a REPOS row manually, so drop it from the
        # picker. Only "present" (to log overtime) / "absent" / "congé"
        # remain, i.e. only the exceptions to a normal working day.
        self.fields["statut"].choices = [
            c for c in Pointage.STATUT_CHOICES if c[0] != Pointage.STATUT_REPOS
        ]

    def clean(self):
        cleaned = super().clean()
        statut = cleaned.get("statut")
        heures_sup = cleaned.get("heures_supplementaires") or 0
        employe = cleaned.get("employe")
        date = cleaned.get("date")
        if statut in (Pointage.STATUT_REPOS, Pointage.STATUT_ABSENT) and heures_sup:
            raise ValidationError(
                {
                    "heures_supplementaires": "لا يمكن تسجيل ساعات إضافية في يوم راحة أو غياب."
                }
            )
        if (
            employe
            and date
            and statut != Pointage.STATUT_REPOS
            and date.weekday() == employe.jour_repos_habituel
        ):
            raise ValidationError(
                {
                    "date": (
                        "هذا اليوم هو يوم الراحة الأسبوعي لهذا العامل — "
                        "يُحتسب تلقائياً ولا حاجة لتسجيله."
                    )
                }
            )
        return cleaned


class PointageFilterForm(forms.Form):
    """
    Non-model filter form for the pointage list view.
    Pass `branche=<Branche instance>` from the view to scope the `employe`
    choices (BR-BRA-09).
    """

    employe = forms.ModelChoiceField(
        queryset=Employe.objects.order_by("nom_complet"),
        required=False,
        empty_label="كل العمال",
        label="العامل",
    )
    date_debut = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"}), label="من"
    )
    date_fin = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"}), label="إلى"
    )
    statut = forms.ChoiceField(
        choices=[("", "كل الحالات")] + list(Pointage.STATUT_CHOICES),
        required=False,
        label="الحالة",
    )

    def __init__(self, *args, branche=None, **kwargs):
        super().__init__(*args, **kwargs)
        if branche:
            self.fields["employe"].queryset = self.fields["employe"].queryset.filter(
                batiment__branche=branche
            )

    def clean(self):
        cleaned = super().clean()
        date_debut = cleaned.get("date_debut")
        date_fin = cleaned.get("date_fin")
        if date_debut and date_fin and date_debut > date_fin:
            raise ValidationError("تاريخ البداية يجب أن يسبق تاريخ النهاية.")
        return cleaned


# ===========================================================================
# RH — Paid leave (CongeEmploye)  (BR-RH-03 / BR-BRA-09)
# ===========================================================================


class CongeEmployeForm(forms.ModelForm):
    """
    Record a paid-leave block. `nb_jours` is computed automatically on save
    (see CongeEmploye.save()) and is therefore excluded from the form.

    Pass `branche=<Branche instance>` from the view to scope the `employe`
    choices (BR-BRA-09).
    """

    class Meta:
        model = CongeEmploye
        fields = ["employe", "date_debut", "date_fin", "motif", "notes"]
        widgets = {
            "date_debut": forms.DateInput(attrs={"type": "date"}),
            "date_fin": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, branche=None, **kwargs):
        super().__init__(*args, **kwargs)
        employe_qs = Employe.objects.filter(actif=True).order_by("nom_complet")
        if branche:
            employe_qs = employe_qs.filter(batiment__branche=branche)
        self.fields["employe"].queryset = employe_qs
        self.fields["motif"].required = False
        self.fields["notes"].required = False

    def clean(self):
        cleaned = super().clean()
        date_debut = cleaned.get("date_debut")
        date_fin = cleaned.get("date_fin")
        if date_debut and date_fin and date_fin < date_debut:
            raise ValidationError(
                {"date_fin": "تاريخ النهاية يجب أن يكون بعد تاريخ البداية."}
            )
        return cleaned


# ===========================================================================
# RH — Salary advances (AcompteEmploye)  (BR-RH-04 / BR-BRA-09)
# ===========================================================================


class AcompteEmployeForm(forms.ModelForm):
    """
    Pass `branche=<Branche instance>` from the view to scope the `employe`
    choices (BR-BRA-09).
    """

    class Meta:
        model = AcompteEmploye
        fields = ["employe", "date", "montant", "mode_paiement", "motif", "notes"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "montant": forms.NumberInput(attrs={"step": "0.01", "min": "0.01"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, branche=None, **kwargs):
        super().__init__(*args, **kwargs)
        employe_qs = Employe.objects.filter(actif=True).order_by("nom_complet")
        if branche:
            employe_qs = employe_qs.filter(batiment__branche=branche)
        self.fields["employe"].queryset = employe_qs
        self.fields["motif"].required = False
        self.fields["notes"].required = False

    def clean_date(self):
        date = self.cleaned_data["date"]
        if date > datetime.date.today():
            raise ValidationError("تاريخ التسبيق لا يمكن أن يكون في المستقبل.")
        return date

    def clean_montant(self):
        montant = self.cleaned_data["montant"]
        if montant <= 0:
            raise ValidationError("يجب أن يكون المبلغ أكبر من 0.")
        return montant


# ===========================================================================
# RH — Payroll (BulletinPaie)  (BR-RH-02 / BR-RH-05 / BR-BRA-09)
# ===========================================================================


class GenererBulletinPaieForm(forms.Form):
    """
    Non-model form: select employee + period to (re)compute a payslip via
    depenses.utils.calculer_donnees_paie(). The view persists the result.

    Pass `branche=<Branche instance>` from the view to scope the `employe`
    choices (BR-BRA-09).
    """

    employe = forms.ModelChoiceField(
        queryset=Employe.objects.filter(actif=True).order_by("nom_complet"),
        label="العامل",
    )
    annee = forms.IntegerField(label="السنة", min_value=2000, max_value=2100)
    mois = forms.IntegerField(label="الشهر", min_value=1, max_value=12)

    def __init__(self, *args, branche=None, **kwargs):
        super().__init__(*args, **kwargs)
        if branche:
            self.fields["employe"].queryset = self.fields["employe"].queryset.filter(
                batiment__branche=branche
            )


class BulletinPaiementForm(forms.ModelForm):
    """
    Mark a payslip as paid: date + payment method only.
    No `branche` kwarg needed — no FK picker to scope, and the payslip's
    employee is already fixed by the time this form is used.
    """

    class Meta:
        model = BulletinPaie
        fields = ["date_paiement", "mode_paiement", "notes"]
        widgets = {
            "date_paiement": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def clean_date_paiement(self):
        date_paiement = self.cleaned_data.get("date_paiement")
        if date_paiement and date_paiement > datetime.date.today():
            raise ValidationError("تاريخ الدفع لا يمكن أن يكون في المستقبل.")
        return date_paiement


class RHFilterForm(forms.Form):
    """
    Non-model filter form for RH list views (pointage / bulletins).
    Pass `branche=<Branche instance>` from the view to scope the `employe`
    choices (BR-BRA-09).
    """

    employe = forms.ModelChoiceField(
        queryset=Employe.objects.order_by("nom_complet"),
        required=False,
        empty_label="كل العمال",
        label="العامل",
    )
    annee = forms.IntegerField(
        required=False, label="السنة", min_value=2000, max_value=2100
    )
    mois = forms.IntegerField(required=False, label="الشهر", min_value=1, max_value=12)

    def __init__(self, *args, branche=None, **kwargs):
        super().__init__(*args, **kwargs)
        if branche:
            self.fields["employe"].queryset = self.fields["employe"].queryset.filter(
                batiment__branche=branche
            )


# ---------------------------------------------------------------------------
# PieceJointe formsets (v1.5) — one alias per attachment-capable model,
# built from core.forms.make_piece_jointe_formset. Replaces the old
# `piece_jointe` FileField on DepenseForm / RetraitAssocieForm (now removed
# above) — proofs are attached/edited via these formsets in the view
# alongside the header form, same pattern as achats.forms.
# ---------------------------------------------------------------------------

DepensePieceJointeFormSet = make_piece_jointe_formset(extra=1)
RetraitAssociePieceJointeFormSet = make_piece_jointe_formset(extra=1)
AcompteEmployePieceJointeFormSet = make_piece_jointe_formset(extra=1, max_num=3)
BulletinPaiePieceJointeFormSet = make_piece_jointe_formset(extra=1, max_num=3)
