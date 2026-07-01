"""
stock/forms.py

Forms for manual stock adjustments.
StockMouvement records are immutable and created exclusively by signals —
they have no form.
"""

import datetime
from django import forms
from django.core.exceptions import ValidationError

from stock.models import StockAjustement
from intrants.models import Intrant
from production.models import ProduitFini


class StockAjustementForm(forms.ModelForm):
    """
    Manual stock adjustment form.

    BR-INT-04: a mandatory reason is required; the record is flagged in the
    audit trail automatically (via post_save signal).

    BR-BRA-07: a stock balance is keyed by (branche, item), so the branche
    being corrected is now an explicit, required field — `quantite_avant`
    must be read from that branche's StockIntrant/StockProduitFini row.

    Exactly one of (intrant / produit_fini) must be filled, consistent with
    the chosen segment.  quantite_avant is read-only — populated from the
    current (branche, item) stock balance by the view before rendering.

    Pass `branche=<Branche instance>` from the view when the current user
    is locked to one branch (chef de branche / opérateur, BR-BRA-02) to
    pre-select and lock the field.
    """

    class Meta:
        model = StockAjustement
        fields = [
            "segment",
            "branche",
            "intrant",
            "produit_fini",
            "date_ajustement",
            "quantite_avant",
            "quantite_apres",
            "raison",
        ]
        widgets = {
            "date_ajustement": forms.DateInput(attrs={"type": "date"}),
            "quantite_avant": forms.NumberInput(
                attrs={"readonly": True, "step": "0.001"}
            ),
            "quantite_apres": forms.NumberInput(attrs={"step": "0.001", "min": "0"}),
            "raison": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, branche=None, **kwargs):
        super().__init__(*args, **kwargs)
        from core.models import Branche

        self.fields["branche"].queryset = Branche.objects.filter(actif=True).order_by(
            "nom"
        )
        if branche:
            self.fields["branche"].initial = branche
            self.fields["branche"].widget = forms.HiddenInput()
        self.fields["intrant"].queryset = Intrant.objects.filter(actif=True).order_by(
            "categorie__libelle", "designation"
        )
        self.fields["produit_fini"].queryset = ProduitFini.objects.filter(actif=True)
        self.fields["intrant"].required = False
        self.fields["produit_fini"].required = False

    def clean(self):
        cleaned = super().clean()
        segment = cleaned.get("segment")
        intrant = cleaned.get("intrant")
        produit_fini = cleaned.get("produit_fini")
        quantite_apres = cleaned.get("quantite_apres")

        if segment == StockAjustement.SEGMENT_INTRANT:
            if not intrant:
                raise ValidationError(
                    {"intrant": "Un intrant doit être sélectionné pour ce segment."}
                )
            if produit_fini:
                raise ValidationError(
                    {"produit_fini": "Laissez ce champ vide pour le segment Intrant."}
                )
        elif segment == StockAjustement.SEGMENT_PRODUIT_FINI:
            if not produit_fini:
                raise ValidationError(
                    {
                        "produit_fini": "Un produit fini doit être sélectionné pour ce segment."
                    }
                )
            if intrant:
                raise ValidationError(
                    {"intrant": "Laissez ce champ vide pour le segment Produit Fini."}
                )

        if quantite_apres is not None and quantite_apres < 0:
            raise ValidationError(
                {
                    "quantite_apres": "La quantité après ajustement ne peut pas être négative."
                }
            )

        date_aj = cleaned.get("date_ajustement")
        if date_aj and date_aj > datetime.date.today():
            raise ValidationError(
                {
                    "date_ajustement": "La date d'ajustement ne peut pas être dans le futur."
                }
            )

        return cleaned
