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
    CollecteFertilisantForm,
    TraitementFertilisantForm,
)
from production.models import (
    ProduitFini,
    ProductionRecord,
    ProductionLigne,
    CollecteFertilisant,
    TraitementFertilisant,
)
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
            f"تم التحقق من سجل الإنتاج هذا ولا يمكن تعديله.",
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
            "title": "كتالوج — المنتجات النهائية",
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
                    f"تم إنشاء المنتج النهائي « {produit.designation} » بنجاح.",
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
                messages.error(request, f"خطأ أثناء الإنشاء: {exc}")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        form = ProduitFiniForm()

    return render(
        request,
        "production/produit_fini_form.html",
        {
            "form": form,
            "title": "منتج نهائي جديد",
            "action_label": "إنشاء",
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
            {"ok": False, "errors": {"__all__": ["الطريقة غير مسموح بها."]}}, status=405
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
            "title": f"المنتج النهائي — {produit.designation}",
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
                    request, f"تم تحديث المنتج النهائي « {produit.designation} »."
                )
                logger.info(
                    "ProduitFini pk=%s updated by '%s'.", produit.pk, request.user
                )
                return redirect("production:produit_fini_detail", pk=produit.pk)
            except Exception as exc:
                logger.exception("Error updating ProduitFini pk=%s: %s", pk, exc)
                messages.error(request, f"خطأ أثناء التحديث: {exc}")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        form = ProduitFiniForm(instance=produit)

    return render(
        request,
        "production/produit_fini_form.html",
        {
            "form": form,
            "produit": produit,
            "title": f"تعديل — {produit.designation}",
            "action_label": "حفظ التعديلات",
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
    state = "مفعَّل" if produit.actif else "معطَّل"
    messages.success(request, f"المنتج النهائي « {produit.designation} » {state}.")
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
            "title": "سجلات الإنتاج",
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

                    # Allocate lot costs to lines so the draft already shows
                    # estimated unit costs (will be recalculated at validation).
                    try:
                        allouer_cout_production(record)
                    except Exception as exc:
                        logger.warning(
                            "allouer_cout_production failed on create for "
                            "ProductionRecord pk=%s: %s. "
                            "cout_unitaire_estime left at 0.",
                            record.pk,
                            exc,
                        )

                messages.success(
                    request,
                    f"تم إنشاء سجل الإنتاج (مسودة) للدفعة « {record.lot.designation} » — {record.date_production}. راجع السطور ثم احقق السجل لتحديث المخزون.",
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
                messages.error(request, f"خطأ أثناء الإنشاء: {exc}")
        else:
            messages.error(
                request,
                "يرجى تصحيح الأخطاء في رأس النموذج و/أو السطور.",
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
            "title": "سجل إنتاج جديد",
            "action_label": "حفظ (مسودة)",
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
            "title": f"الإنتاج — {record.lot.designation} — {record.date_production}",
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

                    # Re-allocate costs so the draft reflects updated lines.
                    try:
                        allouer_cout_production(record)
                    except Exception as exc:
                        logger.warning(
                            "allouer_cout_production failed on edit for "
                            "ProductionRecord pk=%s: %s.",
                            record.pk,
                            exc,
                        )

                messages.success(
                    request,
                    f"تم تحديث سجل الإنتاج بتاريخ {record.date_production}.",
                )
                logger.info(
                    "ProductionRecord pk=%s updated by '%s'.", record.pk, request.user
                )
                return redirect("production:production_record_detail", pk=record.pk)

            except Exception as exc:
                logger.exception("Error updating ProductionRecord pk=%s: %s", pk, exc)
                messages.error(request, f"خطأ أثناء التحديث: {exc}")
        else:
            messages.error(
                request,
                "يرجى تصحيح الأخطاء في رأس النموذج و/أو السطور.",
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
            "title": f"تعديل — إنتاج {record.date_production}",
            "action_label": "حفظ التعديلات",
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
            "لا يمكن التحقق من سجل بدون سطور إنتاج. أضف منتجًا نهائيًا واحدًا على الأقل قبل التحقق.",
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
                    f"تعذّر التحقق: عدد الطيور المذبوحة ({record.nombre_oiseaux_abattus}) يتجاوز التعداد الحي ({effectif}). يرجى تعديل السجل.",
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
            f"تم التحقق من إنتاج {record.date_production}. تم تحديث مخزون المنتجات النهائية لـ {record.lignes.count()} منتج.",
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

        # After successful validation, check if the lot is now empty.
        lot_apres = record.lot
        if (
            lot_apres.effectif_vivant <= 0
            and lot_apres.statut == LotElevage.STATUT_OUVERT
        ):
            request.session[f"suggest_fermeture_lot_{lot_apres.pk}"] = True
            logger.info(
                "ProductionRecord pk=%s validated: effectif_vivant reached 0 "
                "for lot pk=%s. Closure suggestion queued.",
                record.pk,
                lot_apres.pk,
            )
            return redirect("elevage:lot_detail", pk=lot_apres.pk)

    except Exception as exc:
        logger.exception("Error validating ProductionRecord pk=%s: %s", pk, exc)
        messages.error(
            request,
            f"خطأ أثناء التحقق: {exc}",
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
            f"تم حذف سجل الإنتاج بتاريخ {date_ref} (الدفعة « {lot_ref} »).",
        )
        logger.info("ProductionRecord pk=%s deleted by '%s'.", pk, request.user)
    except Exception as exc:
        logger.exception("Error deleting ProductionRecord pk=%s: %s", pk, exc)
        messages.error(request, f"خطأ أثناء الحذف: {exc}")
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
            "title": "لوحة تحكم — الإنتاج",
        },
    )


# ===========================================================================
# CollecteFertilisant — List / Create / Edit / Delete
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def collecte_fertilisant_list(request):
    """
    Cross-building raw fertilizer collection list.

    Filters: ?batiment=<pk>, ?date_debut, ?date_fin, ?non_affecte=1
    """
    from intrants.models import Batiment

    qs = CollecteFertilisant.objects.select_related(
        "batiment", "traitement", "created_by"
    ).order_by("-date_collecte")

    batiment_pk = request.GET.get("batiment", "")
    if batiment_pk:
        qs = qs.filter(batiment_id=batiment_pk)

    date_debut = request.GET.get("date_debut", "")
    date_fin = request.GET.get("date_fin", "")
    if date_debut:
        qs = qs.filter(date_collecte__gte=date_debut)
    if date_fin:
        qs = qs.filter(date_collecte__lte=date_fin)

    if request.GET.get("non_affecte") == "1":
        qs = qs.filter(traitement__isnull=True)

    page = _paginate(qs, request.GET.get("page"))
    batiments = Batiment.objects.filter(
        actif=True,
        type_batiment__in=[Batiment.TYPE_POUSSINIERE, Batiment.TYPE_POULAILLER],
    ).order_by("nom")

    return render(
        request,
        "production/collecte_fertilisant_list.html",
        {
            "page": page,
            "batiments": batiments,
            "batiment_pk": batiment_pk,
            "date_debut": date_debut,
            "date_fin": date_fin,
            "title": "جمع السماد الخام",
        },
    )


@login_required(login_url=LOGIN_URL)
def collecte_fertilisant_create(request):
    """Record a raw manure collection from a building."""
    if request.method == "POST":
        form = CollecteFertilisantForm(request.POST)
        if form.is_valid():
            try:
                collecte = form.save(commit=False)
                collecte.created_by = request.user
                collecte.save()
                messages.success(
                    request,
                    f"تم تسجيل جمع {collecte.quantite_brute_kg} كغ من {collecte.batiment.nom} ({collecte.date_collecte}).",
                )
                logger.info(
                    "CollecteFertilisant pk=%s created by '%s'.",
                    collecte.pk,
                    request.user,
                )
                return redirect("production:collecte_fertilisant_list")
            except Exception as exc:
                logger.exception("Error creating CollecteFertilisant: %s", exc)
                messages.error(request, f"خطأ أثناء الإنشاء: {exc}")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        import datetime

        form = CollecteFertilisantForm(initial={"date_collecte": datetime.date.today()})

    return render(
        request,
        "production/collecte_fertilisant_form.html",
        {"form": form, "title": "جمع سماد خام جديد", "action_label": "حفظ"},
    )


@login_required(login_url=LOGIN_URL)
def collecte_fertilisant_edit(request, pk):
    """Edit an unassigned raw fertilizer collection."""
    collecte = get_object_or_404(CollecteFertilisant, pk=pk)

    if collecte.est_traitee:
        messages.error(
            request,
            "لا يمكن تعديل جمع مخصص لعملية معالجة محققة.",
        )
        return redirect("production:collecte_fertilisant_list")

    if request.method == "POST":
        form = CollecteFertilisantForm(request.POST, instance=collecte)
        if form.is_valid():
            try:
                form.save()
                messages.success(request, "تم تحديث سجل الجمع.")
                return redirect("production:collecte_fertilisant_list")
            except Exception as exc:
                logger.exception(
                    "Error updating CollecteFertilisant pk=%s: %s", pk, exc
                )
                messages.error(request, f"خطأ أثناء التحديث: {exc}")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        form = CollecteFertilisantForm(instance=collecte)

    return render(
        request,
        "production/collecte_fertilisant_form.html",
        {
            "form": form,
            "collecte": collecte,
            "title": f"تعديل — جمع {collecte.date_collecte}",
            "action_label": "حفظ التعديلات",
        },
    )


@login_required(login_url=LOGIN_URL)
@require_POST
def collecte_fertilisant_delete(request, pk):
    """Delete an unassigned raw fertilizer collection (POST-only)."""
    collecte = get_object_or_404(CollecteFertilisant, pk=pk)

    if collecte.est_traitee:
        messages.error(request, "لا يمكن حذف جمع مرتبط بعملية معالجة.")
        return redirect("production:collecte_fertilisant_list")

    try:
        ref = f"{collecte.quantite_brute_kg} كغ ({collecte.date_collecte})"
        collecte.delete()
        messages.success(request, f"تم حذف سجل الجمع: {ref}.")
        logger.info("CollecteFertilisant pk=%s deleted by '%s'.", pk, request.user)
    except Exception as exc:
        logger.exception("Error deleting CollecteFertilisant pk=%s: %s", pk, exc)
        messages.error(request, f"خطأ أثناء الحذف: {exc}")

    return redirect("production:collecte_fertilisant_list")


# ===========================================================================
# TraitementFertilisant — List / Create / Edit / Validate
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def traitement_fertilisant_list(request):
    """
    Fertilizer treatment batch list.

    Filters: ?statut=brouillon|valide
    """
    qs = (
        TraitementFertilisant.objects.select_related("produit_fini", "created_by")
        .prefetch_related("collectes")
        .order_by("-date_traitement")
    )

    statut = request.GET.get("statut", "")
    if statut in (
        TraitementFertilisant.STATUT_BROUILLON,
        TraitementFertilisant.STATUT_VALIDE,
    ):
        qs = qs.filter(statut=statut)

    page = _paginate(qs, request.GET.get("page"))

    return render(
        request,
        "production/traitement_fertilisant_list.html",
        {
            "page": page,
            "statut": statut,
            "statut_choices": TraitementFertilisant.STATUT_CHOICES,
            "title": "معالجة السماد",
        },
    )


@login_required(login_url=LOGIN_URL)
def traitement_fertilisant_create(request):
    """Create a new fertilizer treatment batch (BROUILLON)."""
    if request.method == "POST":
        form = TraitementFertilisantForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    traitement = form.save(commit=False)
                    traitement.created_by = request.user
                    traitement.save()
                    form.save_m2m()

                messages.success(
                    request,
                    f"تم إنشاء دفعة المعالجة (مسودة) بتاريخ {traitement.date_traitement}.",
                )
                logger.info(
                    "TraitementFertilisant pk=%s created by '%s'.",
                    traitement.pk,
                    request.user,
                )
                return redirect(
                    "production:traitement_fertilisant_detail", pk=traitement.pk
                )
            except Exception as exc:
                logger.exception("Error creating TraitementFertilisant: %s", exc)
                messages.error(request, f"خطأ أثناء الإنشاء: {exc}")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        import datetime

        form = TraitementFertilisantForm(
            initial={"date_traitement": datetime.date.today()}
        )

    return render(
        request,
        "production/traitement_fertilisant_form.html",
        {
            "form": form,
            "title": "دفعة معالجة سماد جديدة",
            "action_label": "حفظ (مسودة)",
        },
    )


@login_required(login_url=LOGIN_URL)
def traitement_fertilisant_detail(request, pk):
    """Detail view for one treatment batch."""
    traitement = get_object_or_404(
        TraitementFertilisant.objects.select_related(
            "produit_fini", "created_by"
        ).prefetch_related("collectes__batiment"),
        pk=pk,
    )

    from stock.models import StockMouvement

    mouvements = StockMouvement.objects.filter(
        source=StockMouvement.SOURCE_FERTILISANT,
        reference_id=traitement.pk,
    ).select_related("produit_fini")

    return render(
        request,
        "production/traitement_fertilisant_detail.html",
        {
            "traitement": traitement,
            "mouvements": mouvements,
            "title": f"معالجة سماد — {traitement.date_traitement}",
        },
    )


@login_required(login_url=LOGIN_URL)
def traitement_fertilisant_edit(request, pk):
    """Edit a BROUILLON treatment batch."""
    traitement = get_object_or_404(TraitementFertilisant, pk=pk)

    if traitement.statut == TraitementFertilisant.STATUT_VALIDE:
        messages.error(request, "لا يمكن تعديل دفعة معالجة محققة.")
        return redirect("production:traitement_fertilisant_detail", pk=pk)

    if request.method == "POST":
        form = TraitementFertilisantForm(request.POST, instance=traitement)
        if form.is_valid():
            try:
                with transaction.atomic():
                    form.save()

                messages.success(request, "تم تحديث دفعة المعالجة.")
                logger.info(
                    "TraitementFertilisant pk=%s updated by '%s'.", pk, request.user
                )
                return redirect("production:traitement_fertilisant_detail", pk=pk)
            except Exception as exc:
                logger.exception(
                    "Error updating TraitementFertilisant pk=%s: %s", pk, exc
                )
                messages.error(request, f"خطأ أثناء التحديث: {exc}")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        form = TraitementFertilisantForm(instance=traitement)

    return render(
        request,
        "production/traitement_fertilisant_form.html",
        {
            "form": form,
            "traitement": traitement,
            "title": f"تعديل — معالجة {traitement.date_traitement}",
            "action_label": "حفظ التعديلات",
        },
    )


@login_required(login_url=LOGIN_URL)
@require_POST
def traitement_fertilisant_valider(request, pk):
    """
    Transition a BROUILLON TraitementFertilisant to VALIDE (POST-only).

    On transition the post_save signal credits StockProduitFini and creates
    a StockMouvement (ENTREE / FERTILISANT).
    """
    traitement = get_object_or_404(TraitementFertilisant, pk=pk)

    if traitement.statut == TraitementFertilisant.STATUT_VALIDE:
        messages.warning(request, "هذه الدفعة محققة مسبقًا.")
        return redirect("production:traitement_fertilisant_detail", pk=pk)

    if not traitement.quantite_obtenue_kg or traitement.quantite_obtenue_kg <= 0:
        messages.error(
            request,
            "يجب تحديد الكمية النهائية المتحصل عليها قبل التحقق.",
        )
        return redirect("production:traitement_fertilisant_detail", pk=pk)

    try:
        with transaction.atomic():
            traitement.statut = TraitementFertilisant.STATUT_VALIDE
            traitement.save()  # signal fires here

        messages.success(
            request,
            f"تم التحقق من دفعة المعالجة ({traitement.quantite_obtenue_kg} كغ). تم تحديث مخزون السماد.",
        )
        logger.info("TraitementFertilisant pk=%s validated by '%s'.", pk, request.user)
    except Exception as exc:
        logger.exception("Error validating TraitementFertilisant pk=%s: %s", pk, exc)
        messages.error(request, f"خطأ أثناء التحقق: {exc}")

    return redirect("production:traitement_fertilisant_detail", pk=pk)


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
        try:
            cmp = float(p.stock.cout_moyen_production)
        except Exception:
            cmp = 0.0
        return JsonResponse(
            {
                "quantite": float(stock),
                "unite_mesure": p.unite_mesure,
                "prix_vente_defaut": float(p.prix_vente_defaut),
                "cout_moyen_production": cmp,
            }
        )
    except ProduitFini.DoesNotExist:
        return JsonResponse({"error": "غير موجود"}, status=404)


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
