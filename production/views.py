"""
production/views.py

Function-based views for the production domain:

  ProduitFini      : list, create, edit, toggle active, detail
  ProductionRecord : list, create, detail, edit, validate (BROUILLON → VALIDE)
  ProductionLigne  : managed via inline formset on ProductionRecord forms

Business rules enforced here (complementing model.clean() and signals):
  - Only BROUILLON records may be edited or deleted.
  - The BROUILLON → VALIDE transition triggers stock entries via post_save
    signal (production/signals.py); the view calls allouer_cout_production()
    first so cost allocations are in place before validation.
  - nombre_oiseaux_abattus cannot exceed lot.effectif_vivant (form + view guard).
  - A validated record is immutable — no edit or delete views are provided.

All write operations use Post-Redirect-Get.
State changes (validate, toggle active) are POST-only.
"""

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db import transaction
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from production.forms import (
    ProduitFiniForm,
    ProductionRecordForm,
    ProductionLigneFormSet,
)
from production.models import ProduitFini, ProductionRecord, ProductionLigne
from production.utils import (
    allouer_cout_production,
    get_production_dashboard,
    get_rendement_abattage,
)

logger = logging.getLogger(__name__)

LOGIN_URL = "core:login"
PER_PAGE = 25


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _paginate(qs, page_number, per_page=PER_PAGE):
    paginator = Paginator(qs, per_page)
    try:
        return paginator.page(page_number)
    except PageNotAnInteger:
        return paginator.page(1)
    except EmptyPage:
        return paginator.page(paginator.num_pages)


def _assert_brouillon(record, request):
    """
    Return True if the ProductionRecord is still a draft (BROUILLON).
    Add an error message and return False if it has already been validated.
    """
    if record.statut == ProductionRecord.STATUT_VALIDE:
        messages.error(
            request,
            f"Cet enregistrement de production est validé et ne peut plus être modifié.",
        )
        return False
    return True


# ===========================================================================
# ProduitFini — List
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def produit_fini_list(request):
    """
    List all finished-product types in the catalogue.

    Filters:
      ?type_produit=<val>  — filter by product type
      ?actif=0             — include inactive products (default: active only)
      ?q=<search>          — search by designation
    """
    qs = ProduitFini.objects.order_by("type_produit", "designation")

    actif_param = request.GET.get("actif", "1")
    if actif_param != "0":
        qs = qs.filter(actif=True)

    type_produit = request.GET.get("type_produit", "")
    if type_produit:
        qs = qs.filter(type_produit=type_produit)

    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(designation__icontains=q)

    page = _paginate(qs, request.GET.get("page"))

    return render(
        request,
        "production/produit_fini_list.html",
        {
            "page": page,
            "q": q,
            "type_produit": type_produit,
            "actif_param": actif_param,
            "type_choices": ProduitFini.TYPE_CHOICES,
            "title": "Catalogue — Produits finis",
        },
    )


# ===========================================================================
# ProduitFini — Create
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def produit_fini_create(request):
    """
    Add a new finished-product type to the catalogue.
    The post_save signal (production/signals.py) auto-creates a StockProduitFini
    with quantite=0 so stock lookups never raise RelatedObjectDoesNotExist.
    """
    if request.method == "POST":
        form = ProduitFiniForm(request.POST)
        if form.is_valid():
            try:
                produit = form.save()
                messages.success(
                    request,
                    f"Produit fini « {produit.designation} » créé avec succès.",
                )
                logger.info(
                    "ProduitFini pk=%s ('%s') created by '%s'.",
                    produit.pk,
                    produit.designation,
                    request.user,
                )
                return redirect("production:produit_fini_detail", pk=produit.pk)
            except Exception as exc:
                logger.exception("Error creating ProduitFini: %s", exc)
                messages.error(request, f"Erreur lors de la création : {exc}")
        else:
            messages.error(request, "Veuillez corriger les erreurs ci-dessous.")
    else:
        form = ProduitFiniForm()

    return render(
        request,
        "production/produit_fini_form.html",
        {
            "form": form,
            "title": "Nouveau produit fini",
            "action_label": "Créer",
        },
    )


# ===========================================================================
# ProduitFini — AJAX quick-create (used by the production record form modal)
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def produit_fini_create_ajax(request):
    """
    POST-only endpoint: create a ProduitFini from the inline modal on the
    production record form and return JSON so the form can inject the new
    option into every produit_fini select without a full page reload.

    Returns:
        {"ok": true,  "pk": <int>, "label": "<str>"}
        {"ok": false, "errors": {field: [msg, ...]}}
    """
    if request.method != "POST":
        return JsonResponse(
            {"ok": False, "errors": {"__all__": ["Méthode non autorisée."]}}, status=405
        )

    form = ProduitFiniForm(request.POST)
    if form.is_valid():
        produit = form.save()
        logger.info(
            "ProduitFini pk=%s ('%s') created via AJAX by '%s'.",
            produit.pk,
            produit.designation,
            request.user,
        )
        return JsonResponse(
            {
                "ok": True,
                "pk": produit.pk,
                "label": str(produit),
                "designation": produit.designation,
                "prix_vente_defaut": str(produit.prix_vente_defaut),
            }
        )
    else:
        return JsonResponse({"ok": False, "errors": form.errors}, status=400)


# ===========================================================================
# ProduitFini — Detail
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def produit_fini_detail(request, pk):
    """
    Detail view for one finished-product type: catalogue info, current stock
    balance, recent stock movements, and recent production lines.
    """
    produit = get_object_or_404(ProduitFini, pk=pk)

    try:
        stock = produit.stock
    except Exception:
        stock = None

    from stock.models import StockMouvement

    mouvements = (
        StockMouvement.objects.filter(produit_fini=produit)
        .select_related("created_by")
        .order_by("-date_mouvement", "-created_at")[:30]
    )

    lignes_recentes = (
        ProductionLigne.objects.filter(produit_fini=produit)
        .select_related("production__lot")
        .order_by("-production__date_production")[:20]
    )

    return render(
        request,
        "production/produit_fini_detail.html",
        {
            "produit": produit,
            "stock": stock,
            "mouvements": mouvements,
            "lignes_recentes": lignes_recentes,
            "title": f"Produit fini — {produit.designation}",
        },
    )


# ===========================================================================
# ProduitFini — Edit
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def produit_fini_edit(request, pk):
    produit = get_object_or_404(ProduitFini, pk=pk)

    if request.method == "POST":
        form = ProduitFiniForm(request.POST, instance=produit)
        if form.is_valid():
            try:
                form.save()
                messages.success(
                    request, f"Produit fini « {produit.designation} » mis à jour."
                )
                logger.info(
                    "ProduitFini pk=%s updated by '%s'.", produit.pk, request.user
                )
                return redirect("production:produit_fini_detail", pk=produit.pk)
            except Exception as exc:
                logger.exception("Error updating ProduitFini pk=%s: %s", pk, exc)
                messages.error(request, f"Erreur lors de la mise à jour : {exc}")
        else:
            messages.error(request, "Veuillez corriger les erreurs ci-dessous.")
    else:
        form = ProduitFiniForm(instance=produit)

    return render(
        request,
        "production/produit_fini_form.html",
        {
            "form": form,
            "produit": produit,
            "title": f"Modifier — {produit.designation}",
            "action_label": "Enregistrer les modifications",
        },
    )


# ===========================================================================
# ProduitFini — Toggle active
# ===========================================================================


@login_required(login_url=LOGIN_URL)
@require_POST
def produit_fini_toggle_active(request, pk):
    """Activate or deactivate a produit fini (POST-only)."""
    produit = get_object_or_404(ProduitFini, pk=pk)
    produit.actif = not produit.actif
    produit.save(update_fields=["actif"])
    state = "activé" if produit.actif else "désactivé"
    messages.success(request, f"Produit fini « {produit.designation} » {state}.")
    logger.info(
        "ProduitFini pk=%s set actif=%s by '%s'.",
        produit.pk,
        produit.actif,
        request.user,
    )
    return redirect("production:produit_fini_list")


# ===========================================================================
# ProductionRecord — List
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def production_record_list(request):
    """
    List all production records (harvest events).

    Filters:
      ?statut=brouillon|valide
      ?lot=<pk>
      ?date_debut=YYYY-MM-DD, ?date_fin=YYYY-MM-DD
      ?q=<search>  — lot designation
    """
    from elevage.models import LotElevage

    qs = (
        ProductionRecord.objects.select_related("lot__batiment", "created_by")
        .prefetch_related("lignes__produit_fini")
        .order_by("-date_production", "-created_at")
    )

    statut = request.GET.get("statut", "")
    if statut in (ProductionRecord.STATUT_BROUILLON, ProductionRecord.STATUT_VALIDE):
        qs = qs.filter(statut=statut)

    lot_pk = request.GET.get("lot", "")
    if lot_pk:
        qs = qs.filter(lot_id=lot_pk)

    date_debut = request.GET.get("date_debut", "")
    date_fin = request.GET.get("date_fin", "")
    if date_debut:
        qs = qs.filter(date_production__gte=date_debut)
    if date_fin:
        qs = qs.filter(date_production__lte=date_fin)

    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(lot__designation__icontains=q)

    page = _paginate(qs, request.GET.get("page"))
    lots = LotElevage.objects.order_by("-date_ouverture")

    # Summary counts
    nb_brouillons = ProductionRecord.objects.filter(
        statut=ProductionRecord.STATUT_BROUILLON
    ).count()
    nb_valides = ProductionRecord.objects.filter(
        statut=ProductionRecord.STATUT_VALIDE
    ).count()

    return render(
        request,
        "production/production_record_list.html",
        {
            "page": page,
            "q": q,
            "statut": statut,
            "lot_pk": lot_pk,
            "date_debut": date_debut,
            "date_fin": date_fin,
            "lots": lots,
            "nb_brouillons": nb_brouillons,
            "nb_valides": nb_valides,
            "statut_choices": ProductionRecord.STATUT_CHOICES,
            "title": "Enregistrements de production",
        },
    )


# ===========================================================================
# ProductionRecord — Create
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def production_record_create(request, lot_pk=None):
    """
    Create a new production record (harvest event) in BROUILLON status.

    Accepts an optional `lot_pk` URL parameter to pre-select the lot
    (used when navigating from a lot's detail page).

    The inline formset allows entering multiple ProductionLigne records
    (one per produit fini type generated at harvest).
    Cost allocation (allouer_cout_production) is NOT called at creation —
    it runs at validation time to reflect the latest lot costs.
    """
    from elevage.models import LotElevage

    lot = None
    if lot_pk:
        lot = get_object_or_404(LotElevage, pk=lot_pk)

    if request.method == "POST":
        form = ProductionRecordForm(request.POST, lot=lot)
        formset = ProductionLigneFormSet(request.POST)

        if form.is_valid() and formset.is_valid():
            try:
                with transaction.atomic():
                    record = form.save(commit=False)
                    record.created_by = request.user
                    record.statut = ProductionRecord.STATUT_BROUILLON
                    record.save()

                    formset.instance = record
                    formset.save()

                messages.success(
                    request,
                    f"Enregistrement de production créé (brouillon) pour le lot "
                    f"« {record.lot.designation} » — {record.date_production}. "
                    "Vérifiez les lignes puis validez pour mettre à jour le stock.",
                )
                logger.info(
                    "ProductionRecord pk=%s created (BROUILLON) by '%s' "
                    "(lot pk=%s, date=%s, oiseaux=%s).",
                    record.pk,
                    request.user,
                    record.lot_id,
                    record.date_production,
                    record.nombre_oiseaux_abattus,
                )
                return redirect("production:production_record_detail", pk=record.pk)

            except Exception as exc:
                logger.exception("Error creating ProductionRecord: %s", exc)
                messages.error(request, f"Erreur lors de la création : {exc}")
        else:
            messages.error(
                request,
                "Veuillez corriger les erreurs ci-dessous (entête et/ou lignes).",
            )
    else:
        form = ProductionRecordForm(lot=lot)
        # Bind an unbound formset to an unsaved instance so the inline FK works
        tmp_record = ProductionRecord()
        if lot:
            tmp_record.lot = lot
        formset = ProductionLigneFormSet(instance=tmp_record)

    return render(
        request,
        "production/production_record_form.html",
        {
            "form": form,
            "formset": formset,
            "lot": lot,
            "title": "Nouveau enregistrement de production",
            "action_label": "Enregistrer (brouillon)",
        },
    )


# ===========================================================================
# ProductionRecord — Detail
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def production_record_detail(request, pk):
    """
    Full detail view for one production record.

    Displays:
      - Header data (lot, date, oiseaux abattus, poids)
      - Production lines (produits finis, quantités, coûts estimés)
      - Slaughter yield % (rendement_abattage) for carcasse lines
      - Stock impact summary (only for validated records)
    """
    record = get_object_or_404(
        ProductionRecord.objects.select_related("lot__batiment", "created_by"),
        pk=pk,
    )
    lignes = record.lignes.select_related("produit_fini").order_by(
        "produit_fini__type_produit"
    )

    rendement = None
    if record.statut == ProductionRecord.STATUT_VALIDE:
        rendement = get_rendement_abattage(record)

    # Stock impact: mouvements created by this record's signal
    from stock.models import StockMouvement

    mouvements = (
        StockMouvement.objects.filter(
            source=StockMouvement.SOURCE_PRODUCTION,
            reference_id=record.pk,
        )
        .select_related("produit_fini")
        .order_by("date_mouvement")
    )

    valeur_totale_production = sum(ligne.valeur_totale for ligne in lignes)

    return render(
        request,
        "production/production_record_detail.html",
        {
            "record": record,
            "lignes": lignes,
            "rendement": rendement,
            "mouvements": mouvements,
            "valeur_totale_production": valeur_totale_production,
            "title": f"Production — {record.lot.designation} — {record.date_production}",
        },
    )


# ===========================================================================
# ProductionRecord — Edit  (BROUILLON only)
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def production_record_edit(request, pk):
    """
    Edit a BROUILLON production record and its lines.

    Validated records are immutable — this view redirects with an error if
    the record has already been validated.
    """
    record = get_object_or_404(ProductionRecord.objects.select_related("lot"), pk=pk)

    if not _assert_brouillon(record, request):
        return redirect("production:production_record_detail", pk=record.pk)

    if request.method == "POST":
        form = ProductionRecordForm(request.POST, instance=record, lot=record.lot)
        formset = ProductionLigneFormSet(request.POST, instance=record)

        if form.is_valid() and formset.is_valid():
            try:
                with transaction.atomic():
                    form.save()
                    formset.save()

                messages.success(
                    request,
                    f"Enregistrement de production du {record.date_production} mis à jour.",
                )
                logger.info(
                    "ProductionRecord pk=%s updated by '%s'.", record.pk, request.user
                )
                return redirect("production:production_record_detail", pk=record.pk)

            except Exception as exc:
                logger.exception("Error updating ProductionRecord pk=%s: %s", pk, exc)
                messages.error(request, f"Erreur lors de la mise à jour : {exc}")
        else:
            messages.error(
                request,
                "Veuillez corriger les erreurs ci-dessous (entête et/ou lignes).",
            )
    else:
        form = ProductionRecordForm(instance=record, lot=record.lot)
        formset = ProductionLigneFormSet(instance=record)

    return render(
        request,
        "production/production_record_form.html",
        {
            "form": form,
            "formset": formset,
            "record": record,
            "lot": record.lot,
            "title": f"Modifier — Production {record.date_production}",
            "action_label": "Enregistrer les modifications",
        },
    )


# ===========================================================================
# ProductionRecord — Validate  (BROUILLON → VALIDE, POST-only)
# ===========================================================================


@login_required(login_url=LOGIN_URL)
@require_POST
def production_record_valider(request, pk):
    """
    Transition a BROUILLON ProductionRecord to VALIDE (POST-only).

    Sequence:
      1. Guard: record must be BROUILLON and have at least one ligne.
      2. Guard: nombre_oiseaux_abattus ≤ lot.effectif_vivant (re-checked
         atomically to guard against concurrent lot updates).
      3. Call allouer_cout_production() to set cout_unitaire_estime on each
         ligne using the lot's current total cost.
      4. Set statut = VALIDE and save — the post_save signal takes over:
           - Increases StockProduitFini.quantite per ligne.
           - Recalculates cout_moyen_production (weighted average).
           - Creates StockMouvement (ENTREE / PRODUCTION) per ligne.
    """
    record = get_object_or_404(ProductionRecord.objects.select_related("lot"), pk=pk)

    if not _assert_brouillon(record, request):
        return redirect("production:production_record_detail", pk=record.pk)

    # Guard: at least one ligne required.
    if not record.lignes.exists():
        messages.error(
            request,
            "Impossible de valider un enregistrement sans lignes de production. "
            "Ajoutez au moins un produit fini avant de valider.",
        )
        return redirect("production:production_record_detail", pk=record.pk)

    try:
        with transaction.atomic():
            # Re-check effectif vivant atomically (select_for_update on lot).
            from elevage.models import LotElevage

            lot = LotElevage.objects.select_for_update().get(pk=record.lot_id)
            effectif = lot.effectif_vivant

            if record.nombre_oiseaux_abattus > effectif:
                messages.error(
                    request,
                    f"Impossible de valider : le nombre d'oiseaux abattus "
                    f"({record.nombre_oiseaux_abattus}) dépasse l'effectif vivant "
                    f"actuel du lot ({effectif}). Modifiez l'enregistrement.",
                )
                return redirect("production:production_record_detail", pk=record.pk)

            # Step 3: allocate lot costs to production lines.
            try:
                allouer_cout_production(record)
            except Exception as alloc_exc:
                # Non-fatal: log and continue with existing (possibly zero) costs.
                logger.warning(
                    "allouer_cout_production failed for ProductionRecord pk=%s: %s. "
                    "Proceeding with existing cout_unitaire_estime values.",
                    record.pk,
                    alloc_exc,
                )

            # Step 4: transition to VALIDE — signal fires here.
            record.statut = ProductionRecord.STATUT_VALIDE
            record.save()

        messages.success(
            request,
            f"Production du {record.date_production} validée. "
            f"Stock produits finis mis à jour pour {record.lignes.count()} produit(s).",
        )
        logger.info(
            "ProductionRecord pk=%s validated by '%s' "
            "(lot pk=%s, oiseaux=%s, lignes=%s).",
            record.pk,
            request.user,
            record.lot_id,
            record.nombre_oiseaux_abattus,
            record.lignes.count(),
        )

    except Exception as exc:
        logger.exception("Error validating ProductionRecord pk=%s: %s", pk, exc)
        messages.error(
            request,
            f"Erreur lors de la validation : {exc}",
        )

    return redirect("production:production_record_detail", pk=record.pk)


# ===========================================================================
# ProductionRecord — Delete  (BROUILLON only, POST-only)
# ===========================================================================


@login_required(login_url=LOGIN_URL)
@require_POST
def production_record_delete(request, pk):
    """
    Delete a BROUILLON production record (POST-only).

    Validated records cannot be deleted — they are immutable audit records.
    No stock reversal is needed because BROUILLON records have no stock impact.
    """
    record = get_object_or_404(ProductionRecord.objects.select_related("lot"), pk=pk)

    if not _assert_brouillon(record, request):
        return redirect("production:production_record_detail", pk=record.pk)

    try:
        lot_ref = record.lot.designation
        date_ref = record.date_production
        record.delete()
        messages.success(
            request,
            f"Enregistrement de production du {date_ref} (lot « {lot_ref} ») supprimé.",
        )
        logger.info("ProductionRecord pk=%s deleted by '%s'.", pk, request.user)
    except Exception as exc:
        logger.exception("Error deleting ProductionRecord pk=%s: %s", pk, exc)
        messages.error(request, f"Erreur lors de la suppression : {exc}")
        return redirect("production:production_record_detail", pk=pk)

    return redirect("production:production_record_list")


# ===========================================================================
# Production Dashboard
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def production_dashboard(request):
    """
    Production module dashboard.

    Displays:
      - Per-lot production summary table (from get_production_dashboard())
      - Draft (BROUILLON) records awaiting validation
      - Recent validated records (last 10)
      - Optional date-range filter passed through to get_production_dashboard()
    """
    import datetime

    date_debut_str = request.GET.get("date_debut", "")
    date_fin_str = request.GET.get("date_fin", "")

    date_debut = None
    date_fin = None
    try:
        if date_debut_str:
            date_debut = datetime.datetime.strptime(date_debut_str, "%Y-%m-%d").date()
        if date_fin_str:
            date_fin = datetime.datetime.strptime(date_fin_str, "%Y-%m-%d").date()
    except ValueError:
        pass

    dashboard_rows = get_production_dashboard(date_debut=date_debut, date_fin=date_fin)

    brouillons = (
        ProductionRecord.objects.filter(statut=ProductionRecord.STATUT_BROUILLON)
        .select_related("lot")
        .order_by("-date_production")
    )

    valides_recents = (
        ProductionRecord.objects.filter(statut=ProductionRecord.STATUT_VALIDE)
        .select_related("lot")
        .order_by("-date_production")[:10]
    )

    # Aggregate totals for the filtered period
    valides_qs = ProductionRecord.objects.filter(statut=ProductionRecord.STATUT_VALIDE)
    if date_debut:
        valides_qs = valides_qs.filter(date_production__gte=date_debut)
    if date_fin:
        valides_qs = valides_qs.filter(date_production__lte=date_fin)

    totaux = valides_qs.aggregate(
        total_oiseaux=Sum("nombre_oiseaux_abattus"),
        total_poids=Sum("poids_total_kg"),
    )

    # Stock produits finis summary
    from stock.models import StockProduitFini

    stock_produits = list(
        StockProduitFini.objects.select_related("produit_fini").order_by(
            "produit_fini__type_produit", "produit_fini__designation"
        )
    )
    valeur_stock_total = sum(float(s.valeur_stock) for s in stock_produits)
    revenu_potentiel = sum(
        float(s.quantite) * float(s.produit_fini.prix_vente_defaut)
        for s in stock_produits
    )
    nb_en_alerte = sum(1 for s in stock_produits if s.en_alerte)

    from elevage.models import LotElevage

    lots_actifs = LotElevage.objects.filter(statut=LotElevage.STATUT_OUVERT).count()

    return render(
        request,
        "production/production_dashboard.html",
        {
            "dashboard_rows": dashboard_rows,
            "brouillons": brouillons,
            "valides_recents": valides_recents,
            "totaux": totaux,
            "date_debut": date_debut_str,
            "date_fin": date_fin_str,
            "stock_produits": stock_produits,
            "valeur_stock_total": valeur_stock_total,
            "revenu_potentiel": revenu_potentiel,
            "nb_en_alerte": nb_en_alerte,
            "lots_actifs": lots_actifs,
            "title": "Tableau de bord — Production",
        },
    )


# ===========================================================================
# AJAX helpers
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def lot_effectif_json(request, lot_pk):
    """
    Return the current effectif vivant for a lot as JSON.
    Called when the user selects a lot in the ProductionRecord form to
    show the maximum number of birds that can be harvested.

    Returns:
        {
          "effectif_vivant": int,
          "nombre_poussins_initial": int,
          "total_mortalite": int,
          "taux_mortalite": float,
          "designation": str,
          "statut": str,
        }
    """
    from elevage.models import LotElevage

    lot = get_object_or_404(LotElevage, pk=lot_pk)

    return JsonResponse(
        {
            "effectif_vivant": lot.effectif_vivant,
            "nombre_poussins_initial": lot.nombre_poussins_initial,
            "total_mortalite": lot.total_mortalite,
            "taux_mortalite": float(lot.taux_mortalite),
            "designation": lot.designation,
            "statut": lot.statut,
        }
    )


@login_required(login_url=LOGIN_URL)
def produit_fini_stock_json(request, pk):
    """
    Return current stock balance for a ProduitFini as JSON.
    Called when building BL Client lines to show available quantity.

    Returns:
        {
          "quantite": float,
          "cout_moyen_production": float,
          "unite_mesure": str,
          "en_alerte": bool,
        }
    """
    produit = get_object_or_404(ProduitFini, pk=pk)
    try:
        stock = produit.stock
        data = {
            "quantite": float(stock.quantite),
            "cout_moyen_production": float(stock.cout_moyen_production),
            "unite_mesure": produit.unite_mesure,
            "en_alerte": stock.en_alerte,
            "seuil_alerte": float(stock.seuil_alerte),
            "prix_vente_defaut": float(produit.prix_vente_defaut),  # ← add this line
        }
    except Exception:
        data = {
            "quantite": 0.0,
            "cout_moyen_production": 0.0,
            "unite_mesure": produit.unite_mesure,
            "en_alerte": True,
            "seuil_alerte": 0.0,
            "prix_vente_defaut": float(produit.prix_vente_defaut),  # ← add this line
        }
    return JsonResponse(data)


from django.http import JsonResponse
from django.views.decorators.http import require_GET
from production.models import ProduitFini


@require_GET
def produit_fini_detail_json(request, pk):
    try:
        p = ProduitFini.objects.get(pk=pk, actif=True)
        stock = p.quantite_en_stock
        return JsonResponse(
            {
                "quantite": float(stock),
                "unite_mesure": p.unite_mesure,
                "prix_vente_defaut": float(p.prix_vente_defaut),
            }
        )
    except ProduitFini.DoesNotExist:
        return JsonResponse({"error": "not found"}, status=404)


# ===========================================================================
# AJAX — Dashboard charts data
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def production_dashboard_charts_json(request):
    """
    Returns JSON data for all Chart.js charts on the production dashboard:

    - monthly_production  : last 6 months oiseaux abattus + poids total
    - stock_par_produit   : current stock quantity per produit fini
    - production_par_type : total quantity produced per product type (all time)
    - cout_vs_revenu      : per-lot cost vs revenue potential comparison
    """
    import datetime
    from collections import defaultdict
    from stock.models import StockProduitFini
    from production.models import ProductionRecord, ProductionLigne, ProduitFini

    today = datetime.date.today()

    # ── 1. Monthly production: last 6 months ─────────────────────────────
    months = []
    for i in range(5, -1, -1):
        first_of_month = today.replace(day=1) - datetime.timedelta(days=1)
        # Go back i months from current month
        m = today.month - i
        y = today.year
        while m <= 0:
            m += 12
            y -= 1
        months.append((y, m))

    monthly_labels = []
    monthly_oiseaux = []
    monthly_poids = []

    FR_MONTHS = [
        "Jan",
        "Fév",
        "Mar",
        "Avr",
        "Mai",
        "Juin",
        "Juil",
        "Août",
        "Sep",
        "Oct",
        "Nov",
        "Déc",
    ]

    for y, m in months:
        monthly_labels.append(f"{FR_MONTHS[m-1]} {y}")
        agg = ProductionRecord.objects.filter(
            statut=ProductionRecord.STATUT_VALIDE,
            date_production__year=y,
            date_production__month=m,
        ).aggregate(
            oiseaux=Sum("nombre_oiseaux_abattus"),
            poids=Sum("poids_total_kg"),
        )
        monthly_oiseaux.append(agg["oiseaux"] or 0)
        monthly_poids.append(float(agg["poids"] or 0))

    # ── 2. Stock par produit fini ─────────────────────────────────────────
    stocks = (
        StockProduitFini.objects.select_related("produit_fini")
        .filter(produit_fini__actif=True)
        .order_by("produit_fini__designation")
    )
    stock_labels = [s.produit_fini.designation for s in stocks]
    stock_quantities = [float(s.quantite) for s in stocks]
    stock_alertes = [s.en_alerte for s in stocks]
    stock_unites = [s.produit_fini.unite_mesure for s in stocks]

    # ── 3. Production par type de produit (quantités produites totales) ──
    type_totals = defaultdict(float)
    type_display = dict(ProduitFini.TYPE_CHOICES)
    for ligne in ProductionLigne.objects.select_related(
        "produit_fini", "production"
    ).filter(production__statut=ProductionRecord.STATUT_VALIDE):
        type_totals[ligne.produit_fini.get_type_produit_display()] += float(
            ligne.quantite
        )

    type_labels = list(type_totals.keys())
    type_values = list(type_totals.values())

    # ── 4. Cost vs revenue per lot ────────────────────────────────────────
    from production.utils import get_production_dashboard

    rows = get_production_dashboard()
    lot_labels = [r["lot"].designation.replace(" — ", "\n") for r in rows]
    lot_couts = [float(r["cout_total_dzd"]) for r in rows]
    lot_revenus = []
    for r in rows:
        rev = 0.0
        for ligne in ProductionLigne.objects.filter(
            production__lot=r["lot"],
            production__statut=ProductionRecord.STATUT_VALIDE,
        ).select_related("produit_fini"):
            rev += float(ligne.quantite) * float(ligne.produit_fini.prix_vente_defaut)
        lot_revenus.append(rev)

    return JsonResponse(
        {
            "monthly": {
                "labels": monthly_labels,
                "oiseaux": monthly_oiseaux,
                "poids": monthly_poids,
            },
            "stock": {
                "labels": stock_labels,
                "quantities": stock_quantities,
                "alertes": stock_alertes,
                "unites": stock_unites,
            },
            "par_type": {
                "labels": type_labels,
                "values": type_values,
            },
            "cout_revenu": {
                "labels": lot_labels,
                "couts": lot_couts,
                "revenus": lot_revenus,
            },
        }
    )
