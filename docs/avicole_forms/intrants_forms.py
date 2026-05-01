"""
intrants/forms.py

Forms for master-data management:
  CategorieIntrant, TypeFournisseur, Fournisseur, Batiment, Intrant.
"""

from django import forms

from intrants.models import (
    CategorieIntrant,
    TypeFournisseur,
    Fournisseur,
    Batiment,
    Intrant,
)


class CategorieIntrantForm(forms.ModelForm):
    class Meta:
        model = CategorieIntrant
        fields = ["code", "libelle", "consommable_en_lot", "ordre", "actif"]
        widgets = {
            "code": forms.TextInput(attrs={"placeholder": "Ex : ALIMENT"}),
        }
        help_texts = {
            "code": "Identifiant stable — ne pas renommer les codes ALIMENT, POUSSIN, MEDICAMENT, AUTRE.",
        }


class TypeFournisseurForm(forms.ModelForm):
    class Meta:
        model = TypeFournisseur
        fields = ["code", "libelle", "ordre", "actif"]


class FournisseurForm(forms.ModelForm):
    class Meta:
        model = Fournisseur
        fields = [
            "nom",
            "adresse",
            "wilaya",
            "telephone",
            "telephone_2",
            "email",
            "nif",
            "rc",
            "contact_nom",
            "type_principal",
            "actif",
            "notes",
        ]
        widgets = {
            "adresse": forms.Textarea(attrs={"rows": 2}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Only offer active supplier types.
        self.fields["type_principal"].queryset = TypeFournisseur.objects.filter(actif=True)
        self.fields["type_principal"].required = False


class BatimentForm(forms.ModelForm):
    class Meta:
        model = Batiment
        fields = ["nom", "capacite", "description", "actif"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 2}),
        }


class IntrantForm(forms.ModelForm):
    class Meta:
        model = Intrant
        fields = [
            "designation",
            "categorie",
            "unite_mesure",
            "fournisseurs",
            "seuil_alerte",
            "actif",
            "notes",
        ]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 2}),
            "fournisseurs": forms.CheckboxSelectMultiple(),
            "seuil_alerte": forms.NumberInput(attrs={"step": "0.001", "min": "0"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["categorie"].queryset = CategorieIntrant.objects.filter(actif=True)
        self.fields["fournisseurs"].queryset = Fournisseur.objects.filter(actif=True).order_by("nom")
        self.fields["fournisseurs"].required = False

    def clean_unite_mesure(self):
        """
        BR-INT-05: Unit of measure is immutable once any stock movement
        references the intrant.
        """
        new_unit = self.cleaned_data["unite_mesure"]
        if self.instance and self.instance.pk:
            original_unit = Intrant.objects.values_list("unite_mesure", flat=True).get(
                pk=self.instance.pk
            )
            if original_unit != new_unit:
                # Check whether any stock movement exists.
                from stock.models import StockMouvement
                if StockMouvement.objects.filter(intrant=self.instance).exists():
                    raise forms.ValidationError(
                        "BR-INT-05 : l'unité de mesure ne peut pas être modifiée "
                        "après qu'un mouvement de stock a été enregistré pour cet intrant."
                    )
        return new_unit
