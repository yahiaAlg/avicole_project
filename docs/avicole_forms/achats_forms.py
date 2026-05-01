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
    Header form for a supplier delivery note.

    Statut is limited to choices the user may select manually.
    Transitions to FACTURE are performed by the invoice-creation signal, not
    by users directly (BR-BLF-02).
    """

    # Statut choices available to the user (FACTURE is system-controlled).
    STATUT_USER_CHOICES = [
        (BLFournisseur.STATUT_BROUILLON, "Brouillon"),
        (BLFournisseur.STATUT_RECU, "Reçu"),
        (BLFournisseur.STATUT_LITIGE, "En litige"),
    ]

    class Meta:
        model = BLFournisseur
        fields = [
            "reference",
            "fournisseur",
            "date_bl",
            "reference_fournisseur",
            "statut",
            "notes_reception",
            "piece_jointe",
        ]
        widgets = {
            "date_bl": forms.DateInput(attrs={"type": "date"}),
            "notes_reception": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["fournisseur"].queryset = Fournisseur.objects.filter(actif=True).order_by("nom")
        self.fields["statut"].choices = self.STATUT_USER_CHOICES
        self.fields["reference_fournisseur"].required = False
        self.fields["notes_reception"].required = False
        self.fields["piece_jointe"].required = False

        # BR-BLF-02: lock all fields on a Facturé BL.
        if self.instance and self.instance.est_verrouille:
            for field in self.fields.values():
                field.disabled = True

    def clean_date_bl(self):
        date = self.cleaned_data["date_bl"]
        if date > datetime.date.today():
            raise ValidationError("La date du BL ne peut pas être dans le futur.")
        return date

    def clean_piece_jointe(self):
        file = self.cleaned_data.get("piece_jointe")
        if file and hasattr(file, "content_type"):
            if file.content_type not in ALLOWED_ATTACHMENT_TYPES:
                raise ValidationError("Seuls les fichiers PDF, JPG et PNG sont acceptés.")
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
        self.fields["intrant"].queryset = Intrant.objects.filter(actif=True).select_related("categorie")
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
    """

    # Statut choices available to the user.
    STATUT_USER_CHOICES = [
        (FactureFournisseur.STATUT_NON_PAYE, "Non payée"),
        (FactureFournisseur.STATUT_EN_LITIGE, "En litige"),
    ]

    class Meta:
        model = FactureFournisseur
        fields = [
            "reference",
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

    def __init__(self, *args, fournisseur=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["statut"].choices = self.STATUT_USER_CHOICES
        self.fields["date_echeance"].required = False
        self.fields["notes"].required = False

        if fournisseur:
            # BR-FAF-02: only Reçu BLs for the selected supplier.
            self.fields["bls"].queryset = BLFournisseur.objects.filter(
                fournisseur=fournisseur,
                statut=BLFournisseur.STATUT_RECU,
            ).order_by("date_bl")
            self.fields["fournisseur"].initial = fournisseur
            self.fields["fournisseur"].widget = forms.HiddenInput()
        else:
            self.fields["bls"].queryset = BLFournisseur.objects.filter(
                statut=BLFournisseur.STATUT_RECU
            ).order_by("fournisseur__nom", "date_bl")

    def clean(self):
        cleaned = super().clean()
        fournisseur = cleaned.get("fournisseur")
        bls = cleaned.get("bls")
        date_facture = cleaned.get("date_facture")
        date_echeance = cleaned.get("date_echeance")

        # BR-FAF-02: all selected BLs must belong to the selected supplier.
        if fournisseur and bls:
            bad_bls = [bl.reference for bl in bls if bl.fournisseur_id != fournisseur.pk]
            if bad_bls:
                raise ValidationError(
                    f"BR-FAF-02 : les BLs suivants n'appartiennent pas au fournisseur "
                    f"sélectionné : {', '.join(bad_bls)}."
                )
            # All selected BLs must be Reçu (not Litige or Brouillon).
            non_recu = [bl.reference for bl in bls if bl.statut != BLFournisseur.STATUT_RECU]
            if non_recu:
                raise ValidationError(
                    f"BR-FAF-02 / BR-BLF-03 : les BLs suivants ne sont pas au statut "
                    f"'Reçu' et ne peuvent pas être facturés : {', '.join(non_recu)}."
                )

        # Due date must be ≥ invoice date.
        if date_facture and date_echeance and date_echeance < date_facture:
            raise ValidationError(
                {"date_echeance": "La date d'échéance doit être postérieure ou égale à la date de facturation."}
            )

        return cleaned


# ---------------------------------------------------------------------------
# Règlement Fournisseur
# ---------------------------------------------------------------------------

class ReglementFournisseurForm(forms.ModelForm):
    """
    Record a supplier payment.  On save, the FIFO allocation engine runs
    automatically via post_save signal (BR-REG-03).

    BR-REG-06: no edit form — règlements are immutable after creation.
    """

    class Meta:
        model = ReglementFournisseur
        fields = [
            "fournisseur",
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

    def __init__(self, *args, fournisseur=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["fournisseur"].queryset = Fournisseur.objects.filter(actif=True).order_by("nom")
        self.fields["reference_paiement"].required = False
        self.fields["notes"].required = False
        if fournisseur:
            self.fields["fournisseur"].initial = fournisseur
            self.fields["fournisseur"].widget = forms.HiddenInput()

    def clean_montant(self):
        montant = self.cleaned_data["montant"]
        if montant <= 0:
            raise ValidationError("Le montant du règlement doit être supérieur à 0.")
        return montant

    def clean_date_reglement(self):
        date = self.cleaned_data["date_reglement"]
        if date > datetime.date.today():
            raise ValidationError("La date du règlement ne peut pas être dans le futur.")
        return date
