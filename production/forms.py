"""
production/forms.py

Forms for harvest recording and finished-product catalogue management:
  ProduitFini, ProductionRecord, ProductionLigne.
"""

import datetime
from django import forms
from django.forms import inlineformset_factory
from django.core.exceptions import ValidationError

from production.models import ProduitFini, ProductionRecord, ProductionLigne
from elevage.models import LotElevage


class ProduitFiniForm(forms.ModelForm):
    class Meta:
        model = ProduitFini
        fields = [
            "designation",
            "type_produit",
            "unite_mesure",
            "prix_vente_defaut",
            "actif",
            "notes",
        ]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 2}),
            "prix_vente_defaut": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
        }


class ProductionRecordForm(forms.ModelForm):
    """
    Header form for a harvest / production event.

    BR-LOT-04 context: closing a lot requires at least one production record —
    this is enforced in the view after saving this form.

    nombre_oiseaux_abattus cannot exceed the lot's current effectif vivant.
    poids_moyen_kg is auto-computed from poids_total_kg at model.save().
    """

    class Meta:
        model = ProductionRecord
        fields = [
            "lot",
            "date_production",
            "nombre_oiseaux_abattus",
            "poids_total_kg",
            "notes",
        ]
        widgets = {
            "date_production": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
            "poids_total_kg": forms.NumberInput(attrs={"step": "0.001", "min": "0"}),
        }

    def __init__(self, *args, lot=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Restrict to open lots only; a closed lot cannot produce.
        self.fields["lot"].queryset = LotElevage.objects.filter(
            statut=LotElevage.STATUT_OUVERT
        ).order_by("-date_ouverture")
        if lot:
            self.fields["lot"].initial = lot
            self.fields["lot"].widget = forms.HiddenInput()
            self._lot = lot
        else:
            self._lot = None

    def clean(self):
        cleaned = super().clean()
        lot = cleaned.get("lot") or self._lot
        nombre = cleaned.get("nombre_oiseaux_abattus")
        date_prod = cleaned.get("date_production")

        if date_prod and date_prod > datetime.date.today():
            self.add_error(
                "date_production",
                "La date de production ne peut pas être dans le futur.",
            )

        if lot and nombre:
            effectif = lot.effectif_vivant
            if nombre > effectif:
                raise ValidationError(
                    f"Le nombre d'oiseaux abattus ({nombre}) dépasse "
                    f"l'effectif vivant actuel du lot ({effectif})."
                )
        return cleaned


class ProductionLigneForm(forms.ModelForm):
    class Meta:
        model = ProductionLigne
        fields = [
            "produit_fini",
            "quantite",
            "poids_unitaire_kg",
            "cout_unitaire_estime",
            "notes",
        ]
        widgets = {
            "quantite": forms.NumberInput(attrs={"step": "0.001", "min": "0.001"}),
            "poids_unitaire_kg": forms.NumberInput(attrs={"step": "0.001", "min": "0"}),
            "cout_unitaire_estime": forms.NumberInput(
                attrs={"step": "0.0001", "min": "0"}
            ),
            "notes": forms.Textarea(
                attrs={"rows": 1, "placeholder": "Optionnel", "class": "notes-input"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["produit_fini"].queryset = ProduitFini.objects.filter(actif=True)
        self.fields["poids_unitaire_kg"].required = False
        self.fields["cout_unitaire_estime"].required = False
        self.fields["notes"].required = False


# Inline formset: one ProductionRecord → many ProductionLignes.
ProductionLigneFormSet = inlineformset_factory(
    ProductionRecord,
    ProductionLigne,
    form=ProductionLigneForm,
    extra=3,
    min_num=1,
    validate_min=True,
    can_delete=True,
)
