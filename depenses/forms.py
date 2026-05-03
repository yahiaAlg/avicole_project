"""
depenses/forms.py

Forms for operational expense tracking:
  CategorieDepense, Depense.

Business rules enforced here:
  BR-DEP-01  Goods-type facture fournisseur NEVER linked to a dépense.
  BR-DEP-03  Only Service-type supplier invoices may be optionally linked.
  BR-DEP-04  Dépenses may optionally be attributed to a lot for profitability.
"""

import datetime
from django import forms
from django.core.exceptions import ValidationError

from depenses.models import CategorieDepense, Depense
from elevage.models import LotElevage


class CategorieDepenseForm(forms.ModelForm):
    class Meta:
        model = CategorieDepense
        fields = ["code", "libelle", "description", "ordre", "actif"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 2}),
            "code": forms.TextInput(attrs={"placeholder": "Ex : ENERGIE"}),
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
                attrs={"placeholder": "N° de la pièce justificative"}
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
            "Optionnel — uniquement pour les factures fournisseur de type Service (BR-DEP-03)."
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
        empty_label="Toutes les catégories",
        label="Catégorie",
    )
    date_debut = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        label="Date de début",
    )
    date_fin = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        label="Date de fin",
    )
    lot = forms.ModelChoiceField(
        queryset=LotElevage.objects.order_by("-date_ouverture"),
        required=False,
        empty_label="Tous les lots",
        label="Lot attribué",
    )
    mode_paiement = forms.ChoiceField(
        choices=[("", "Tous les modes")] + list(Depense.MODE_CHOICES),
        required=False,
        label="Mode de paiement",
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
        label="Du",
    )
    date_fin = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        label="Au",
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
