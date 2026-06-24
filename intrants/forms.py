"""
intrants/forms.py

Forms for master-data management:
  CategorieIntrant, TypeFournisseur, Fournisseur, Batiment, Intrant.
"""

from django import forms

from intrants.models import (
    CategorieIntrant,
    CategorieQualite,
    TypeFournisseur,
    Fournisseur,
    Batiment,
    Intrant,
)


class CategorieQualiteForm(forms.ModelForm):
    class Meta:
        model = CategorieQualite
        fields = [
            "code",
            "libelle",
            "type_pesee",
            "poids_min",
            "poids_max",
            "ordre",
            "actif",
        ]
        widgets = {
            "poids_min": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "poids_max": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
        }


class CategorieIntrantForm(forms.ModelForm):
    class Meta:
        model = CategorieIntrant
        fields = ["code", "libelle", "consommable_en_lot", "ordre", "actif"]
        widgets = {
            "code": forms.TextInput(attrs={"placeholder": "مثال: ALIMENT"}),
        }
        help_texts = {
            "code": "معرف ثابت — لا تعيد تسمية الرموز ALIMENT, POUSSIN, MEDICAMENT, AUTRE.",
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
        self.fields["type_principal"].queryset = TypeFournisseur.objects.filter(
            actif=True
        )
        self.fields["type_principal"].required = False


class BatimentForm(forms.ModelForm):
    class Meta:
        model = Batiment
        fields = [
            "nom",
            "type_batiment",
            "categorie_stockage",
            "capacite",
            "description",
            "actif",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["categorie_stockage"].required = False

    def clean(self):
        cleaned = super().clean()
        type_batiment = cleaned.get("type_batiment")
        categorie_stockage = cleaned.get("categorie_stockage")

        if type_batiment == Batiment.TYPE_ENTREPOT and not categorie_stockage:
            self.add_error(
                "categorie_stockage",
                "مطلوب تحديد نوع التخزين عندما يكون المبنى مستودعاً.",
            )
        if type_batiment != Batiment.TYPE_ENTREPOT and categorie_stockage:
            self.add_error(
                "categorie_stockage",
                "اترك هذا الحقل فارغاً إلا إذا كان المبنى مستودعاً.",
            )
        return cleaned


class IntrantForm(forms.ModelForm):
    class Meta:
        model = Intrant
        fields = [
            "designation",
            "categorie",
            "stade",
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
        self.fields["fournisseurs"].queryset = Fournisseur.objects.filter(
            actif=True
        ).order_by("nom")
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
