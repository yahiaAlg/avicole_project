"""
production/views.py

Production module:
  - ProduitFini catalogue
  - ProductionRecord (header) with ProductionLigne (lines) via inline formset
  - Validation: BROUILLON → VALIDE triggers stock entries via signal
  - Cost allocation helper exposed as an action before validation
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.forms import ModelForm, inlineformset_factory
from django.shortcuts import get_object_or_404, redirect, render

from production.models import ProductionLigne, ProductionRecord, ProduitFini

# ---------------------------------------------------------------------------
# Inline forms & formsets
# ---------------------------------------------------------------------------


class ProduitFiniForm(ModelForm):
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


class ProductionRecordForm(ModelForm):
    class Meta:
        model = ProductionRecord
        fields = [
            "lot",
            "date_production",
            "nombre_oiseaux_abattus",
            "poids_total_kg",
            "notes",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from elevage.models import LotElevage

        # Allow selecting any lot — open or closed (harvest may happen on closure day)
        self.fields["lot"].queryset = LotElevage.objects.order_by("-date_ouverture")


class ProductionLigneForm(ModelForm):
    class Meta:
        model = ProductionLigne
        fields = ["produit_fini", "quantite", "poids_unitaire_kg", "notes"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["produit_fini"].queryset = ProduitFini.objects.filter(
            actif=True
        ).order_by("type_produit", "designation")


ProductionLigneFormSet = inlineformset_factory(
    ProductionRecord,
    ProductionLigne,
    form=ProductionLigneForm,
    extra=3,
    can_delete=True,
    min_num=1,
    validate_min=True,
)


# ---------------------------------------------------------------------------
# ProduitFini catalogue
# ---------------------------------------------------------------------------


@login_required
def produit_fini_list(request):
    produits = ProduitFini.objects.select_related("stock").order_by(
        "type_produit", "designation"
    )
    return render(request, "production/produit_fini_list.html", {"produits": produits})


@login_required
def produit_fini_create(request):
    if request.method == "POST":
        form = ProduitFiniForm(request.POST)
        if form.is_valid():
            p = form.save()
            messages.success(request, f"Produit « {p.designation} » créé.")
            return redirect("production:produit_fini_list")
    else:
        form = ProduitFiniForm()
    return render(
        request, "production/produit_fini_form.html", {"form": form, "action": "Créer"}
    )


@login_required
def produit_fini_edit(request, pk):
    produit = get_object_or_404(ProduitFini, pk=pk)
    if request.method == "POST":
        form = ProduitFiniForm(request.POST, instance=produit)
        if form.is_valid():
            form.save()
            messages.success(request, "Produit mis à jour.")
            return redirect("production:produit_fini_list")
    else:
        form = ProduitFiniForm(instance=produit)
    return render(
        request,
        "production/produit_fini_form.html",
        {
            "form": form,
            "produit": produit,
            "action": "Modifier",
        },
    )


# ---------------------------------------------------------------------------
# ProductionRecord
# ---------------------------------------------------------------------------


@login_required
def production_list(request):
    qs = ProductionRecord.objects.select_related("lot").order_by("-date_production")
    lot_pk = request.GET.get("lot")
    if lot_pk:
        qs = qs.filter(lot_id=lot_pk)
    statut = request.GET.get("statut")
    if statut in (ProductionRecord.STATUT_BROUILLON, ProductionRecord.STATUT_VALIDE):
        qs = qs.filter(statut=statut)
    return render(
        request,
        "production/production_list.html",
        {
            "productions": qs,
            "statut_filter": statut,
        },
    )


@login_required
def production_create(request):
    if request.method == "POST":
        form = ProductionRecordForm(request.POST)
        formset = ProductionLigneFormSet(request.POST)
        if form.is_valid() and formset.is_valid():
            record = form.save(commit=False)
            record.created_by = request.user
            record.save()
            formset.instance = record
            formset.save()
            messages.success(request, f"Enregistrement de production créé (brouillon).")
            return redirect("production:production_detail", pk=record.pk)
    else:
        form = ProductionRecordForm()
        formset = ProductionLigneFormSet()
    return render(
        request,
        "production/production_form.html",
        {
            "form": form,
            "formset": formset,
            "action": "Créer",
        },
    )


@login_required
def production_detail(request, pk):
    record = get_object_or_404(
        ProductionRecord.objects.select_related("lot", "created_by").prefetch_related(
            "lignes__produit_fini"
        ),
        pk=pk,
    )
    return render(request, "production/production_detail.html", {"record": record})


@login_required
def production_edit(request, pk):
    record = get_object_or_404(ProductionRecord, pk=pk)
    if record.statut == ProductionRecord.STATUT_VALIDE:
        messages.error(request, "Un enregistrement validé ne peut plus être modifié.")
        return redirect("production:production_detail", pk=pk)
    if request.method == "POST":
        form = ProductionRecordForm(request.POST, instance=record)
        formset = ProductionLigneFormSet(request.POST, instance=record)
        if form.is_valid() and formset.is_valid():
            form.save()
            formset.save()
            messages.success(request, "Enregistrement mis à jour.")
            return redirect("production:production_detail", pk=pk)
    else:
        form = ProductionRecordForm(instance=record)
        formset = ProductionLigneFormSet(instance=record)
    return render(
        request,
        "production/production_form.html",
        {
            "form": form,
            "formset": formset,
            "record": record,
            "action": "Modifier",
        },
    )


@login_required
def production_allouer_cout(request, pk):
    """
    POST-only action: distribute lot total cost across lines before validation.
    Must be called while the record is still in BROUILLON state.
    """
    record = get_object_or_404(ProductionRecord, pk=pk)
    if record.statut == ProductionRecord.STATUT_VALIDE:
        messages.warning(request, "L'enregistrement est déjà validé.")
        return redirect("production:production_detail", pk=pk)
    if request.method == "POST":
        try:
            from production.utils import allouer_cout_production

            allouer_cout_production(record)
            messages.success(request, "Coûts unitaires estimés alloués aux lignes.")
        except ValueError as e:
            messages.error(request, str(e))
    return redirect("production:production_detail", pk=pk)


@login_required
def production_valider(request, pk):
    """
    POST-only action: transition BROUILLON → VALIDE.
    The post_save signal (signals_production) handles stock entry.
    """
    record = get_object_or_404(ProductionRecord, pk=pk)
    if record.statut == ProductionRecord.STATUT_VALIDE:
        messages.warning(request, "Déjà validé.")
        return redirect("production:production_detail", pk=pk)
    if request.method == "POST":
        if not record.lignes.exists():
            messages.error(
                request, "Impossible de valider : aucune ligne de production."
            )
            return redirect("production:production_detail", pk=pk)
        record.statut = ProductionRecord.STATUT_VALIDE
        record.save()  # triggers post_save signal → stock entries
        messages.success(
            request,
            f"Production validée. Le stock des produits finis a été mis à jour.",
        )
    return redirect("production:production_detail", pk=pk)
