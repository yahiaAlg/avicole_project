"""
elevage/views.py

Lot d'élevage lifecycle management:
  - LotElevage CRUD + close action
  - Mortalité recording and deletion
  - Consommation recording, editing, deletion
"""

import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.forms import ModelForm, DateField, IntegerField, DecimalField
from django.shortcuts import get_object_or_404, redirect, render

from elevage.models import Consommation, LotElevage, Mortalite

# ---------------------------------------------------------------------------
# Inline forms
# ---------------------------------------------------------------------------


class LotElevageForm(ModelForm):
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Only BLs in RECU status make sense as chick source
        from achats.models import BLFournisseur

        self.fields["bl_fournisseur_poussins"].queryset = BLFournisseur.objects.filter(
            statut__in=[BLFournisseur.STATUT_RECU, BLFournisseur.STATUT_FACTURE]
        ).order_by("-date_bl")
        self.fields["bl_fournisseur_poussins"].required = False


class LotFermerForm(ModelForm):
    """Minimal form just to capture the closure date."""

    date_fermeture = DateField(
        label="Date de fermeture",
        initial=datetime.date.today,
    )

    class Meta:
        model = LotElevage
        fields = ["date_fermeture"]


class MortaliteForm(ModelForm):
    class Meta:
        model = Mortalite
        fields = ["date", "nombre", "cause", "notes"]


class ConsommationForm(ModelForm):
    class Meta:
        model = Consommation
        fields = ["date", "intrant", "quantite", "notes"]

    def __init__(self, *args, lot=None, **kwargs):
        super().__init__(*args, **kwargs)
        from intrants.models import Intrant

        self.fields["intrant"].queryset = (
            Intrant.objects.filter(
                categorie__consommable_en_lot=True,
                actif=True,
            )
            .select_related("categorie")
            .order_by("categorie__libelle", "designation")
        )
        self.lot = lot

    def clean(self):
        cleaned = super().clean()
        intrant = cleaned.get("intrant")
        quantite = cleaned.get("quantite")
        if intrant and quantite:
            try:
                stock_qty = intrant.stock.quantite
            except Exception:
                stock_qty = 0
            # Warn (not block) on negative stock — spec allows it but logs a warning
            if stock_qty < quantite:
                from django.core.exceptions import ValidationError

                raise ValidationError(
                    f"Stock insuffisant pour « {intrant.designation} » : "
                    f"{stock_qty} {intrant.unite_mesure} disponibles, "
                    f"{quantite} demandés. Créez un ajustement de stock si nécessaire."
                )
        return cleaned


# ---------------------------------------------------------------------------
# Lot d'élevage
# ---------------------------------------------------------------------------


@login_required
def lot_list(request):
    statut_filter = request.GET.get("statut", "ouvert")
    qs = LotElevage.objects.select_related("batiment", "fournisseur_poussins").order_by(
        "-date_ouverture"
    )
    if statut_filter in ("ouvert", "ferme"):
        qs = qs.filter(statut=statut_filter)
    return render(
        request,
        "elevage/lot_list.html",
        {
            "lots": qs,
            "statut_filter": statut_filter,
        },
    )


@login_required
def lot_create(request):
    if request.method == "POST":
        form = LotElevageForm(request.POST)
        if form.is_valid():
            lot = form.save(commit=False)
            lot.created_by = request.user
            lot.save()
            messages.success(request, f"Lot « {lot.designation} » ouvert.")
            return redirect("elevage:lot_detail", pk=lot.pk)
    else:
        form = LotElevageForm()
    return render(
        request, "elevage/lot_form.html", {"form": form, "action": "Ouvrir un lot"}
    )


@login_required
def lot_detail(request, pk):
    lot = get_object_or_404(
        LotElevage.objects.select_related("batiment", "fournisseur_poussins"), pk=pk
    )
    from elevage.utils import get_lot_summary

    summary = get_lot_summary(lot)
    mortalite_form = MortaliteForm(initial={"date": datetime.date.today()})
    conso_form = ConsommationForm(lot=lot, initial={"date": datetime.date.today()})
    return render(
        request,
        "elevage/lot_detail.html",
        {
            "lot": lot,
            "summary": summary,
            "mortalite_form": mortalite_form,
            "conso_form": conso_form,
        },
    )


@login_required
def lot_edit(request, pk):
    lot = get_object_or_404(LotElevage, pk=pk)
    if lot.statut == LotElevage.STATUT_FERME:
        messages.error(request, "Un lot fermé ne peut pas être modifié.")
        return redirect("elevage:lot_detail", pk=pk)
    if request.method == "POST":
        form = LotElevageForm(request.POST, instance=lot)
        if form.is_valid():
            form.save()
            messages.success(request, "Lot mis à jour.")
            return redirect("elevage:lot_detail", pk=pk)
    else:
        form = LotElevageForm(instance=lot)
    return render(
        request,
        "elevage/lot_form.html",
        {
            "form": form,
            "lot": lot,
            "action": "Modifier",
        },
    )


@login_required
def lot_fermer(request, pk):
    lot = get_object_or_404(LotElevage, pk=pk)
    if lot.statut == LotElevage.STATUT_FERME:
        messages.warning(request, "Ce lot est déjà fermé.")
        return redirect("elevage:lot_detail", pk=pk)
    if request.method == "POST":
        form = LotFermerForm(request.POST, instance=lot)
        if form.is_valid():
            date_fermeture = form.cleaned_data["date_fermeture"]
            lot.fermer(date_fermeture=date_fermeture)
            messages.success(
                request, f"Lot « {lot.designation} » fermé le {date_fermeture}."
            )
            return redirect("elevage:lot_detail", pk=pk)
    else:
        form = LotFermerForm(instance=lot)
    return render(
        request, "elevage/lot_fermer_confirm.html", {"form": form, "lot": lot}
    )


# ---------------------------------------------------------------------------
# Mortalité
# ---------------------------------------------------------------------------


@login_required
def mortalite_create(request, lot_pk):
    lot = get_object_or_404(LotElevage, pk=lot_pk)
    if lot.statut == LotElevage.STATUT_FERME:
        messages.error(request, "Impossible d'ajouter une mortalité sur un lot fermé.")
        return redirect("elevage:lot_detail", pk=lot_pk)
    if request.method == "POST":
        form = MortaliteForm(request.POST)
        if form.is_valid():
            mortalite = form.save(commit=False)
            mortalite.lot = lot
            mortalite.full_clean()  # runs Mortalite.clean()
            mortalite.save()
            messages.success(
                request, f"{mortalite.nombre} mortalité(s) enregistrée(s)."
            )
        else:
            messages.error(
                request, "Formulaire invalide. Vérifiez les données saisies."
            )
    return redirect("elevage:lot_detail", pk=lot_pk)


@login_required
def mortalite_delete(request, pk):
    mortalite = get_object_or_404(Mortalite, pk=pk)
    lot_pk = mortalite.lot_id
    if request.method == "POST":
        mortalite.delete()
        messages.success(request, "Enregistrement de mortalité supprimé.")
    return redirect("elevage:lot_detail", pk=lot_pk)


# ---------------------------------------------------------------------------
# Consommation
# ---------------------------------------------------------------------------


@login_required
def consommation_create(request, lot_pk):
    lot = get_object_or_404(LotElevage, pk=lot_pk)
    if lot.statut == LotElevage.STATUT_FERME:
        messages.error(
            request, "Impossible d'enregistrer une consommation sur un lot fermé."
        )
        return redirect("elevage:lot_detail", pk=lot_pk)
    if request.method == "POST":
        form = ConsommationForm(request.POST, lot=lot)
        if form.is_valid():
            conso = form.save(commit=False)
            conso.lot = lot
            conso.created_by = request.user
            conso.full_clean()
            conso.save()
            messages.success(request, "Consommation enregistrée.")
        else:
            for field, errs in form.errors.items():
                for e in errs:
                    messages.error(request, f"{field} : {e}")
    return redirect("elevage:lot_detail", pk=lot_pk)


@login_required
def consommation_edit(request, pk):
    conso = get_object_or_404(Consommation.objects.select_related("lot"), pk=pk)
    lot = conso.lot
    if lot.statut == LotElevage.STATUT_FERME:
        messages.error(request, "Le lot est fermé.")
        return redirect("elevage:lot_detail", pk=lot.pk)
    if request.method == "POST":
        form = ConsommationForm(request.POST, instance=conso, lot=lot)
        if form.is_valid():
            form.save()
            messages.success(request, "Consommation mise à jour.")
            return redirect("elevage:lot_detail", pk=lot.pk)
    else:
        form = ConsommationForm(instance=conso, lot=lot)
    return render(
        request,
        "elevage/consommation_form.html",
        {
            "form": form,
            "conso": conso,
            "lot": lot,
        },
    )


@login_required
def consommation_delete(request, pk):
    conso = get_object_or_404(Consommation, pk=pk)
    lot_pk = conso.lot_id
    if request.method == "POST":
        conso.delete()  # signal restores stock
        messages.success(request, "Consommation supprimée et stock restauré.")
    return redirect("elevage:lot_detail", pk=lot_pk)
