"""
elevage/forms.py

Forms for lot lifecycle management:
  LotElevage, Mortalite, Consommation.
"""

import datetime
from django import forms
from django.core.exceptions import ValidationError

from elevage.models import LotElevage, Mortalite, Consommation
from intrants.models import Intrant, CategorieIntrant, Batiment, Fournisseur
from achats.models import BLFournisseur


class LotElevageForm(forms.ModelForm):
    """
    Open a new lot d'élevage.

    BR-LOT-01: an initial poussin count + BL Fournisseur (poussins) is required.
    The BL must be in RECU or FACTURE status (already received).
    """

    class Meta:
        model = LotElevage
        fields = [
            "designation",
            "date_ouverture",
            "nombre_poussins_initial",
            "fournisseur_poussins",
            "bl_fournisseur_poussins",
            "batiment",
            "souche",
            "notes",
        ]
        widgets = {
            "date_ouverture": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["fournisseur_poussins"].queryset = Fournisseur.objects.filter(
            actif=True
        ).order_by("nom")
        self.fields["batiment"].queryset = Batiment.objects.filter(actif=True)
        self.fields["bl_fournisseur_poussins"].queryset = BLFournisseur.objects.filter(
            statut__in=[BLFournisseur.STATUT_RECU, BLFournisseur.STATUT_FACTURE]
        ).order_by("-date_bl")
        self.fields["bl_fournisseur_poussins"].required = False

    def clean_nombre_poussins_initial(self):
        val = self.cleaned_data["nombre_poussins_initial"]
        if val < 1:
            raise ValidationError(
                "Le nombre de poussins initial doit être supérieur à 0."
            )
        return val

    def clean_date_ouverture(self):
        date = self.cleaned_data["date_ouverture"]
        if date > datetime.date.today():
            raise ValidationError("La date d'ouverture ne peut pas être dans le futur.")
        return date


class LotFermetureForm(forms.Form):
    """
    Close an open lot.  BR-LOT-04: at least one production record must exist
    (validated at view level before showing this form).
    """

    date_fermeture = forms.DateField(
        label="Date de fermeture",
        widget=forms.DateInput(attrs={"type": "date"}),
        initial=datetime.date.today,
    )
    notes = forms.CharField(
        label="Notes de clôture",
        widget=forms.Textarea(attrs={"rows": 2}),
        required=False,
    )

    def clean_date_fermeture(self):
        date = self.cleaned_data["date_fermeture"]
        if date > datetime.date.today():
            raise ValidationError(
                "La date de fermeture ne peut pas être dans le futur."
            )
        return date


class MortaliteForm(forms.ModelForm):
    """
    Record daily bird deaths on a lot.

    BR-LOT-03: only permitted on open lots (enforced in model.clean() and
    validated here for a better user-facing error message).
    The cumulative mortality cannot exceed the initial bird count.
    """

    class Meta:
        model = Mortalite
        fields = ["lot", "date", "nombre", "cause", "notes"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, lot=None, **kwargs):
        """
        Pass ``lot=<LotElevage instance>`` from the view to pre-select and
        lock the lot field, and to enable cumulative-mortality validation.
        """
        super().__init__(*args, **kwargs)
        self.fields["lot"].queryset = LotElevage.objects.filter(
            statut=LotElevage.STATUT_OUVERT
        )
        if lot:
            self.fields["lot"].initial = lot
            self.fields["lot"].widget = forms.HiddenInput()
            self._lot = lot
        else:
            self._lot = None

    def clean(self):
        cleaned = super().clean()
        lot = cleaned.get("lot") or self._lot
        nombre = cleaned.get("nombre")

        if lot and nombre:
            if lot.statut == LotElevage.STATUT_FERME:
                raise ValidationError(
                    "BR-LOT-03 : impossible d'ajouter une mortalité sur un lot fermé."
                )
            # Cumulative-mortality guard: total deaths cannot exceed initial count.
            total_actuel = lot.total_mortalite
            # On update, subtract the current record's existing value.
            if self.instance and self.instance.pk:
                total_actuel -= self.instance.nombre
            if total_actuel + nombre > lot.nombre_poussins_initial:
                raise ValidationError(
                    f"La mortalité cumulée ({total_actuel + nombre}) dépasse "
                    f"le nombre initial de poussins ({lot.nombre_poussins_initial})."
                )
        return cleaned


class ConsommationForm(forms.ModelForm):
    """
    Record daily feed or medicine consumption attributed to a lot.

    Business rules enforced here:
      BR-LOT-03  : lot must be open.
      BR-INT-03  : requested quantity cannot exceed available stock.
    """

    class Meta:
        model = Consommation
        fields = ["lot", "date", "intrant", "quantite", "notes"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
            "quantite": forms.NumberInput(attrs={"step": "0.001", "min": "0.001"}),
        }

    def __init__(self, *args, lot=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["lot"].queryset = LotElevage.objects.filter(
            statut=LotElevage.STATUT_OUVERT
        )
        # Only consumable intrants (feed, medicine) are allowed.
        self.fields["intrant"].queryset = Intrant.objects.filter(
            categorie__consommable_en_lot=True,
            actif=True,
        ).select_related("categorie")
        if lot:
            self.fields["lot"].initial = lot
            self.fields["lot"].widget = forms.HiddenInput()
            self._lot = lot
        else:
            self._lot = None

    def clean(self):
        cleaned = super().clean()
        lot = cleaned.get("lot") or self._lot
        intrant = cleaned.get("intrant")
        quantite = cleaned.get("quantite")

        if lot and lot.statut == LotElevage.STATUT_FERME:
            raise ValidationError(
                "BR-LOT-03 : impossible d'enregistrer une consommation sur un lot fermé."
            )

        if intrant and quantite:
            stock_dispo = intrant.quantite_en_stock
            # On update, add back the already-recorded quantity.
            if self.instance and self.instance.pk:
                if self.instance.intrant_id == intrant.pk:
                    stock_dispo += self.instance.quantite
            if quantite > stock_dispo:
                raise ValidationError(
                    f"BR-INT-03 : stock insuffisant pour «\u202f{intrant.designation}\u202f». "
                    f"Disponible\u202f: {stock_dispo} {intrant.unite_mesure} — "
                    f"Demandé\u202f: {quantite} {intrant.unite_mesure}."
                )
        return cleaned
