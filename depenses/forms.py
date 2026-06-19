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

    BR-DEP-03: facture_liee is optional and restricted to Service-type invoices
               only.  The queryset is filtered accordingly.
    BR-DEP-04: lot attribution is optional.
    """

    class Meta:
        model = Depense
        fields = [
            "date",
            "categorie",
            "description",
            "montant",
            "mode_paiement",
            "reference_document",
            "piece_jointe",
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Active categories only, ordered for display.
        self.fields["categorie"].queryset = CategorieDepense.objects.filter(
            actif=True
        ).order_by("ordre", "libelle")

        # BR-DEP-04: all lots shown; lot attribution is informational.
        self.fields["lot"].queryset = LotElevage.objects.order_by("-date_ouverture")
        self.fields["lot"].required = False

        # BR-DEP-03: only Service-type supplier invoices may be linked.
        from achats.models import FactureFournisseur

        self.fields["facture_liee"].queryset = FactureFournisseur.objects.filter(
            type_facture=FactureFournisseur.TYPE_SERVICE
        ).order_by("-date_facture")
        self.fields["facture_liee"].required = False
        self.fields["facture_liee"].help_text = (
            "اختياري — للفواتير من النوع خدمة فقط (BR-DEP-03)."
        )

        self.fields["reference_document"].required = False
        self.fields["piece_jointe"].required = False
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
        return cleaned


# ---------------------------------------------------------------------------
# Filter form for the dépenses list view
# ---------------------------------------------------------------------------


class DepenseFilterForm(forms.Form):
    """
    Non-model form used to filter the dépenses list view.
    All fields are optional.
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
            "piece_jointe",
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
        self.fields["piece_jointe"].required = False
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
    """Non-model filter form for the retraits list view."""

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
# RH — Employees  (BR-RH-01 / BR-RH-02)
# ===========================================================================


class EmployeForm(forms.ModelForm):
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from intrants.models import Batiment

        self.fields["batiment"].queryset = Batiment.objects.filter(actif=True).order_by(
            "nom"
        )
        self.fields["batiment"].required = False

        binome_qs = Employe.objects.filter(actif=True).order_by("nom_complet")
        if self.instance and self.instance.pk:
            binome_qs = binome_qs.exclude(pk=self.instance.pk)
        self.fields["binome"].queryset = binome_qs
        self.fields["binome"].required = False

    def clean(self):
        cleaned = super().clean()
        binome = cleaned.get("binome")
        if binome is not None and self.instance and binome.pk == self.instance.pk:
            raise ValidationError({"binome": "لا يمكن أن يكون العامل بديلاً لنفسه."})
        return cleaned


# ===========================================================================
# RH — Attendance (Pointage)  (BR-RH-05)
# ===========================================================================


class PointageForm(forms.ModelForm):
    """Single-day attendance entry/correction."""

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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["employe"].queryset = Employe.objects.filter(actif=True).order_by(
            "nom_complet"
        )
        self.fields["notes"].required = False

    def clean(self):
        cleaned = super().clean()
        statut = cleaned.get("statut")
        heures_sup = cleaned.get("heures_supplementaires") or 0
        if statut in (Pointage.STATUT_REPOS, Pointage.STATUT_ABSENT) and heures_sup:
            raise ValidationError(
                {
                    "heures_supplementaires": "لا يمكن تسجيل ساعات إضافية في يوم راحة أو غياب."
                }
            )
        return cleaned


class GenererPointagesMoisForm(forms.Form):
    """
    Non-model form: pre-fill a whole month of Pointage rows for one employee
    (PRESENT by default, REPOS on jour_repos_habituel) so HR only has to
    correct the exceptions (absences, congés, heures sup).
    """

    employe = forms.ModelChoiceField(
        queryset=Employe.objects.filter(actif=True).order_by("nom_complet"),
        label="العامل",
    )
    annee = forms.IntegerField(label="السنة", min_value=2000, max_value=2100)
    mois = forms.IntegerField(label="الشهر", min_value=1, max_value=12)


class PointageFilterForm(forms.Form):
    """Non-model filter form for the pointage list view."""

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

    def clean(self):
        cleaned = super().clean()
        date_debut = cleaned.get("date_debut")
        date_fin = cleaned.get("date_fin")
        if date_debut and date_fin and date_debut > date_fin:
            raise ValidationError("تاريخ البداية يجب أن يسبق تاريخ النهاية.")
        return cleaned


# ===========================================================================
# RH — Paid leave (CongeEmploye)  (BR-RH-03)
# ===========================================================================


class CongeEmployeForm(forms.ModelForm):
    """
    Record a paid-leave block. `nb_jours` is computed automatically on save
    (see CongeEmploye.save()) and is therefore excluded from the form.
    """

    class Meta:
        model = CongeEmploye
        fields = ["employe", "date_debut", "date_fin", "motif", "notes"]
        widgets = {
            "date_debut": forms.DateInput(attrs={"type": "date"}),
            "date_fin": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["employe"].queryset = Employe.objects.filter(actif=True).order_by(
            "nom_complet"
        )
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
# RH — Salary advances (AcompteEmploye)  (BR-RH-04)
# ===========================================================================


class AcompteEmployeForm(forms.ModelForm):
    class Meta:
        model = AcompteEmploye
        fields = ["employe", "date", "montant", "mode_paiement", "motif", "notes"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "montant": forms.NumberInput(attrs={"step": "0.01", "min": "0.01"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["employe"].queryset = Employe.objects.filter(actif=True).order_by(
            "nom_complet"
        )
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
# RH — Payroll (BulletinPaie)  (BR-RH-02 / BR-RH-05)
# ===========================================================================


class GenererBulletinPaieForm(forms.Form):
    """
    Non-model form: select employee + period to (re)compute a payslip via
    depenses.utils.calculer_donnees_paie(). The view persists the result.
    """

    employe = forms.ModelChoiceField(
        queryset=Employe.objects.filter(actif=True).order_by("nom_complet"),
        label="العامل",
    )
    annee = forms.IntegerField(label="السنة", min_value=2000, max_value=2100)
    mois = forms.IntegerField(label="الشهر", min_value=1, max_value=12)


class BulletinPaiementForm(forms.ModelForm):
    """Mark a payslip as paid: date + payment method only."""

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
    """Non-model filter form for RH list views (pointage / bulletins)."""

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
