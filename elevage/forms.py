"""
elevage/forms.py

Forms for lot lifecycle management:
  LotElevage, Mortalite, Consommation.
"""

import datetime
from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q

from elevage.models import (
    LotElevage,
    Mortalite,
    Consommation,
    TransfertLot,
    RecolteOeufs,
    PeseeEchantillon,
)
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
        label="تاريخ الإغلاق",
        widget=forms.DateInput(attrs={"type": "date"}),
        initial=datetime.date.today,
    )
    notes = forms.CharField(
        label="ملاحظات الإغلاق",
        widget=forms.Textarea(attrs={"rows": 2}),
        required=False,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.future_date_warning = False

    def clean_date_fermeture(self):
        date = self.cleaned_data["date_fermeture"]
        if date > datetime.date.today():
            self.future_date_warning = True
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
        intrant_qs = Intrant.objects.filter(
            categorie__consommable_en_lot=True,
            actif=True,
        ).select_related("categorie")
        if lot:
            # Narrow further to the lot's current life-stage (chicks vs grown
            # birds) — items flagged STADE_TOUS remain available everywhere.
            stade_attendu = lot.stade_intrant_attendu
            intrant_qs = intrant_qs.filter(
                Q(stade=stade_attendu) | Q(stade=Intrant.STADE_TOUS)
            )
        self.fields["intrant"].queryset = intrant_qs
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


class TransfertLotForm(forms.ModelForm):
    """
    Move a lot from its current building to another, in one of three modes:

    MODE_FULL        — whole live flock relocates; baseline unchanged.
    MODE_SPLIT_NEW   — partial move; a child lot is auto-created at destination
                       (source baseline decreases by effectif_transfere).
    MODE_SPLIT_MERGE — partial move; birds merge into an existing open lot at
                       destination (source baseline decreases, dest increases).

    batiment_origine is pre-filled and locked from the lot's current batiment.
    lot_destination is only required for MODE_SPLIT_MERGE.
    designation_lot_enfant is optional for MODE_SPLIT_NEW (auto-generated if blank).
    """

    class Meta:
        model = TransfertLot
        fields = [
            "lot",
            "batiment_origine",
            "batiment_destination",
            "date_transfert",
            "age_jours_transfert",
            "effectif_transfere",
            "mode",
            "lot_destination",
            "designation_lot_enfant",
            "motif",
            "notes",
        ]
        widgets = {
            "date_transfert": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
            "designation_lot_enfant": forms.TextInput(),
            # mode rendered as hidden input — the template uses card radio buttons
            # that write to this field via JS so the value still POST-es correctly.
            "mode": forms.HiddenInput(),
        }

    def __init__(self, *args, lot=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["lot"].queryset = LotElevage.objects.filter(
            statut=LotElevage.STATUT_OUVERT
        )
        self.fields["batiment_destination"].queryset = Batiment.objects.filter(
            actif=True
        )
        self.fields["motif"].required = False
        self.fields["notes"].required = False
        self.fields["lot_destination"].required = False
        self.fields["designation_lot_enfant"].required = False

        if lot:
            self.fields["lot"].initial = lot
            self.fields["lot"].widget = forms.HiddenInput()
            self.fields["batiment_origine"].initial = lot.batiment
            self.fields["batiment_origine"].widget = forms.HiddenInput()
            self.fields["batiment_destination"].queryset = self.fields[
                "batiment_destination"
            ].queryset.exclude(pk=lot.batiment_id)
            self.fields["age_jours_transfert"].initial = lot.age_jours
            self.fields["effectif_transfere"].initial = lot.effectif_vivant
            # lot_destination: all other open lots (template JS filters by building)
            self.fields["lot_destination"].queryset = LotElevage.objects.filter(
                statut=LotElevage.STATUT_OUVERT
            ).exclude(pk=lot.pk)
            self._lot = lot
        else:
            self._lot = None

    def clean(self):
        cleaned = super().clean()
        lot = cleaned.get("lot") or self._lot
        origine = cleaned.get("batiment_origine")
        destination = cleaned.get("batiment_destination")
        effectif = cleaned.get("effectif_transfere")
        mode = cleaned.get("mode") or TransfertLot.MODE_FULL
        lot_dest = cleaned.get("lot_destination")

        if lot and lot.statut == LotElevage.STATUT_FERME:
            raise ValidationError("Impossible de transférer un lot fermé.")

        if origine and destination and origine.pk == destination.pk:
            raise ValidationError(
                "Le bâtiment de destination doit être différent du bâtiment d'origine."
            )

        if lot and effectif and effectif > lot.effectif_vivant:
            raise ValidationError(
                f"L'effectif transféré ({effectif}) dépasse l'effectif vivant "
                f"du lot ({lot.effectif_vivant})."
            )

        # Split modes: effectif must be strictly LESS than full live count
        if mode in (TransfertLot.MODE_SPLIT_NEW, TransfertLot.MODE_SPLIT_MERGE):
            if lot and effectif and effectif >= lot.effectif_vivant:
                raise ValidationError(
                    "في نمط التقسيم، يجب أن يكون عدد الطيور المنقولة أقل من العدد الحي الكلي "
                    f"({lot.effectif_vivant} طير). للنقل الكامل، اختر «نقل كامل»."
                )

        # Merge mode: lot_destination is required and must be valid
        if mode == TransfertLot.MODE_SPLIT_MERGE:
            if not lot_dest:
                raise ValidationError("يجب تحديد الدفعة الوجهة عند اختيار نمط الدمج.")
            if lot and lot_dest.pk == lot.pk:
                raise ValidationError("لا يمكن دمج الدفعة مع نفسها.")
            if lot_dest.statut == LotElevage.STATUT_FERME:
                raise ValidationError("الدفعة الوجهة مغلقة — اختر دفعة مفتوحة.")
            if destination and lot_dest.batiment_id != destination.pk:
                raise ValidationError(
                    "الدفعة الوجهة يجب أن تكون في المبنى المختار كوجهة النقل."
                )

        return cleaned


class PeseeEchantillonForm(forms.ModelForm):
    """Record a sample weighing (birds or eggs) for a lot."""

    class Meta:
        model = PeseeEchantillon
        fields = [
            "lot",
            "date",
            "type_pesee",
            "nombre_sujets",
            "poids_total_g",
            "notes",
        ]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
            "poids_total_g": forms.NumberInput(attrs={"step": "0.01", "min": "0.01"}),
        }

    def __init__(self, *args, lot=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["lot"].queryset = LotElevage.objects.filter(
            statut=LotElevage.STATUT_OUVERT
        )
        self.fields["notes"].required = False
        if lot:
            self.fields["lot"].initial = lot
            self.fields["lot"].widget = forms.HiddenInput()
            self._lot = lot
        else:
            self._lot = None

    def clean_date(self):
        date = self.cleaned_data["date"]
        if date > datetime.date.today():
            raise ValidationError("La date de pesée ne peut pas être dans le futur.")
        return date


class RecolteOeufsForm(forms.ModelForm):
    """
    Record a daily egg-collection event for a lot in laying phase.

    BR-LOT-03 equivalent: only permitted on open lots (model.clean() also
    enforces this — duplicated here for a form-level error message).
    """

    class Meta:
        model = RecolteOeufs
        fields = ["lot", "date", "nombre_oeufs", "pesee", "notes"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, lot=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["lot"].queryset = LotElevage.objects.filter(
            statut=LotElevage.STATUT_OUVERT
        )
        self.fields["pesee"].required = False
        self.fields["notes"].required = False
        if lot:
            self.fields["lot"].initial = lot
            self.fields["lot"].widget = forms.HiddenInput()
            self.fields["pesee"].queryset = PeseeEchantillon.objects.filter(
                lot=lot, type_pesee=PeseeEchantillon.TYPE_OEUFS
            ).order_by("-date")
            self._lot = lot
        else:
            self._lot = None

    def clean(self):
        cleaned = super().clean()
        lot = cleaned.get("lot") or self._lot
        if lot and lot.statut == LotElevage.STATUT_FERME:
            raise ValidationError(
                "Impossible d'enregistrer une récolte d'œufs sur un lot fermé."
            )
        return cleaned
