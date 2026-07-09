"""
elevage/forms.py

Forms for lot lifecycle management:
  LotElevage, Mortalite, Consommation.
"""

import datetime
from decimal import Decimal
from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.forms import inlineformset_factory

from elevage.models import (
    LotElevage,
    Mortalite,
    Consommation,
    TransfertLot,
    RecolteOeufs,
    PeseeEchantillon,
    ProductionAliment,
    FormuleAliment,
    FormuleAlimentLigne,
    RetraitOeufs,
)
from intrants.models import Intrant, CategorieIntrant, Batiment, Fournisseur
from achats.models import BLFournisseur
from clients.models import Client


class LotElevageForm(forms.ModelForm):
    """
    Open a new lot d'élevage.

    BR-LOT-01: an initial poussin count + BL Fournisseur (poussins) is required.
    The BL must be in RECU or FACTURE status (already received).

    BR-BRA-01: a lot's branche is DERIVED from its bâtiment (denormalized
    in LotElevage.save()), so there is no `branche` field here. Pass
    `branche=<Branche instance>` from the view when the current user is
    locked to one branch (chef de branche / opérateur, BR-BRA-02) to scope
    the `batiment` and `bl_fournisseur_poussins` choices to that branche.
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

    def __init__(self, *args, branche=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["fournisseur_poussins"].queryset = Fournisseur.objects.filter(
            actif=True
        ).order_by("nom")
        batiment_qs = Batiment.objects.filter(actif=True)
        bl_qs = BLFournisseur.objects.filter(
            statut__in=[BLFournisseur.STATUT_RECU, BLFournisseur.STATUT_FACTURE]
        )
        if branche:
            # BR-BRA-01: the new lot's branche will be derived from
            # `batiment`, so only offer buildings (and their delivery
            # notes) already in this branche.
            batiment_qs = batiment_qs.filter(branche=branche)
            bl_qs = bl_qs.filter(branche=branche)
        self.fields["batiment"].queryset = batiment_qs
        self.fields["bl_fournisseur_poussins"].queryset = bl_qs.order_by("-date_bl")
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

    BR-BRA-01: Mortalite.branche is DERIVED from `lot.branche` (no stored
    column). Pass `branche=<Branche instance>` from the view when the
    current user is locked to one branch (chef de branche / opérateur,
    BR-BRA-02) and no specific `lot` is already known, to scope the `lot`
    choices to that branche.
    """

    class Meta:
        model = Mortalite
        fields = ["lot", "date", "nombre", "cause", "notes"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, lot=None, branche=None, **kwargs):
        """
        Pass ``lot=<LotElevage instance>`` from the view to pre-select and
        lock the lot field, and to enable cumulative-mortality validation.
        """
        super().__init__(*args, **kwargs)
        lot_qs = LotElevage.objects.filter(statut=LotElevage.STATUT_OUVERT)
        if branche:
            lot_qs = lot_qs.filter(branche=branche)
        self.fields["lot"].queryset = lot_qs
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


class _BaseConsommationForm(forms.ModelForm):
    """
    Shared plumbing for the two Consommation forms (feed vs médicament).

    Both forms record the *same* underlying Consommation model — only the
    `intrant` catégorie scope differs — so lot-queryset scoping, life-stage
    filtering, and the BR-LOT-03 / BR-INT-03 validations live here once.
    Concrete subclasses only need to implement `_intrant_base_queryset()`.

    BR-BRA-01: Consommation.branche is DERIVED from `lot.branche` (no
    stored column). Pass `branche=<Branche instance>` from the view when
    the current user is locked to one branch and no specific `lot` is
    already known, to scope the `lot` choices to that branche.
    """

    class Meta:
        model = Consommation
        fields = ["lot", "date", "intrant", "quantite", "notes"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
            "quantite": forms.NumberInput(attrs={"step": "0.001", "min": "0.001"}),
        }

    def _intrant_base_queryset(self):
        """Subclasses return the catégorie-scoped Intrant queryset."""
        raise NotImplementedError

    def __init__(self, *args, lot=None, branche=None, **kwargs):
        super().__init__(*args, **kwargs)
        lot_qs = LotElevage.objects.filter(statut=LotElevage.STATUT_OUVERT)
        if branche:
            lot_qs = lot_qs.filter(branche=branche)
        self.fields["lot"].queryset = lot_qs
        intrant_qs = self._intrant_base_queryset()
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
            # v1.4 (BR-BRA-07): Intrant.quantite_en_stock() takes an optional
            # `branche` — omitting it sums stock across every branch, but a
            # Consommation can only ever draw from its own lot's branche, so
            # it must be passed explicitly here.
            stock_dispo = intrant.quantite_en_stock(
                branche=lot.branche if lot else None
            )
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


class ConsommationForm(_BaseConsommationForm):
    """
    Record daily feed consumption attributed to a lot.

    Intrant choices are restricted to catégorie ALIMENT (finished feed only)
    — médicaments, vaccins, vitamines, antibiotiques et désinfectants are
    also `consommable_en_lot`, but they belong in ConsommationMedicamentForm
    instead. Separation of concerns: one section, one form, one template per
    catégorie of consumption on the lot detail page.

    Business rules enforced in _BaseConsommationForm:
      BR-LOT-03  : lot must be open.
      BR-INT-03  : requested quantity cannot exceed available stock.
    """

    def _intrant_base_queryset(self):
        intrant_qs = Intrant.objects.filter(
            categorie__code="ALIMENT",
            actif=True,
        ).select_related("categorie")
        # A lot only ever consumes a *finished* feed (e.g. "Aliment Démarrage
        # Poussin") — never a raw ingredient (MAIS, SOJA, Phosphate, CMV…)
        # that only exists to be milled into one via
        # FormuleAliment/ProductionAliment. Both share the same ALIMENT
        # catégorie, so raw ingredients are told apart here as "any ALIMENT
        # intrant that appears as a FormuleAlimentLigne component somewhere"
        # and excluded from this dropdown.
        raw_ingredient_ids = FormuleAlimentLigne.objects.values_list(
            "intrant_id", flat=True
        ).distinct()
        return intrant_qs.exclude(pk__in=raw_ingredient_ids)


class ConsommationMedicamentForm(_BaseConsommationForm):
    """
    Record médicament / vaccin / vitamine / antibiotique / désinfectant
    consumption attributed to a lot.

    Intrant choices cover every catégorie flagged `consommable_en_lot=True`
    *except* ALIMENT (that's ConsommationForm's job), so this dropdown only
    ever shows non-feed consommables — a distinct section/form/template
    from feed consumption, per separation of concerns.

    Costing (BR-request, mirrors ProductionAliment.prix_unitaire): this is
    the only Consommation form exposing `prix_unitaire` — a straight
    per-unit cost known at entry time (auto-expensed immediately, see
    views._auto_creer_depense_consommation_medicament). Left at 0 (the
    default), the record instead awaits a later consolidated team/vet
    payment batched across several records — see
    views.consommation_medicament_paiement_create.
    """

    class Meta(_BaseConsommationForm.Meta):
        fields = ["lot", "date", "intrant", "quantite", "prix_unitaire", "notes"]
        widgets = {
            **_BaseConsommationForm.Meta.widgets,
            "prix_unitaire": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["prix_unitaire"].required = False
        self.fields["prix_unitaire"].label = "سعر الوحدة — اختياري"
        self.fields["prix_unitaire"].help_text = (
            "أدخله عند معرفة السعر فوراً (يُنشئ مصروفاً تلقائياً). اتركه 0 "
            "لدفع أجرة الطبيب/الفريق لاحقاً دفعة واحدة عن عدة استهلاكات "
            "(انظر: استهلاكات الأدوية ← دفع أجرة)."
        )

    def clean_prix_unitaire(self):
        return self.cleaned_data.get("prix_unitaire") or Decimal("0")

    def _intrant_base_queryset(self):
        return (
            Intrant.objects.filter(categorie__consommable_en_lot=True, actif=True)
            .exclude(categorie__code="ALIMENT")
            .select_related("categorie")
        )


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

    BR-BRA-01: TransfertLot.branche is DERIVED from `lot.branche`. A
    transfer always stays within one branche — `batiment_destination` and
    `lot_destination` are scoped to the same branche as the source `lot`
    automatically once `lot` is known; pass `branche=<Branche instance>`
    from the view to additionally scope the initial `lot` choices when no
    specific lot is pre-selected.
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

    def __init__(self, *args, lot=None, branche=None, **kwargs):
        super().__init__(*args, **kwargs)
        lot_qs = LotElevage.objects.filter(statut=LotElevage.STATUT_OUVERT)
        if branche:
            lot_qs = lot_qs.filter(branche=branche)
        self.fields["lot"].queryset = lot_qs
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
            self.fields["batiment_destination"].queryset = (
                self.fields["batiment_destination"].queryset.exclude(pk=lot.batiment_id)
                # BR-BRA-01: a transfer never crosses branches — destination
                # building must be in the same branche as the source lot.
                .filter(branche=lot.branche)
            )
            self.fields["age_jours_transfert"].initial = lot.age_jours
            self.fields["effectif_transfere"].initial = lot.effectif_vivant
            # lot_destination: all other open lots in the same branche
            # (template JS filters further by building).
            self.fields["lot_destination"].queryset = LotElevage.objects.filter(
                statut=LotElevage.STATUT_OUVERT, branche=lot.branche
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
    """
    Record a sample weighing (birds or eggs) for a lot.

    BR-BRA-01: PeseeEchantillon.branche is DERIVED from `lot.branche` (no
    stored column). Pass `branche=<Branche instance>` from the view when
    the current user is locked to one branch and no specific `lot` is
    already known, to scope the `lot` choices to that branche.
    """

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

    def __init__(self, *args, lot=None, branche=None, **kwargs):
        super().__init__(*args, **kwargs)
        lot_qs = LotElevage.objects.filter(statut=LotElevage.STATUT_OUVERT)
        if branche:
            lot_qs = lot_qs.filter(branche=branche)
        self.fields["lot"].queryset = lot_qs
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

    BR-BRA-01: RecolteOeufs.branche is DERIVED from `lot.branche` (no
    stored column). Pass `branche=<Branche instance>` from the view when
    the current user is locked to one branch and no specific `lot` is
    already known, to scope the `lot` choices to that branche.
    """

    class Meta:
        model = RecolteOeufs
        fields = ["lot", "date", "nombre_oeufs", "pesee", "notes"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, lot=None, branche=None, **kwargs):
        super().__init__(*args, **kwargs)
        lot_qs = LotElevage.objects.filter(statut=LotElevage.STATUT_OUVERT)
        if branche:
            lot_qs = lot_qs.filter(branche=branche)
        self.fields["lot"].queryset = lot_qs
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


class FormuleAlimentForm(forms.ModelForm):
    """
    Create/edit a feed recipe (which finished feed it produces + its
    ingredient lines, handled separately by FormuleAlimentLigneFormSet
    below). This is what populates the "التركيبة" dropdown on
    ProductionAlimentForm — that dropdown has nothing to show until at
    least one FormuleAliment exists.
    """

    class Meta:
        model = FormuleAliment
        fields = ["nom", "intrant_produit", "actif", "notes"]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["intrant_produit"].queryset = Intrant.objects.filter(
            categorie__code="ALIMENT", actif=True
        )
        self.fields["notes"].required = False


class FormuleAlimentLigneForm(forms.ModelForm):
    """One ingredient row of a FormuleAliment (kg per 100kg produced)."""

    class Meta:
        model = FormuleAlimentLigne
        fields = ["intrant", "proportion_kg"]
        widgets = {
            "proportion_kg": forms.NumberInput(attrs={"step": "0.001", "min": "0.001"}),
        }

    def __init__(self, *args, parent_formule=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Ingredients can be ANY active intrant — including raw feed
        # components (MAIS, SOJA, Phosphate, CMV…), which are themselves
        # catégorie ALIMENT — EXCEPT another *finished* feed (an intrant
        # already produced by some FormuleAliment), since that would let a
        # recipe list a finished feed as its own component. Excluding the
        # whole ALIMENT category here was the bug: it hid every raw
        # ingredient too, leaving only vitamines/médicaments/vaccins in the
        # dropdown.
        excluded_ids = set(
            FormuleAliment.objects.exclude(
                pk=parent_formule.pk if parent_formule and parent_formule.pk else None
            ).values_list("intrant_produit_id", flat=True)
        )
        if parent_formule and parent_formule.intrant_produit_id:
            excluded_ids.add(parent_formule.intrant_produit_id)
        self.fields["intrant"].queryset = Intrant.objects.filter(actif=True).exclude(
            pk__in=excluded_ids
        )


class FormuleAlimentLigneFormSetBase(forms.BaseInlineFormSet):
    """Feeds `parent_formule` (the FormuleAliment being edited/created) down
    to every child FormuleAlimentLigneForm so it can exclude that recipe's
    own output feed from its ingredient choices — see
    FormuleAlimentLigneForm.__init__."""

    def get_form_kwargs(self, index):
        kwargs = super().get_form_kwargs(index)
        kwargs["parent_formule"] = self.instance
        return kwargs


FormuleAlimentLigneFormSet = inlineformset_factory(
    FormuleAliment,
    FormuleAlimentLigne,
    form=FormuleAlimentLigneForm,
    formset=FormuleAlimentLigneFormSetBase,
    extra=3,
    can_delete=True,
)


class ProductionAlimentForm(forms.ModelForm):
    """
    Replenish a finished feed's stock: bare quantity fast-path (the common
    case — optionally priced via `prix_unitaire`), or via a FormuleAliment
    recipe (in which case ingredient Intrants are also debited, and their
    own stock cost is used to price the feed automatically — handled in
    signals.py, not here).
    """

    class Meta:
        model = ProductionAliment
        fields = [
            "branche",
            "date",
            "formule",
            "intrant_produit",
            "quantite_produite_kg",
            "prix_unitaire",
            "notes",
        ]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
            "quantite_produite_kg": forms.NumberInput(
                attrs={"step": "0.001", "min": "0.001"}
            ),
            "prix_unitaire": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
        }

    def __init__(self, *args, branche=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["formule"].required = False
        self.fields["formule"].queryset = FormuleAliment.objects.filter(actif=True)
        self.fields["intrant_produit"].queryset = Intrant.objects.filter(
            categorie__code="ALIMENT", actif=True
        )
        self.fields["prix_unitaire"].required = False
        self.fields["prix_unitaire"].label = "سعر الوحدة (د.ج/كغ) — اختياري"
        self.fields["prix_unitaire"].help_text = (
            "اترك 0 إن كنت تستعمل تركيبة — تُحسب التكلفة تلقائياً من " "مكوّناتها."
        )
        if branche:
            self.fields["branche"].initial = branche
            self.fields["branche"].widget = forms.HiddenInput()
        if not self.initial.get("date"):
            self.fields["date"].initial = datetime.date.today()

    def clean_prix_unitaire(self):
        return self.cleaned_data.get("prix_unitaire") or Decimal("0")

    def clean(self):
        cleaned = super().clean()
        formule = cleaned.get("formule")
        intrant_produit = cleaned.get("intrant_produit")
        if (
            formule
            and intrant_produit
            and formule.intrant_produit_id != intrant_produit.pk
        ):
            raise ValidationError(
                "التركيبة المختارة تُنتج علفاً مختلفاً عن العلف المحدد."
            )
        return cleaned


class ProductionAlimentPaiementForm(forms.Form):
    """
    Batch-pay the feed-mill worker for one or more formule-based
    ProductionAliment records (see views.production_aliment_paiement_create).

    Not a ModelForm: it doesn't edit a ProductionAliment itself, it collects
    the info needed to create ONE consolidated depenses.Depense — the actual
    ProductionAliment selection travels as hidden `production_ids` fields in
    the template, validated/looked-up server-side in the view (never trusted
    from cleaned_data here).
    """

    date = forms.DateField(
        label="تاريخ الدفع",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    prix_unitaire = forms.DecimalField(
        label="سعر الوحدة (د.ج/كغ)",
        max_digits=12,
        decimal_places=4,
        min_value=Decimal("0.01"),
        # NOTE: widget min is "0", NOT "0.01" — a fractional HTML min combined
        # with step="0.01" trips browsers' floating-point stepMismatch check
        # on perfectly valid values (e.g. entering 50 got rejected with
        # "nearest valid values are 49.9901 / 50.0001"). min="0" sidesteps
        # that; the real "must be > 0" rule is still enforced server-side via
        # min_value above (same pattern as ProductionAlimentForm.prix_unitaire).
        widget=forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
        help_text="أجرة تصنيع الكيلوغرام الواحد — يُضرب في إجمالي الكمية المختارة.",
    )
    mode_paiement = forms.ChoiceField(label="طريقة الدفع")
    notes = forms.CharField(
        label="ملاحظات",
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from depenses.models import Depense

        self.fields["mode_paiement"].choices = Depense.MODE_CHOICES
        if not self.initial.get("date"):
            self.fields["date"].initial = datetime.date.today()
        if not self.initial.get("mode_paiement"):
            self.fields["mode_paiement"].initial = Depense.MODE_ESPECES


class ConsommationMedicamentPaiementForm(forms.Form):
    """
    Batch-pay the veterinarian/team for one or more Consommation (médicament)
    records left unpriced at entry (see
    views.consommation_medicament_paiement_create). Mirrors
    ProductionAlimentPaiementForm exactly — same rationale.

    Not a ModelForm: it doesn't edit a Consommation itself, it collects the
    info needed to create ONE consolidated depenses.Depense — the actual
    Consommation selection travels as hidden `consommation_ids` fields in
    the template, validated/looked-up server-side in the view (never
    trusted from cleaned_data here).
    """

    date = forms.DateField(
        label="تاريخ الدفع",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    # BR-request: a batch can mix several non-homogeneous médicaments/vaccins
    # (e.g. one vet visit covering both a vaccine and an antibiotic). Forcing
    # a single per-chick prix_unitaire in that case is often wrong — the vet/
    # team fee is frequently a single lump sum for the whole visit, not a
    # clean per-bird rate. So the form now offers TWO mutually-exclusive ways
    # to price the batch, selected via `mode_montant`:
    #   "unitaire" → prix_unitaire × total_effectif (unchanged legacy path)
    #   "direct"   → montant_direct entered as-is, for the whole batch
    # Exactly one of the two amount fields must be filled in, enforced in
    # clean() below — never both, never neither.
    MODE_UNITAIRE = "unitaire"
    MODE_DIRECT = "direct"
    MODE_CHOICES_MONTANT = [
        (MODE_UNITAIRE, "سعر الوحدة لكل طير"),
        (MODE_DIRECT, "مبلغ إجمالي مباشر"),
    ]

    mode_montant = forms.ChoiceField(
        label="طريقة التسعير",
        choices=MODE_CHOICES_MONTANT,
        initial=MODE_UNITAIRE,
        widget=forms.RadioSelect,
    )
    prix_unitaire = forms.DecimalField(
        label="سعر الوحدة",
        max_digits=12,
        decimal_places=4,
        required=False,
        min_value=Decimal("0"),
        # NOTE: widget min is "0", NOT "0.01" — see identical rationale in
        # ProductionAlimentPaiementForm.prix_unitaire.
        widget=forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
        help_text="سعر الوحدة الواحدة (لكل طير) — يُضرب في إجمالي عدد الطيور الحية للدفعة/الدفعات المعنية.",
    )
    montant_direct = forms.DecimalField(
        label="المبلغ الإجمالي المباشر",
        max_digits=12,
        decimal_places=2,
        required=False,
        min_value=Decimal("0"),
        widget=forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
        help_text=(
            "مبلغ إجمالي واحد يُغطّي كامل عملية الطبيب/الفريق البيطري — "
            "مناسب عندما تضم الدفعة أدوية/لقاحات غير متجانسة وليس لها سعر "
            "موحّد لكل طير."
        ),
    )
    mode_paiement = forms.ChoiceField(label="طريقة الدفع")
    notes = forms.CharField(
        label="ملاحظات",
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from depenses.models import Depense

        self.fields["mode_paiement"].choices = Depense.MODE_CHOICES
        if not self.initial.get("date"):
            self.fields["date"].initial = datetime.date.today()
        if not self.initial.get("mode_paiement"):
            self.fields["mode_paiement"].initial = Depense.MODE_ESPECES

    def clean(self):
        cleaned_data = super().clean()
        mode = cleaned_data.get("mode_montant")
        prix_unitaire = cleaned_data.get("prix_unitaire")
        montant_direct = cleaned_data.get("montant_direct")

        if mode == self.MODE_DIRECT:
            if not montant_direct:
                self.add_error(
                    "montant_direct",
                    "أدخل المبلغ الإجمالي المباشر لهذه الدفعة.",
                )
        else:
            if not prix_unitaire:
                self.add_error(
                    "prix_unitaire",
                    "أدخل سعر الوحدة لكل طير لهذه الدفعة.",
                )
        return cleaned_data


class RetraitOeufsForm(forms.ModelForm):
    """
    Withdraw eggs from stock outside the formal BLClient sales flow: direct
    truck sale, gift, or loss/breakage. `lot` is optional and informational
    only (see model docstring) — it lets the withdrawal show up on that
    lot's daily table (utils.get_lot_suivi_journalier) without implying the
    physical egg stock is split by lot.

    If `client` is set, the create view (views.retrait_oeufs_create) auto-
    generates a formal BLClient + line for this quantity — see
    RetraitOeufs.bl_genere / signals.retrait_oeufs_post_save for how stock
    is kept single-sourced in that case. `destinataire` stays free-text for
    withdrawals with no registered client (gifts, losses, walk-ins).
    """

    class Meta:
        model = RetraitOeufs
        fields = [
            "branche",
            "lot",
            "date",
            "quantite_oeufs",
            "motif",
            "client",
            "destinataire",
            "notes",
        ]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, lot=None, branche=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["client"].required = False
        self.fields["client"].queryset = Client.objects.filter(actif=True).order_by(
            "nom"
        )
        self.fields["client"].empty_label = "— بدون عميل مسجل —"
        self.fields["destinataire"].required = False
        self.fields["notes"].required = False
        self.fields["lot"].required = False
        self.fields["lot"].empty_label = "— بدون دفعة (سحب عام) —"
        lot_qs = LotElevage.objects.all()
        if branche:
            lot_qs = lot_qs.filter(branche=branche)
        self.fields["lot"].queryset = lot_qs.order_by("-date_ouverture")
        if lot:
            self.fields["lot"].initial = lot
            self.fields["lot"].widget = forms.HiddenInput()
            self.fields["branche"].initial = lot.branche
            self.fields["branche"].widget = forms.HiddenInput()
        elif branche:
            self.fields["branche"].initial = branche
            self.fields["branche"].widget = forms.HiddenInput()
        if not self.initial.get("date"):
            self.fields["date"].initial = datetime.date.today()

    def clean(self):
        cleaned = super().clean()
        client = cleaned.get("client")
        motif = cleaned.get("motif")
        if client and motif != RetraitOeufs.MOTIF_CLIENT_CAMION:
            raise ValidationError(
                "لا يمكن ربط عميل مسجل إلا مع السبب «بيع مباشر (شاحنة/زبون)» "
                "— سينشئ هذا وصل تسليم رسمي للعميل."
            )
        return cleaned
