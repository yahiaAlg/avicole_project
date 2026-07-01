"""
achats/forms.py

Forms for the full supplier procurement cycle:
  BLFournisseur + lines → FactureFournisseur → ReglementFournisseur.

Business rules enforced here (complementing model.clean() and signals):
  BR-BLF-01  Stock impact only on BL validation — form controls statut transitions.
  BR-BLF-02  Locked (Facturé) BLs cannot be edited.
  BR-BLF-03  Litige BLs excluded from invoice BL selection.
  BR-FAF-01  Invoice total auto-computed from BL lines — no manual entry.
  BR-FAF-02  Only Reçu BLs from the selected supplier may be included.
  BR-FAF-04  Statut Payé is not a selectable choice (set only by settlement).
  BR-REG-06  Règlements are immutable — no edit form.
"""

import datetime
from django import forms
from django.forms import inlineformset_factory
from django.core.exceptions import ValidationError

from achats.models import (
    BLFournisseur,
    BLFournisseurLigne,
    FactureFournisseur,
    ReglementFournisseur,
)
from intrants.models import Fournisseur, Intrant

# ---------------------------------------------------------------------------
# BL Fournisseur
# ---------------------------------------------------------------------------

ALLOWED_ATTACHMENT_TYPES = ["application/pdf", "image/jpeg", "image/png"]
MAX_ATTACHMENT_SIZE_MB = 5


class BLFournisseurForm(forms.ModelForm):
    """
    Header form for a supplier delivery note (classic BL or autorisation d'accès).

    Statut choices:
      - Classic BL   : brouillon / recu / litige  (facture is system-set)
      - Autorisation : autorise / recu / litige   (brouillon has no meaning)

    BR-BLF-02 : Facturé BLs are fully locked.
    BR-BLF-05 : An expired autorisation d'accès cannot be confirmed as Reçu.
    BR-BRA-01 : every BL belongs to exactly one branche — the goods land in
                that branche's StockIntrant. Pass `branche=<Branche
                instance>` from the view when the user is locked to one
                branch (chef de branche / opérateur, BR-BRA-02) to
                pre-select and lock the field.
    """

    # All statut values a user may choose.  The form's clean() enforces
    # which subset is valid per document type.
    STATUT_USER_CHOICES = [
        (BLFournisseur.STATUT_AUTORISE, "مفوَّض (في انتظار الاستلام)"),
        (BLFournisseur.STATUT_BROUILLON, "مسودة"),
        (BLFournisseur.STATUT_RECU, "مستلم"),
        (BLFournisseur.STATUT_LITIGE, "في نزاع"),
    ]

    class Meta:
        model = BLFournisseur
        fields = [
            "reference",
            "branche",
            "fournisseur",
            "date_bl",
            "type_document",
            "reference_fournisseur",
            "statut",
            # --- autorisation d'accès fields ---
            "numero_autorisation",
            "date_expiration_autorisation",
            "nom_chauffeur",
            "matricule_camion",
            "numero_permis",
            "portail_entree",
            "portail_sortie",
            # -----------------------------------
            "notes_reception",
            "piece_jointe",
        ]
        widgets = {
            "date_bl": forms.DateInput(attrs={"type": "date"}),
            "date_expiration_autorisation": forms.DateInput(attrs={"type": "date"}),
            "notes_reception": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, branche=None, **kwargs):
        super().__init__(*args, **kwargs)
        from core.models import Branche

        self.fields["fournisseur"].queryset = Fournisseur.objects.filter(
            actif=True
        ).order_by("nom")
        self.fields["branche"].queryset = Branche.objects.filter(actif=True).order_by(
            "nom"
        )
        if branche:
            self.fields["branche"].initial = branche
            self.fields["branche"].widget = forms.HiddenInput()
        self.fields["statut"].choices = self.STATUT_USER_CHOICES
        self.fields["reference_fournisseur"].required = False
        self.fields["notes_reception"].required = False
        self.fields["piece_jointe"].required = False

        # Autorisation-specific fields are always optional at the DB level;
        # the template hides/shows them via JS based on type_document.
        for f in (
            "numero_autorisation",
            "date_expiration_autorisation",
            "nom_chauffeur",
            "matricule_camion",
            "numero_permis",
            "portail_entree",
            "portail_sortie",
        ):
            self.fields[f].required = False

        # BR-BLF-02: lock all fields on a Facturé BL.
        if self.instance and self.instance.est_verrouille:
            for field in self.fields.values():
                field.disabled = True

    def clean_date_bl(self):
        date = self.cleaned_data["date_bl"]
        if date > datetime.date.today():
            raise ValidationError("La date du BL ne peut pas être dans le futur.")
        return date

    def clean_date_expiration_autorisation(self):
        date_exp = self.cleaned_data.get("date_expiration_autorisation")
        date_bl = self.cleaned_data.get("date_bl")
        if date_exp and date_bl and date_exp < date_bl:
            raise ValidationError(
                "La date d'expiration doit être postérieure ou égale à la date du BL / de l'autorisation."
            )
        return date_exp

    def clean_piece_jointe(self):
        file = self.cleaned_data.get("piece_jointe")
        if file and hasattr(file, "content_type"):
            if file.content_type not in ALLOWED_ATTACHMENT_TYPES:
                raise ValidationError(
                    "Seuls les fichiers PDF, JPG et PNG sont acceptés."
                )
            if file.size > MAX_ATTACHMENT_SIZE_MB * 1024 * 1024:
                raise ValidationError(
                    f"La taille du fichier ne doit pas dépasser {MAX_ATTACHMENT_SIZE_MB} Mo."
                )
        return file

    def clean(self):
        cleaned = super().clean()

        # BR-BLF-02: block saves on locked instances.
        if self.instance and self.instance.est_verrouille:
            raise ValidationError(
                "BR-BLF-02 : ce BL est verrouillé (statut Facturé) et ne peut plus être modifié."
            )

        type_doc = cleaned.get("type_document")
        statut = cleaned.get("statut")
        date_exp = cleaned.get("date_expiration_autorisation")

        # BR-BLF-05: an expired autorisation d'accès cannot be confirmed RECU.
        if (
            type_doc == BLFournisseur.TYPE_AUTORISATION_ACCES
            and statut == BLFournisseur.STATUT_RECU
            and date_exp
            and date_exp < datetime.date.today()
        ):
            raise ValidationError(
                f"BR-BLF-05 : l'autorisation d'accès est expirée depuis le {date_exp}. "
                "Impossible de confirmer la réception — contactez le fournisseur pour renouveler l'autorisation."
            )

        # STATUT_AUTORISE is only meaningful for autorisation d'accès documents.
        if (
            statut == BLFournisseur.STATUT_AUTORISE
            and type_doc == BLFournisseur.TYPE_BL_CLASSIQUE
        ):
            raise ValidationError(
                {
                    "statut": "Le statut 'Mfawwad' est réservé aux autorisations d'accès. "
                    "Utilisez 'Brouillon' pour un BL classique."
                }
            )

        return cleaned


class BLFournisseurLigneForm(forms.ModelForm):
    class Meta:
        model = BLFournisseurLigne
        fields = ["intrant", "quantite", "prix_unitaire", "notes"]
        widgets = {
            "quantite": forms.NumberInput(attrs={"step": "0.001", "min": "0.001"}),
            "prix_unitaire": forms.NumberInput(attrs={"step": "0.0001", "min": "0"}),
            "notes": forms.Textarea(attrs={"rows": 1}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["intrant"].queryset = Intrant.objects.filter(
            actif=True
        ).select_related("categorie")
        self.fields["notes"].required = False


# Inline formset: one BLFournisseur → many BLFournisseurLignes.
BLFournisseurLigneFormSet = inlineformset_factory(
    BLFournisseur,
    BLFournisseurLigne,
    form=BLFournisseurLigneForm,
    extra=3,
    min_num=1,
    validate_min=True,
    can_delete=True,
)


# ---------------------------------------------------------------------------
# Facture Fournisseur
# ---------------------------------------------------------------------------


class FactureFournisseurForm(forms.ModelForm):
    """
    Create a supplier invoice by selecting Reçu BLs.

    BR-FAF-01: montant_total is excluded from the form — it is computed from
               BL lines in the post_save signal.
    BR-FAF-02: The bls queryset is filtered to Reçu BLs for the chosen supplier.
               The view must pass `fournisseur` kwarg to apply this filter.
    BR-FAF-04: STATUT_PAYE is excluded from the form choices — only signals/
               settlement records set this value.
    BR-BRA-01: must match the branche of every selected BL (the model's
               docstring notes this is enforced at the view/M2M-assignment
               layer — done here in clean()). Pass `branche=<Branche
               instance>` from the view when the user is locked to one
               branch (chef de branche / opérateur, BR-BRA-02) to
               pre-select, lock the field, and scope the bls queryset.
    """

    # Statut choices available to the user.
    STATUT_USER_CHOICES = [
        (FactureFournisseur.STATUT_NON_PAYE, "غير مدفوعة"),
        (FactureFournisseur.STATUT_EN_LITIGE, "في نزاع"),
    ]

    class Meta:
        model = FactureFournisseur
        fields = [
            "reference",
            "branche",
            "fournisseur",
            "bls",
            "date_facture",
            "date_echeance",
            "type_facture",
            "statut",
            "notes",
        ]
        widgets = {
            "date_facture": forms.DateInput(attrs={"type": "date"}),
            "date_echeance": forms.DateInput(attrs={"type": "date"}),
            "bls": forms.CheckboxSelectMultiple(),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, fournisseur=None, branche=None, **kwargs):
        super().__init__(*args, **kwargs)
        from core.models import Branche

        self.fields["statut"].choices = self.STATUT_USER_CHOICES
        self.fields["date_echeance"].required = False
        self.fields["notes"].required = False
        self.fields["branche"].queryset = Branche.objects.filter(actif=True).order_by(
            "nom"
        )
        self._branche = branche
        if branche:
            self.fields["branche"].initial = branche
            self.fields["branche"].widget = forms.HiddenInput()

        bls_qs = BLFournisseur.objects.filter(statut=BLFournisseur.STATUT_RECU)
        if fournisseur:
            # BR-FAF-02: only Reçu BLs for the selected supplier.
            bls_qs = bls_qs.filter(fournisseur=fournisseur)
            self.fields["fournisseur"].initial = fournisseur
            self.fields["fournisseur"].widget = forms.HiddenInput()
        if branche:
            # BR-BRA-01: only BLs from the same branche as this invoice.
            bls_qs = bls_qs.filter(branche=branche)
        self.fields["bls"].queryset = bls_qs.order_by("fournisseur__nom", "date_bl")

    def clean(self):
        cleaned = super().clean()
        fournisseur = cleaned.get("fournisseur")
        branche = cleaned.get("branche") or self._branche
        bls = cleaned.get("bls")
        date_facture = cleaned.get("date_facture")
        date_echeance = cleaned.get("date_echeance")

        # BR-FAF-02: all selected BLs must belong to the selected supplier.
        if fournisseur and bls:
            bad_bls = [
                bl.reference for bl in bls if bl.fournisseur_id != fournisseur.pk
            ]
            if bad_bls:
                raise ValidationError(
                    f"BR-FAF-02 : les BLs suivants n'appartiennent pas au fournisseur "
                    f"sélectionné : {', '.join(bad_bls)}."
                )
            # All selected BLs must be Reçu (not Litige or Brouillon).
            non_recu = [
                bl.reference for bl in bls if bl.statut != BLFournisseur.STATUT_RECU
            ]
            if non_recu:
                raise ValidationError(
                    f"BR-FAF-02 / BR-BLF-03 : les BLs suivants ne sont pas au statut "
                    f"'Reçu' et ne peuvent pas être facturés : {', '.join(non_recu)}."
                )

        # BR-BRA-01: every selected BL must belong to this invoice's branche.
        if branche and bls:
            bad_branche = [bl.reference for bl in bls if bl.branche_id != branche.pk]
            if bad_branche:
                raise ValidationError(
                    f"BR-BRA-01 : les BLs suivants n'appartiennent pas à la branche "
                    f"sélectionnée : {', '.join(bad_branche)}."
                )

        # Due date must be ≥ invoice date.
        if date_facture and date_echeance and date_echeance < date_facture:
            raise ValidationError(
                {
                    "date_echeance": "La date d'échéance doit être postérieure ou égale à la date de facturation."
                }
            )

        return cleaned


# ---------------------------------------------------------------------------
# Règlement Fournisseur
# ---------------------------------------------------------------------------


class ReglementFournisseurForm(forms.ModelForm):
    """
    Record a supplier payment.  On save, the FIFO allocation engine runs
    automatically via post_save signal (BR-REG-03), scoped to this
    règlement's branche (BR-BRA-01) — it can only settle invoices in the
    same branche.

    BR-REG-06: no edit form — règlements are immutable after creation.

    Pass `branche=<Branche instance>` from the view when the current user
    is locked to one branch (chef de branche / opérateur, BR-BRA-02) to
    pre-select and lock the field.
    """

    class Meta:
        model = ReglementFournisseur
        fields = [
            "fournisseur",
            "branche",
            "date_reglement",
            "montant",
            "mode_paiement",
            "reference_paiement",
            "notes",
        ]
        widgets = {
            "date_reglement": forms.DateInput(attrs={"type": "date"}),
            "montant": forms.NumberInput(attrs={"step": "0.01", "min": "0.01"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, fournisseur=None, branche=None, **kwargs):
        super().__init__(*args, **kwargs)
        from core.models import Branche

        self.fields["fournisseur"].queryset = Fournisseur.objects.filter(
            actif=True
        ).order_by("nom")
        self.fields["branche"].queryset = Branche.objects.filter(actif=True).order_by(
            "nom"
        )
        self.fields["reference_paiement"].required = False
        self.fields["notes"].required = False
        if fournisseur:
            self.fields["fournisseur"].initial = fournisseur
            self.fields["fournisseur"].widget = forms.HiddenInput()
        if branche:
            self.fields["branche"].initial = branche
            self.fields["branche"].widget = forms.HiddenInput()

    def clean_montant(self):
        montant = self.cleaned_data["montant"]
        if montant <= 0:
            raise ValidationError("Le montant du règlement doit être supérieur à 0.")
        return montant

    def clean_date_reglement(self):
        date = self.cleaned_data["date_reglement"]
        if date > datetime.date.today():
            raise ValidationError(
                "La date du règlement ne peut pas être dans le futur."
            )
        return date
