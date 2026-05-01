"""
clients/forms.py

Forms for the full client AR cycle:
  Client → BLClient + lines → FactureClient → PaiementClient + allocations.

Business rules enforced here:
  BR-BLC-02  BL cannot be validated if qty > available stock produits finis.
  BR-BLC-03  Locked (Facturé) BLs cannot be edited.
  BR-FAC-01  Invoice total auto-computed from BL lines — no manual entry.
  BR-FAC-02  Only Livré BLs from the selected client may be included.
  BR-FAC-03  User selects which invoice(s) a payment applies to (manual allocation).
"""

import datetime
from decimal import Decimal
from django import forms
from django.forms import inlineformset_factory, BaseInlineFormSet
from django.core.exceptions import ValidationError

from clients.models import (
    Client,
    BLClient,
    BLClientLigne,
    FactureClient,
    PaiementClient,
    PaiementClientAllocation,
)
from production.models import ProduitFini


# ---------------------------------------------------------------------------
# Client master record
# ---------------------------------------------------------------------------

class ClientForm(forms.ModelForm):
    class Meta:
        model = Client
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
            "type_client",
            "plafond_credit",
            "actif",
            "notes",
        ]
        widgets = {
            "adresse": forms.Textarea(attrs={"rows": 2}),
            "notes": forms.Textarea(attrs={"rows": 2}),
            "plafond_credit": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
        }


# ---------------------------------------------------------------------------
# BL Client
# ---------------------------------------------------------------------------

class BLClientForm(forms.ModelForm):
    """
    Header form for a client delivery note.

    STATUT_FACTURE is system-controlled (BR-BLC-03) and excluded from
    user-selectable choices.
    """

    STATUT_USER_CHOICES = [
        (BLClient.STATUT_BROUILLON, "Brouillon"),
        (BLClient.STATUT_LIVRE, "Livré"),
        (BLClient.STATUT_LITIGE, "En litige"),
    ]

    class Meta:
        model = BLClient
        fields = [
            "reference",
            "client",
            "date_bl",
            "adresse_livraison",
            "statut",
            "signe_par",
            "notes",
        ]
        widgets = {
            "date_bl": forms.DateInput(attrs={"type": "date"}),
            "adresse_livraison": forms.Textarea(attrs={"rows": 2}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, client=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["client"].queryset = Client.objects.filter(actif=True).order_by("nom")
        self.fields["statut"].choices = self.STATUT_USER_CHOICES
        self.fields["adresse_livraison"].required = False
        self.fields["signe_par"].required = False
        self.fields["notes"].required = False
        if client:
            self.fields["client"].initial = client
            self.fields["client"].widget = forms.HiddenInput()

        # BR-BLC-03: lock all fields on a Facturé BL.
        if self.instance and self.instance.est_verrouille:
            for field in self.fields.values():
                field.disabled = True

    def clean_date_bl(self):
        date = self.cleaned_data["date_bl"]
        if date > datetime.date.today():
            raise ValidationError("La date du BL ne peut pas être dans le futur.")
        return date

    def clean(self):
        cleaned = super().clean()
        if self.instance and self.instance.est_verrouille:
            raise ValidationError(
                "BR-BLC-03 : ce BL est verrouillé (statut Facturé) et ne peut plus être modifié."
            )
        return cleaned


class BLClientLigneForm(forms.ModelForm):
    """
    One line on a BL Client.

    BR-BLC-02: requested quantity cannot exceed available stock produits finis.
    This check runs per-line at form validation time; the view must also
    re-check atomically before committing to prevent race conditions.
    """

    class Meta:
        model = BLClientLigne
        fields = ["produit_fini", "quantite", "prix_unitaire", "notes"]
        widgets = {
            "quantite": forms.NumberInput(attrs={"step": "0.001", "min": "0.001"}),
            "prix_unitaire": forms.NumberInput(attrs={"step": "0.0001", "min": "0"}),
            "notes": forms.Textarea(attrs={"rows": 1}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["produit_fini"].queryset = ProduitFini.objects.filter(actif=True)
        self.fields["notes"].required = False

    def clean(self):
        cleaned = super().clean()
        produit_fini = cleaned.get("produit_fini")
        quantite = cleaned.get("quantite")

        if produit_fini and quantite:
            stock_dispo = produit_fini.quantite_en_stock
            # On update, add back the current line's recorded quantity.
            if self.instance and self.instance.pk:
                stock_dispo += self.instance.quantite
            if quantite > stock_dispo:
                raise ValidationError(
                    f"BR-BLC-02 : stock insuffisant pour «\u202f{produit_fini.designation}\u202f». "
                    f"Disponible\u202f: {stock_dispo} {produit_fini.unite_mesure} — "
                    f"Demandé\u202f: {quantite} {produit_fini.unite_mesure}."
                )
        return cleaned


class BaseBLClientLigneFormSet(BaseInlineFormSet):
    """
    Custom formset that enforces at least one non-deleted line.
    """

    def clean(self):
        super().clean()
        active_forms = [
            f for f in self.forms
            if f.cleaned_data and not f.cleaned_data.get("DELETE", False)
        ]
        if not active_forms:
            raise ValidationError("Un BL client doit contenir au moins une ligne produit.")


BLClientLigneFormSet = inlineformset_factory(
    BLClient,
    BLClientLigne,
    form=BLClientLigneForm,
    formset=BaseBLClientLigneFormSet,
    extra=3,
    min_num=1,
    validate_min=True,
    can_delete=True,
)


# ---------------------------------------------------------------------------
# Facture Client
# ---------------------------------------------------------------------------

class FactureClientForm(forms.ModelForm):
    """
    Create a client invoice by selecting Livré BLs.

    BR-FAC-01: montant_ht is excluded — computed from BL lines in the view/signal.
    BR-FAC-02: bls queryset filtered to Livré BLs for the selected client.
    """

    # User-selectable statut values (Payée is driven by payments, not manually set).
    STATUT_USER_CHOICES = [
        (FactureClient.STATUT_NON_PAYEE, "Non payée"),
        (FactureClient.STATUT_EN_LITIGE, "En litige"),
    ]

    class Meta:
        model = FactureClient
        fields = [
            "reference",
            "client",
            "bls",
            "date_facture",
            "date_echeance",
            "taux_tva",
            "statut",
            "notes",
        ]
        widgets = {
            "date_facture": forms.DateInput(attrs={"type": "date"}),
            "date_echeance": forms.DateInput(attrs={"type": "date"}),
            "bls": forms.CheckboxSelectMultiple(),
            "taux_tva": forms.NumberInput(attrs={"step": "0.01", "min": "0", "max": "100"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, client=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["statut"].choices = self.STATUT_USER_CHOICES
        self.fields["date_echeance"].required = False
        self.fields["notes"].required = False

        if client:
            # BR-FAC-02: only Livré BLs for this client.
            self.fields["bls"].queryset = BLClient.objects.filter(
                client=client,
                statut=BLClient.STATUT_LIVRE,
            ).order_by("date_bl")
            self.fields["client"].initial = client
            self.fields["client"].widget = forms.HiddenInput()
        else:
            self.fields["bls"].queryset = BLClient.objects.filter(
                statut=BLClient.STATUT_LIVRE
            ).order_by("client__nom", "date_bl")

    def clean(self):
        cleaned = super().clean()
        client = cleaned.get("client")
        bls = cleaned.get("bls")
        date_facture = cleaned.get("date_facture")
        date_echeance = cleaned.get("date_echeance")

        # BR-FAC-02: all BLs must belong to the selected client and be Livré.
        if client and bls:
            bad_bls = [bl.reference for bl in bls if bl.client_id != client.pk]
            if bad_bls:
                raise ValidationError(
                    f"BR-FAC-02 : les BLs suivants n'appartiennent pas au client "
                    f"sélectionné : {', '.join(bad_bls)}."
                )
            non_livre = [bl.reference for bl in bls if bl.statut != BLClient.STATUT_LIVRE]
            if non_livre:
                raise ValidationError(
                    f"BR-FAC-02 : les BLs suivants ne sont pas au statut 'Livré' : "
                    f"{', '.join(non_livre)}."
                )

        if date_facture and date_echeance and date_echeance < date_facture:
            raise ValidationError(
                {"date_echeance": "La date d'échéance doit être postérieure ou égale à la date de facturation."}
            )

        return cleaned


# ---------------------------------------------------------------------------
# Paiement Client
# ---------------------------------------------------------------------------

class PaiementClientForm(forms.ModelForm):
    """
    Record a payment from a client.

    BR-FAC-03: the user manually selects which invoice(s) to apply the
    payment to via PaiementClientAllocationForm(s) rendered on the same page.
    Records are immutable after creation — no edit form provided.
    """

    class Meta:
        model = PaiementClient
        fields = [
            "client",
            "date_paiement",
            "montant",
            "mode_paiement",
            "reference_paiement",
            "notes",
        ]
        widgets = {
            "date_paiement": forms.DateInput(attrs={"type": "date"}),
            "montant": forms.NumberInput(attrs={"step": "0.01", "min": "0.01"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, client=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["client"].queryset = Client.objects.filter(actif=True).order_by("nom")
        self.fields["reference_paiement"].required = False
        self.fields["notes"].required = False
        if client:
            self.fields["client"].initial = client
            self.fields["client"].widget = forms.HiddenInput()

    def clean_montant(self):
        montant = self.cleaned_data["montant"]
        if montant <= 0:
            raise ValidationError("Le montant du paiement doit être supérieur à 0.")
        return montant

    def clean_date_paiement(self):
        date = self.cleaned_data["date_paiement"]
        if date > datetime.date.today():
            raise ValidationError("La date du paiement ne peut pas être dans le futur.")
        return date


class PaiementClientAllocationForm(forms.ModelForm):
    """
    Allocate a portion of a PaiementClient to a single FactureClient.

    BR-FAC-03: one instance of this form is rendered per open invoice on the
    payment creation page.  The view iterates over submitted allocations and
    calls facture.recalculer_solde() for each.

    montant_alloue must not exceed the invoice's reste_a_payer, and the sum
    of all allocations must not exceed the payment total (validated in the view).
    """

    class Meta:
        model = PaiementClientAllocation
        fields = ["facture", "montant_alloue"]
        widgets = {
            "montant_alloue": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "facture": forms.HiddenInput(),
        }

    def clean_montant_alloue(self):
        montant = self.cleaned_data.get("montant_alloue", Decimal("0"))
        if montant < 0:
            raise ValidationError("Le montant alloué ne peut pas être négatif.")
        return montant

    def clean(self):
        cleaned = super().clean()
        facture = cleaned.get("facture")
        montant = cleaned.get("montant_alloue", Decimal("0"))

        if facture and montant > facture.reste_a_payer:
            raise ValidationError(
                f"Le montant alloué ({montant} DZD) dépasse le reste à payer "
                f"de la facture {facture.reference} ({facture.reste_a_payer} DZD)."
            )
        return cleaned


# ---------------------------------------------------------------------------
# Helper: build a list of allocation forms for a client's open invoices
# ---------------------------------------------------------------------------

def get_allocation_forms(client, paiement=None, data=None):
    """
    Return a list of PaiementClientAllocationForm instances, one per open
    invoice for the given client.  Used by the payment creation view to
    display allocation rows inline.

    Args:
        client (Client): The client whose open invoices should be listed.
        paiement (PaiementClient | None): If provided, the paiement FK is
            pre-set (for rendering after initial POST).
        data (QueryDict | None): POST data to bind the forms.

    Returns:
        list[PaiementClientAllocationForm]
    """
    open_factures = FactureClient.objects.filter(
        client=client,
        statut__in=[
            FactureClient.STATUT_NON_PAYEE,
            FactureClient.STATUT_PARTIELLEMENT_PAYEE,
        ],
    ).order_by("date_facture", "pk")

    forms_list = []
    for idx, facture in enumerate(open_factures):
        prefix = f"allocation_{facture.pk}"
        initial = {"facture": facture, "montant_alloue": Decimal("0")}
        if paiement:
            initial["paiement"] = paiement
        form = PaiementClientAllocationForm(
            data=data,
            prefix=prefix,
            initial=initial,
        )
        # Expose the facture object on the form for template rendering.
        form.facture_instance = facture
        forms_list.append(form)
    return forms_list
