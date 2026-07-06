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
    TypeClient,
    Client,
    BLClient,
    BLClientLigne,
    FactureClient,
    PaiementClient,
    PaiementClientAllocation,
    AbonnementClient,
    VoyageLivraison,
    LivraisonPartielle,
)
from production.models import ProduitFini
from core.forms import make_piece_jointe_formset

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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["type_client"].queryset = TypeClient.objects.filter(actif=True)


# ---------------------------------------------------------------------------
# BL Client
# ---------------------------------------------------------------------------


class BLClientForm(forms.ModelForm):
    """
    Header form for a client delivery note.

    STATUT_FACTURE is system-controlled (BR-BLC-03) and excluded from
    user-selectable choices.

    BR-BRA-01: the delivery comes out of one branche's StockProduitFini.
    Pass `branche=<Branche instance>` from the view when the current user
    is locked to one branch (chef de branche / opérateur, BR-BRA-02) to
    pre-select and lock the field.
    """

    STATUT_USER_CHOICES = [
        (BLClient.STATUT_BROUILLON, "مسودة"),
        (BLClient.STATUT_LIVRE, "تم التسليم"),
        (BLClient.STATUT_LITIGE, "في نزاع"),
    ]

    class Meta:
        model = BLClient
        fields = [
            "reference",
            "branche",
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

    def __init__(self, *args, client=None, branche=None, **kwargs):
        super().__init__(*args, **kwargs)
        from core.models import Branche

        self.fields["client"].queryset = Client.objects.filter(actif=True).order_by(
            "nom"
        )
        self.fields["branche"].queryset = Branche.objects.filter(actif=True).order_by(
            "nom"
        )
        self.fields["statut"].choices = self.STATUT_USER_CHOICES
        self.fields["adresse_livraison"].required = False
        self.fields["signe_par"].required = False
        self.fields["notes"].required = False
        if client:
            self.fields["client"].initial = client
            self.fields["client"].widget = forms.HiddenInput()
        if branche:
            self.fields["branche"].initial = branche
            self.fields["branche"].widget = forms.HiddenInput()

        # BR-BLC-03: lock all fields on a Facturé BL.
        if self.instance and self.instance.est_verrouille:
            for field in self.fields.values():
                field.disabled = True
        self.future_date_warning = False

    def clean_date_bl(self):
        date = self.cleaned_data["date_bl"]
        if date > datetime.date.today():
            self.future_date_warning = True
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

    BR-BRA-07: stock is now keyed by (branche, produit_fini). Pass
    `branche=<Branche instance>` from the view (via the formset's
    `form_kwargs`) so the availability check reads that branche's balance
    instead of the Vue Globale total across every branch.
    """

    class Meta:
        model = BLClientLigne
        fields = ["produit_fini", "quantite", "prix_unitaire", "notes"]
        widgets = {
            "quantite": forms.NumberInput(attrs={"step": "1", "min": "1"}),
            "prix_unitaire": forms.NumberInput(attrs={"step": "0.0001", "min": "0"}),
            "notes": forms.Textarea(attrs={"rows": 1}),
        }

    def __init__(self, *args, branche=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["produit_fini"].queryset = ProduitFini.objects.filter(actif=True)
        self.fields["notes"].required = False
        self._branche = branche

    def clean(self):
        cleaned = super().clean()
        produit_fini = cleaned.get("produit_fini")
        quantite = cleaned.get("quantite")

        if produit_fini and quantite:
            if self._branche is not None:
                stock_dispo = produit_fini.quantite_en_stock_branche(self._branche)
            else:
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
            f
            for f in self.forms
            if f.cleaned_data and not f.cleaned_data.get("DELETE", False)
        ]
        if not active_forms:
            raise ValidationError(
                "Un BL client doit contenir au moins une ligne produit."
            )


BLClientLigneFormSet = inlineformset_factory(
    BLClient,
    BLClientLigne,
    form=BLClientLigneForm,
    formset=BaseBLClientLigneFormSet,
    extra=1,
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
    BR-BRA-01: must match the branche of every selected BL (mirrors
               FactureFournisseur — enforced here in clean()). Pass
               `branche=<Branche instance>` from the view when the user is
               locked to one branch (chef de branche / opérateur,
               BR-BRA-02) to pre-select, lock the field, and scope the
               bls queryset.
    """

    # User-selectable statut values (Payée is driven by payments, not manually set).
    STATUT_USER_CHOICES = [
        (FactureClient.STATUT_NON_PAYEE, "غير مدفوعة"),
        (FactureClient.STATUT_EN_LITIGE, "في نزاع"),
    ]

    class Meta:
        model = FactureClient
        fields = [
            "reference",
            "branche",
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
            "taux_tva": forms.NumberInput(
                attrs={"step": "0.01", "min": "0", "max": "100"}
            ),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, client=None, branche=None, **kwargs):
        super().__init__(*args, **kwargs)
        from core.models import Branche

        self.fields["statut"].choices = self.STATUT_USER_CHOICES
        self.fields["date_echeance"].required = False
        self.fields["notes"].required = False
        self.fields["branche"].queryset = Branche.objects.filter(actif=True).order_by(
            "nom"
        )
        self._branche = branche

        bls_qs = BLClient.objects.filter(statut=BLClient.STATUT_LIVRE)
        if client:
            # BR-FAC-02: only Livré BLs for this client.
            bls_qs = bls_qs.filter(client=client)
            self.fields["client"].initial = client
            self.fields["client"].widget = forms.HiddenInput()
        if branche:
            # BR-BRA-01: only BLs from the same branche as this invoice.
            bls_qs = bls_qs.filter(branche=branche)
            self.fields["branche"].initial = branche
            self.fields["branche"].widget = forms.HiddenInput()
        self.fields["bls"].queryset = bls_qs.order_by("client__nom", "date_bl")

    def clean(self):
        cleaned = super().clean()
        client = cleaned.get("client")
        branche = cleaned.get("branche") or self._branche
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
            non_livre = [
                bl.reference for bl in bls if bl.statut != BLClient.STATUT_LIVRE
            ]
            if non_livre:
                raise ValidationError(
                    f"BR-FAC-02 : les BLs suivants ne sont pas au statut 'Livré' : "
                    f"{', '.join(non_livre)}."
                )

        # BR-BRA-01: every selected BL must belong to this invoice's branche.
        if branche and bls:
            bad_branche = [bl.reference for bl in bls if bl.branche_id != branche.pk]
            if bad_branche:
                raise ValidationError(
                    f"BR-BRA-01 : les BLs suivants n'appartiennent pas à la branche "
                    f"sélectionnée : {', '.join(bad_branche)}."
                )

        if date_facture and date_echeance and date_echeance < date_facture:
            raise ValidationError(
                {
                    "date_echeance": "La date d'échéance doit être postérieure ou égale à la date de facturation."
                }
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

    BR-BRA-01: the invoices selected via allocation must belong to this
    same branche (mirrors ReglementFournisseur — see
    PaiementClientAllocation.clean() and get_allocation_forms() below).
    Pass `branche=<Branche instance>` from the view when the current user
    is locked to one branch (chef de branche / opérateur, BR-BRA-02) to
    pre-select and lock the field.
    """

    class Meta:
        model = PaiementClient
        fields = [
            "client",
            "branche",
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

    def __init__(self, *args, client=None, branche=None, **kwargs):
        super().__init__(*args, **kwargs)
        from core.models import Branche

        self.fields["client"].queryset = Client.objects.filter(actif=True).order_by(
            "nom"
        )
        self.fields["branche"].queryset = Branche.objects.filter(actif=True).order_by(
            "nom"
        )
        self.fields["reference_paiement"].required = False
        self.fields["notes"].required = False
        if client:
            self.fields["client"].initial = client
            self.fields["client"].widget = forms.HiddenInput()
        if branche:
            self.fields["branche"].initial = branche
            self.fields["branche"].widget = forms.HiddenInput()
        self.future_date_warning = False

    def clean_montant(self):
        montant = self.cleaned_data["montant"]
        if montant <= 0:
            raise ValidationError("Le montant du paiement doit être supérieur à 0.")
        return montant

    def clean_date_paiement(self):
        date = self.cleaned_data["date_paiement"]
        if date > datetime.date.today():
            self.future_date_warning = True
        return date


class PaiementClientAllocationForm(forms.ModelForm):
    """
    Allocate a portion of a PaiementClient to a single FactureClient.

    BR-FAC-03: one instance of this form is rendered per open invoice on the
    payment creation page.  The view iterates over submitted allocations and
    calls facture.recalculer_solde() for each.

    montant_alloue = 0 means "skip this invoice" (FIFO fallback applies when
    ALL rows are 0).  The model has MinValueValidator(0.01) for DB integrity,
    so we override the field here to permit 0 at the form layer.

    montant_alloue must not exceed the invoice's reste_a_payer, and the sum
    of all allocations must not exceed the payment total (validated in the view).

    BR-BRA-01: cross-branch allocation is prevented upstream — the invoice
    list these forms are built from is already scoped to the payment's
    branche by get_allocation_forms() below, and PaiementClientAllocation.
    clean() is the final, authoritative guard at save time.
    """

    # Override to allow 0 — model has MinValueValidator(0.01) for DB integrity,
    # but 0 at form level simply means "don't allocate to this invoice".
    montant_alloue = forms.DecimalField(
        min_value=Decimal("0"),
        decimal_places=2,
        max_digits=14,
        required=False,
        initial=Decimal("0"),
        widget=forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
    )

    class Meta:
        model = PaiementClientAllocation
        fields = ["facture", "montant_alloue"]
        widgets = {
            "facture": forms.HiddenInput(),
        }

    def _post_clean(self):
        """
        ModelForm._post_clean() runs model-level validators (including the
        MinValueValidator(0.01) on PaiementClientAllocation.montant_alloue)
        even when the field is overridden in the form.  Since 0 is a valid
        sentinel meaning "skip this invoice", we suppress that specific error.
        """
        super()._post_clean()
        self._errors.pop("montant_alloue", None)

    def clean_montant_alloue(self):
        montant = self.cleaned_data.get("montant_alloue") or Decimal("0")
        if montant < 0:
            raise ValidationError("Le montant alloué ne peut pas être négatif.")
        return montant

    def clean(self):
        cleaned = super().clean()
        facture = cleaned.get("facture")
        montant = cleaned.get("montant_alloue") or Decimal("0")

        # Only validate over-allocation when the user actually entered an amount.
        if facture and montant > 0 and montant > facture.reste_a_payer:
            raise ValidationError(
                f"Le montant alloué ({montant} DZD) dépasse le reste à payer "
                f"de la facture {facture.reference} ({facture.reste_a_payer} DZD)."
            )
        return cleaned


# ---------------------------------------------------------------------------
# Helper: build a list of allocation forms for a client's open invoices
# ---------------------------------------------------------------------------


def get_allocation_forms(client, paiement=None, branche=None, data=None):
    """
    Return a list of PaiementClientAllocationForm instances, one per open
    invoice for the given client.  Used by the payment creation view to
    display allocation rows inline.

    Args:
        client (Client): The client whose open invoices should be listed.
        paiement (PaiementClient | None): If provided, the paiement FK is
            pre-set (for rendering after initial POST), and its branche
            takes precedence for scoping below (BR-BRA-01).
        branche (Branche | None): Scopes the open invoices to this branche
            only — required when `paiement` doesn't exist yet (the
            creation flow); a payment can only ever be allocated to
            invoices in its own branche, mirroring
            PaiementClientAllocation.clean().
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
    )
    scoping_branche = (paiement.branche if paiement else None) or branche
    if scoping_branche:
        open_factures = open_factures.filter(branche=scoping_branche)
    open_factures = open_factures.order_by("date_facture", "pk")

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


# ---------------------------------------------------------------------------
# Abonnement Client — recurring/metered deliveries
# ---------------------------------------------------------------------------


class AbonnementClientForm(forms.ModelForm):
    """
    Open or edit a client's recurring delivery agreement.

    BR-BRA-01: the agreement is fulfilled out of one branche's stock;
    LivraisonPartielle inherits it from here. Pass `branche=<Branche
    instance>` from the view when the current user is locked to one
    branch (chef de branche / opérateur, BR-BRA-02) to pre-select and
    lock the field.
    """

    class Meta:
        model = AbonnementClient
        fields = [
            "client",
            "branche",
            "produit_fini",
            "date_debut",
            "date_fin",
            "frequence",
            "quantite_totale_prevue",
            "prix_unitaire",
            "statut",
            "notes",
        ]
        widgets = {
            "date_debut": forms.DateInput(attrs={"type": "date"}),
            "date_fin": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
            "quantite_totale_prevue": forms.NumberInput(
                attrs={"step": "0.001", "min": "0"}
            ),
            "prix_unitaire": forms.NumberInput(attrs={"step": "0.0001", "min": "0"}),
        }

    def __init__(self, *args, client=None, branche=None, **kwargs):
        super().__init__(*args, **kwargs)
        from core.models import Branche

        self.fields["client"].queryset = Client.objects.filter(actif=True).order_by(
            "nom"
        )
        self.fields["produit_fini"].queryset = ProduitFini.objects.filter(actif=True)
        self.fields["branche"].queryset = Branche.objects.filter(actif=True).order_by(
            "nom"
        )
        self.fields["date_fin"].required = False
        self.fields["notes"].required = False
        if client:
            self.fields["client"].initial = client
            self.fields["client"].widget = forms.HiddenInput()
        if branche:
            self.fields["branche"].initial = branche
            self.fields["branche"].widget = forms.HiddenInput()

    def clean(self):
        cleaned = super().clean()
        date_debut = cleaned.get("date_debut")
        date_fin = cleaned.get("date_fin")
        if date_debut and date_fin and date_fin < date_debut:
            raise ValidationError(
                {"date_fin": "تاريخ الانتهاء يجب أن يكون بعد تاريخ البدء."}
            )
        return cleaned


class VoyageLivraisonForm(forms.ModelForm):
    """Log one truck trip (a single run can serve several subscriptions)."""

    class Meta:
        model = VoyageLivraison
        fields = ["date_voyage", "chauffeur", "vehicule", "notes"]
        widgets = {
            "date_voyage": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["chauffeur"].required = False
        self.fields["vehicule"].required = False
        self.fields["notes"].required = False


class LivraisonPartielleForm(forms.ModelForm):
    """
    Record one metered delivery against an AbonnementClient.

    Business rules enforced here (duplicated from AbonnementClient/
    LivraisonPartielle model.clean() for a friendlier form-level message —
    same pattern as MortaliteForm in elevage/forms.py):
      - The subscription must be ACTIF.
      - Cumulative delivered quantity cannot exceed quantite_totale_prevue
        when a quota is configured.
    """

    class Meta:
        model = LivraisonPartielle
        fields = ["abonnement", "voyage", "date", "quantite_livree", "notes"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
            "quantite_livree": forms.NumberInput(
                attrs={"step": "0.001", "min": "0.001"}
            ),
        }

    def __init__(self, *args, abonnement=None, branche=None, **kwargs):
        """
        Pass ``abonnement=<AbonnementClient instance>`` from the view to
        pre-select and lock the abonnement field, mirroring the
        lot=<LotElevage> kwarg pattern used throughout elevage/forms.py.

        Pass ``branche=<Branche instance>`` instead (or in addition) when
        the current user is locked to one branch (chef de branche /
        opérateur, BR-BRA-02), to scope the abonnement choices to that
        branche — LivraisonPartielle.branche is inherited from
        abonnement.branche, not stored directly.
        """
        super().__init__(*args, **kwargs)
        abonnement_qs = AbonnementClient.objects.filter(
            statut=AbonnementClient.STATUT_ACTIF
        ).select_related("client", "produit_fini")
        if branche:
            abonnement_qs = abonnement_qs.filter(branche=branche)
        self.fields["abonnement"].queryset = abonnement_qs
        self.fields["voyage"].queryset = VoyageLivraison.objects.order_by(
            "-date_voyage"
        )
        self.fields["voyage"].required = False
        self.fields["notes"].required = False

        if abonnement:
            self.fields["abonnement"].initial = abonnement
            self.fields["abonnement"].widget = forms.HiddenInput()
            self._abonnement = abonnement
        else:
            self._abonnement = None

    def clean_date(self):
        date = self.cleaned_data["date"]
        if date > datetime.date.today():
            raise ValidationError(
                "La date de livraison ne peut pas être dans le futur."
            )
        return date

    def clean(self):
        cleaned = super().clean()
        abonnement = cleaned.get("abonnement") or self._abonnement
        quantite = cleaned.get("quantite_livree")

        if abonnement and abonnement.statut != AbonnementClient.STATUT_ACTIF:
            raise ValidationError(
                "Impossible d'enregistrer une livraison sur un abonnement non actif."
            )

        if abonnement and quantite and abonnement.quantite_totale_prevue:
            deja_livre = abonnement.quantite_livree_cumulee
            if self.instance and self.instance.pk:
                deja_livre -= self.instance.quantite_livree
            if deja_livre + quantite > abonnement.quantite_totale_prevue:
                raise ValidationError(
                    f"الكمية المسلَّمة الإجمالية ({deja_livre + quantite}) "
                    f"تتجاوز الكمية المتعاقد عليها ({abonnement.quantite_totale_prevue})."
                )
        return cleaned


# ---------------------------------------------------------------------------
# PrixMarche form — daily egg market price entry
# ---------------------------------------------------------------------------


class PrixMarcheForm(forms.ModelForm):
    """Form for entering / editing a market price record."""

    class Meta:
        from clients.models import PrixMarche

        model = PrixMarche
        fields = ["produit_fini", "date", "prix_marche", "source", "notes"]
        widgets = {
            "produit_fini": forms.Select(attrs={"class": "form-control"}),
            "date": forms.DateInput(
                attrs={"type": "date", "class": "form-control"}, format="%Y-%m-%d"
            ),
            "prix_marche": forms.NumberInput(
                attrs={"class": "form-control", "step": "0.01", "min": "0"}
            ),
            "source": forms.TextInput(attrs={"class": "form-control"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Limit to egg-type products only.
        from production.models import ProduitFini

        self.fields["produit_fini"].queryset = ProduitFini.objects.filter(
            actif=True, type_produit__code="OEUFS"
        ).order_by("designation")


# ---------------------------------------------------------------------------
# PieceJointe formsets (v1.5) — one alias per attachment-capable model,
# built from core.forms.make_piece_jointe_formset. BLClient / FactureClient /
# PaiementClient never had an ad-hoc `piece_jointe` FileField, so this is a
# pure addition (same pattern as achats.forms / depenses.forms).
# ---------------------------------------------------------------------------

BLClientPieceJointeFormSet = make_piece_jointe_formset(extra=1)
FactureClientPieceJointeFormSet = make_piece_jointe_formset(extra=1)
PaiementClientPieceJointeFormSet = make_piece_jointe_formset(extra=1)
AcompteClientPieceJointeFormSet = make_piece_jointe_formset(extra=1)
