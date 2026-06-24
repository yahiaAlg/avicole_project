"""
production/forms.py

Forms for harvest recording and finished-product catalogue management:
  ProduitFini, ProductionRecord, ProductionLigne.
  CollecteFertilisant, TraitementFertilisant (fertilizer by-product flow).
"""

import datetime
from django import forms
from django.forms import inlineformset_factory
from django.core.exceptions import ValidationError

from production.models import (
    ProduitFini,
    ProductionRecord,
    ProductionLigne,
    CollecteFertilisant,
    TraitementFertilisant,
)
from elevage.models import LotElevage, ParametrageElevage


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
        self.future_date_warning = False

    def clean(self):
        cleaned = super().clean()
        lot = cleaned.get("lot") or self._lot
        nombre = cleaned.get("nombre_oiseaux_abattus")
        date_prod = cleaned.get("date_production")

        if date_prod and date_prod > datetime.date.today():
            self.future_date_warning = True

        # BR-LOT-05: lot must have reached the minimum maturity age before
        # any slaughter/harvest record can be entered (model.clean() is the
        # authoritative check — duplicated here for a clearer form error).
        if lot and not lot.est_mature_pour_vente:
            seuil = ParametrageElevage.get_solo().age_maturite_vente_jours
            raise ValidationError(
                f"BR-LOT-05 : الدفعة لم تبلغ السن الأدنى للبيع/الذبح "
                f"({seuil} يوم). العمر الحالي: {lot.age_jours} يوم."
            )

        if lot and nombre:
            effectif = lot.effectif_vivant
            # On edit, add back this record's own previously saved count so
            # the user isn't blocked from adjusting their own record.
            if (
                self.instance
                and self.instance.pk
                and self.instance.statut == ProductionRecord.STATUT_BROUILLON
            ):
                effectif += self.instance.nombre_oiseaux_abattus or 0
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
                attrs={"rows": 1, "placeholder": "اختياري", "class": "notes-input"}
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
    extra=1,
    min_num=1,
    validate_min=True,
    can_delete=True,
)


# ---------------------------------------------------------------------------
# Fertilisant (by-product): collection then treatment
# ---------------------------------------------------------------------------


class CollecteFertilisantForm(forms.ModelForm):
    """Record one raw manure/fertilizer collection from a building."""

    class Meta:
        model = CollecteFertilisant
        fields = ["batiment", "date_collecte", "quantite_brute_kg", "notes"]
        widgets = {
            "date_collecte": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
            "quantite_brute_kg": forms.NumberInput(
                attrs={"step": "0.001", "min": "0.001"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from intrants.models import Batiment

        # Manure originates from rearing buildings, not from storage entrepôts.
        self.fields["batiment"].queryset = Batiment.objects.filter(
            actif=True,
            type_batiment__in=[Batiment.TYPE_POUSSINIERE, Batiment.TYPE_POULAILLER],
        )
        self.fields["notes"].required = False

    def clean_date_collecte(self):
        date = self.cleaned_data["date_collecte"]
        if date > datetime.date.today():
            raise ValidationError("La date de collecte ne peut pas être dans le futur.")
        return date


class TraitementFertilisantForm(forms.ModelForm):
    """
    Treatment batch form.

    The `collectes` field lets the user pick which untreated
    CollecteFertilisant raw inputs feed into this batch; save() assigns
    `traitement` on every selected collecte and clears it on any collecte
    that was deselected (e.g. moved to a different batch before this one
    was validated).

    Editing is blocked once the batch is VALIDE (stock has already been
    credited by production/signals.py — reopening it would desync stock
    from the recorded inputs).
    """

    collectes = forms.ModelMultipleChoiceField(
        queryset=CollecteFertilisant.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple(),
        label="الكميات الخام المضمنة",
    )

    class Meta:
        model = TraitementFertilisant
        fields = [
            "date_traitement",
            "methode",
            "produit_fini",
            "quantite_obtenue_kg",
            "cout_unitaire_estime",
            "notes",
        ]
        widgets = {
            "date_traitement": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
            "quantite_obtenue_kg": forms.NumberInput(
                attrs={"step": "0.001", "min": "0"}
            ),
            "cout_unitaire_estime": forms.NumberInput(
                attrs={"step": "0.0001", "min": "0"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["produit_fini"].queryset = ProduitFini.objects.filter(
            actif=True, type_produit=ProduitFini.TYPE_FERTILISANT
        )
        self.fields["methode"].required = False
        self.fields["notes"].required = False

        # Selectable: untreated collectes, plus whatever is already linked
        # to *this* batch (so editing doesn't silently drop them).
        qs = CollecteFertilisant.objects.filter(traitement__isnull=True)
        if self.instance and self.instance.pk:
            qs = qs | CollecteFertilisant.objects.filter(traitement=self.instance)
            self.fields["collectes"].initial = self.instance.collectes.all()
        self.fields["collectes"].queryset = qs.order_by("-date_collecte")

    def clean(self):
        cleaned = super().clean()
        if (
            self.instance
            and self.instance.pk
            and self.instance.statut == TraitementFertilisant.STATUT_VALIDE
        ):
            raise ValidationError("Impossible de modifier un traitement déjà validé.")
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=commit)
        if commit:
            self._assigner_collectes(instance)
        else:
            # Defer assignment until the caller invokes save_m2m(), matching
            # the standard ModelForm contract for many-to-many-like fields.
            self.save_m2m = lambda: self._assigner_collectes(instance)
        return instance

    def _assigner_collectes(self, instance):
        selected = self.cleaned_data.get("collectes")
        if selected is None:
            selected = CollecteFertilisant.objects.none()
        selected_ids = [c.pk for c in selected]
        instance.collectes.exclude(pk__in=selected_ids).update(traitement=None)
        selected.update(traitement=instance)
