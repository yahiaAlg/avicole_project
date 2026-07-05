"""
intrants/views.py

Function-based views for master-data management:
  - CategorieIntrant  : list, create, edit, toggle active
  - TypeFournisseur   : list, create, edit, toggle active
  - Fournisseur       : list, create, edit, toggle active, detail
  - Batiment          : list, create, edit, toggle active
  - Intrant           : list, create, edit, toggle active, detail

All write operations use Post-Redirect-Get.
Destructive state changes (toggle active) are POST-only.
"""

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core.views import (
    branche_object_or_404,
    get_active_branche,
    require_branche_context,
)
from intrants.forms import (
    BatimentForm,
    CategorieIntrantForm,
    CategorieQualiteForm,
    FournisseurForm,
    IntrantForm,
    TypeFournisseurForm,
)
from intrants.models import (
    Batiment,
    CategorieIntrant,
    CategorieQualite,
    Fournisseur,
    Intrant,
    TypeFournisseur,
)

logger = logging.getLogger(__name__)

LOGIN_URL = "core:login"
PER_PAGE = 25


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _paginate(qs, page_number, per_page=PER_PAGE):
    paginator = Paginator(qs, per_page)
    try:
        return paginator.page(page_number)
    except PageNotAnInteger:
        return paginator.page(1)
    except EmptyPage:
        return paginator.page(paginator.num_pages)


# ===========================================================================
# CategorieIntrant
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def categorie_intrant_list(request):
    """List all intrant categories, ordered by display order then label."""
    categories = CategorieIntrant.objects.all().order_by("ordre", "libelle")
    return render(
        request,
        "intrants/categorie_intrant_list.html",
        {
            "categories": categories,
            "title": "فئات المدخلات",
        },
    )


@login_required(login_url=LOGIN_URL)
def categorie_intrant_create(request):
    if request.method == "POST":
        form = CategorieIntrantForm(request.POST)
        if form.is_valid():
            cat = form.save()
            messages.success(request, f"تم إنشاء الفئة « {cat.libelle} » بنجاح.")
            logger.info("CategorieIntrant pk=%s created by '%s'.", cat.pk, request.user)
            return redirect("intrants:categorie_intrant_list")
        messages.error(request, "يرجى تصحيح الأخطاء.")
    else:
        form = CategorieIntrantForm()
    return render(
        request,
        "intrants/categorie_intrant_form.html",
        {
            "form": form,
            "title": "فئة مدخلات جديدة",
            "action_label": "إنشاء",
        },
    )


@login_required(login_url=LOGIN_URL)
def categorie_intrant_edit(request, pk):
    cat = get_object_or_404(CategorieIntrant, pk=pk)
    if request.method == "POST":
        form = CategorieIntrantForm(request.POST, instance=cat)
        if form.is_valid():
            form.save()
            messages.success(request, f"تم تحديث الفئة « {cat.libelle} ».")
            return redirect("intrants:categorie_intrant_list")
        messages.error(request, "يرجى تصحيح الأخطاء.")
    else:
        form = CategorieIntrantForm(instance=cat)
    return render(
        request,
        "intrants/categorie_intrant_form.html",
        {
            "form": form,
            "object": cat,
            "title": f"تعديل — {cat.libelle}",
            "action_label": "حفظ",
        },
    )


@login_required(login_url=LOGIN_URL)
@require_POST
def categorie_intrant_toggle_active(request, pk):
    """Toggle actif/inactif. POST-only."""
    cat = get_object_or_404(CategorieIntrant, pk=pk)
    cat.actif = not cat.actif
    cat.save(update_fields=["actif"])
    state = "مفعَّلة" if cat.actif else "معطَّلة"
    messages.success(request, f"الفئة « {cat.libelle} » {state}.")
    return redirect("intrants:categorie_intrant_list")


# ===========================================================================
# CategorieQualite — quality-grading brackets (oiseaux / oeufs)
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def categorie_qualite_list(request):
    """
    List all quality-grading brackets, grouped by type_pesee (oiseaux/oeufs).

    Filters:
      ?type_pesee=oiseaux|oeufs
    """
    qs = CategorieQualite.objects.all().order_by("type_pesee", "ordre")

    type_pesee = request.GET.get("type_pesee", "")
    if type_pesee in (CategorieQualite.TYPE_OISEAUX, CategorieQualite.TYPE_OEUFS):
        qs = qs.filter(type_pesee=type_pesee)

    return render(
        request,
        "intrants/categorie_qualite_list.html",
        {
            "categories": qs,
            "type_pesee": type_pesee,
            "type_choices": CategorieQualite.TYPE_CHOICES,
            "title": "فئات الجودة",
        },
    )


@login_required(login_url=LOGIN_URL)
def categorie_qualite_create(request):
    if request.method == "POST":
        form = CategorieQualiteForm(request.POST)
        if form.is_valid():
            cat = form.save()
            messages.success(request, f"تم إنشاء فئة الجودة « {cat.libelle} » بنجاح.")
            logger.info("CategorieQualite pk=%s created by '%s'.", cat.pk, request.user)
            return redirect("intrants:categorie_qualite_list")
        messages.error(request, "يرجى تصحيح الأخطاء.")
    else:
        form = CategorieQualiteForm()
    return render(
        request,
        "intrants/categorie_qualite_form.html",
        {
            "form": form,
            "title": "فئة جودة جديدة",
            "action_label": "إنشاء",
        },
    )


@login_required(login_url=LOGIN_URL)
def categorie_qualite_edit(request, pk):
    cat = get_object_or_404(CategorieQualite, pk=pk)
    if request.method == "POST":
        form = CategorieQualiteForm(request.POST, instance=cat)
        if form.is_valid():
            form.save()
            messages.success(request, f"تم تحديث فئة الجودة « {cat.libelle} ».")
            return redirect("intrants:categorie_qualite_list")
        messages.error(request, "يرجى تصحيح الأخطاء.")
    else:
        form = CategorieQualiteForm(instance=cat)
    return render(
        request,
        "intrants/categorie_qualite_form.html",
        {
            "form": form,
            "object": cat,
            "title": f"تعديل — {cat.libelle}",
            "action_label": "حفظ",
        },
    )


@login_required(login_url=LOGIN_URL)
@require_POST
def categorie_qualite_toggle_active(request, pk):
    """Toggle actif/inactif. POST-only."""
    cat = get_object_or_404(CategorieQualite, pk=pk)
    cat.actif = not cat.actif
    cat.save(update_fields=["actif"])
    state = "مفعَّلة" if cat.actif else "معطَّلة"
    messages.success(request, f"فئة الجودة « {cat.libelle} » {state}.")
    return redirect("intrants:categorie_qualite_list")


# ===========================================================================
# TypeFournisseur
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def type_fournisseur_list(request):
    types = TypeFournisseur.objects.all().order_by("ordre", "libelle")
    return render(
        request,
        "intrants/type_fournisseur_list.html",
        {
            "types": types,
            "title": "أنواع الموردين",
        },
    )


@login_required(login_url=LOGIN_URL)
def type_fournisseur_create(request):
    if request.method == "POST":
        form = TypeFournisseurForm(request.POST)
        if form.is_valid():
            obj = form.save()
            messages.success(request, f"تم إنشاء النوع « {obj.libelle} » بنجاح.")
            return redirect("intrants:type_fournisseur_list")
        messages.error(request, "يرجى تصحيح الأخطاء.")
    else:
        form = TypeFournisseurForm()
    return render(
        request,
        "intrants/type_fournisseur_form.html",
        {
            "form": form,
            "title": "نوع مورد جديد",
            "action_label": "إنشاء",
        },
    )


@login_required(login_url=LOGIN_URL)
def type_fournisseur_edit(request, pk):
    obj = get_object_or_404(TypeFournisseur, pk=pk)
    if request.method == "POST":
        form = TypeFournisseurForm(request.POST, instance=obj)
        if form.is_valid():
            form.save()
            messages.success(request, f"تم تحديث النوع « {obj.libelle} ».")
            return redirect("intrants:type_fournisseur_list")
        messages.error(request, "يرجى تصحيح الأخطاء.")
    else:
        form = TypeFournisseurForm(instance=obj)
    return render(
        request,
        "intrants/type_fournisseur_form.html",
        {
            "form": form,
            "object": obj,
            "title": f"تعديل — {obj.libelle}",
            "action_label": "حفظ",
        },
    )


@login_required(login_url=LOGIN_URL)
@require_POST
def type_fournisseur_toggle_active(request, pk):
    obj = get_object_or_404(TypeFournisseur, pk=pk)
    obj.actif = not obj.actif
    obj.save(update_fields=["actif"])
    state = "مفعَّل" if obj.actif else "معطَّل"
    messages.success(request, f"النوع « {obj.libelle} » {state}.")
    return redirect("intrants:type_fournisseur_list")


# ===========================================================================
# Fournisseur
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def fournisseur_list(request):
    """
    Supplier list with search (name, city) and actif/inactif filter.
    """
    qs = Fournisseur.objects.select_related("type_principal").order_by("nom")

    # Filter: actif only by default; pass ?afficher=tous to see all.
    afficher = request.GET.get("afficher", "actifs")
    if afficher == "actifs":
        qs = qs.filter(actif=True)
    elif afficher == "inactifs":
        qs = qs.filter(actif=False)
    # else: tous

    # Search
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(
            Q(nom__icontains=q) | Q(wilaya__icontains=q) | Q(telephone__icontains=q)
        )

    page = _paginate(qs, request.GET.get("page"))
    return render(
        request,
        "intrants/fournisseur_list.html",
        {
            "page": page,
            "q": q,
            "afficher": afficher,
            "title": "الموردين",
        },
    )


@login_required(login_url=LOGIN_URL)
def fournisseur_detail(request, pk):
    """
    Supplier detail view with financial summary (dette, acompte, open invoices).

    Fournisseur stays global (BR-BRA-06), but its BLs/factures/règlements
    are branch-scoped (§3.5.3 ¶4): Vue par Branche shows this branch's
    figures only; Vue Globale sums across every branch the supplier has
    ever transacted with.
    """
    fournisseur = get_object_or_404(Fournisseur, pk=pk)
    branche = get_active_branche(request)

    # Financial snapshot via achats utils (lazy import — achats depends on intrants).
    try:
        from achats.utils import get_fournisseur_solde

        solde = get_fournisseur_solde(fournisseur, branche=branche)
    except Exception:
        solde = {
            "dette_globale": 0,
            "acompte_disponible": 0,
            "factures_ouvertes": [],
            "total_reglements": 0,
            "nb_factures_retard": 0,
        }

    # Recent BLs (scoped to the active branche, Vue Globale shows all)
    try:
        from achats.models import BLFournisseur

        bls_recents_qs = BLFournisseur.objects.filter(fournisseur=fournisseur)
        if branche is not None:
            bls_recents_qs = bls_recents_qs.filter(branche=branche)
        bls_recents = bls_recents_qs.order_by("-date_bl")[:10]
    except Exception:
        bls_recents = []

    # Linked intrants
    intrants_lies = fournisseur.intrants.filter(actif=True).order_by("designation")

    return render(
        request,
        "intrants/fournisseur_detail.html",
        {
            "fournisseur": fournisseur,
            "solde": solde,
            "bls_recents": bls_recents,
            "intrants_lies": intrants_lies,
            "active_branche": branche,
            "title": fournisseur.nom,
        },
    )


@login_required(login_url=LOGIN_URL)
def fournisseur_create(request):
    if request.method == "POST":
        form = FournisseurForm(request.POST)
        if form.is_valid():
            obj = form.save()
            messages.success(request, f"تم إنشاء المورد « {obj.nom} » بنجاح.")
            logger.info("Fournisseur pk=%s created by '%s'.", obj.pk, request.user)
            return redirect("intrants:fournisseur_detail", pk=obj.pk)
        messages.error(request, "يرجى تصحيح الأخطاء.")
    else:
        form = FournisseurForm()
    return render(
        request,
        "intrants/fournisseur_form.html",
        {
            "form": form,
            "title": "Nouveau fournisseur",
            "action_label": "إنشاء",
        },
    )


@login_required(login_url=LOGIN_URL)
def fournisseur_edit(request, pk):
    fournisseur = get_object_or_404(Fournisseur, pk=pk)
    if request.method == "POST":
        form = FournisseurForm(request.POST, instance=fournisseur)
        if form.is_valid():
            form.save()
            messages.success(request, f"تم تحديث المورد « {fournisseur.nom} ».")
            return redirect("intrants:fournisseur_detail", pk=pk)
        messages.error(request, "يرجى تصحيح الأخطاء.")
    else:
        form = FournisseurForm(instance=fournisseur)
    return render(
        request,
        "intrants/fournisseur_form.html",
        {
            "form": form,
            "object": fournisseur,
            "title": f"تعديل — {fournisseur.nom}",
            "action_label": "حفظ",
        },
    )


@login_required(login_url=LOGIN_URL)
@require_POST
def fournisseur_toggle_active(request, pk):
    """Deactivate / reactivate a supplier. Does NOT delete."""
    fournisseur = get_object_or_404(Fournisseur, pk=pk)
    fournisseur.actif = not fournisseur.actif
    fournisseur.save(update_fields=["actif", "updated_at"])
    state = "مفعَّل" if fournisseur.actif else "معطَّل"
    messages.success(request, f"المورد « {fournisseur.nom} » {state}.")
    logger.info(
        "Fournisseur pk=%s set actif=%s by '%s'.",
        pk,
        fournisseur.actif,
        request.user,
    )
    return redirect("intrants:fournisseur_list")


# ===========================================================================
# Batiment
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def batiment_list(request):
    """
    List buildings. Vue par Branche: only the active branche's buildings
    (exactly what a chef de branche sees, BR-BRA-01/02). Vue Globale:
    every building across all branches, with the branche shown per row.
    """
    branche = get_active_branche(request)
    batiments = Batiment.objects.select_related("branche").order_by(
        "branche__nom", "nom"
    )
    if branche is not None:
        batiments = batiments.filter(branche=branche)
    return render(
        request,
        "intrants/batiment_list.html",
        {
            "batiments": batiments,
            "active_branche": branche,
            "title": "المباني",
        },
    )


@login_required(login_url=LOGIN_URL)
@require_branche_context
def batiment_create(request):
    """Create a building — locked to the active branche (BR-BRA-01/02)."""
    branche = get_active_branche(request)
    if request.method == "POST":
        form = BatimentForm(request.POST, branche=branche)
        if form.is_valid():
            obj = form.save()
            messages.success(request, f"تم إنشاء المبنى « {obj.nom} » بنجاح.")
            return redirect("intrants:batiment_list")
        messages.error(request, "يرجى تصحيح الأخطاء.")
    else:
        form = BatimentForm(branche=branche)
    return render(
        request,
        "intrants/batiment_form.html",
        {
            "form": form,
            "title": "مبنى جديد",
            "action_label": "إنشاء",
        },
    )


@login_required(login_url=LOGIN_URL)
def batiment_edit(request, pk):
    """Edit a building. A chef de branche/opérateur can only reach their
    own branche's buildings (BR-BRA-02, enforced via branche_object_or_404)."""
    batiment = branche_object_or_404(request, Batiment, pk=pk)
    branche = get_active_branche(request)
    if request.method == "POST":
        form = BatimentForm(request.POST, instance=batiment, branche=branche)
        if form.is_valid():
            form.save()
            messages.success(request, f"تم تحديث المبنى « {batiment.nom} ».")
            return redirect("intrants:batiment_list")
        messages.error(request, "يرجى تصحيح الأخطاء.")
    else:
        form = BatimentForm(instance=batiment, branche=branche)
    return render(
        request,
        "intrants/batiment_form.html",
        {
            "form": form,
            "object": batiment,
            "title": f"تعديل — {batiment.nom}",
            "action_label": "حفظ",
        },
    )


@login_required(login_url=LOGIN_URL)
@require_POST
def batiment_toggle_active(request, pk):
    batiment = branche_object_or_404(request, Batiment, pk=pk)
    batiment.actif = not batiment.actif
    batiment.save(update_fields=["actif"])
    state = "مفعَّل" if batiment.actif else "معطَّل"
    messages.success(request, f"المبنى « {batiment.nom} » {state}.")
    return redirect("intrants:batiment_list")


# ===========================================================================
# Intrant
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def intrant_list(request):
    """
    Intrant catalogue with search (designation, category) and alert filter.

    v1.4 (BR-BRA-07): StockIntrant is now one row per (branche, intrant),
    so the alert filter reads `en_alerte(branche)` for the active branche
    (Vue par Branche) or the aggregated company-wide balance in Vue Globale.
    """
    branche = get_active_branche(request)
    qs = (
        Intrant.objects.select_related("categorie")
        .prefetch_related("stocks", "fournisseurs")
        .order_by("categorie__libelle", "designation")
    )

    # Active / all filter
    afficher = request.GET.get("afficher", "actifs")
    if afficher == "actifs":
        qs = qs.filter(actif=True)
    elif afficher == "inactifs":
        qs = qs.filter(actif=False)

    # Category filter
    categorie_pk = request.GET.get("categorie")
    if categorie_pk:
        qs = qs.filter(categorie_id=categorie_pk)

    # Search
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(designation__icontains=q) | Q(categorie__libelle__icontains=q))

    # Alert filter — en_alerte is now a method taking the active branche
    # (BR-BRA-07); done in Python since it crosses model boundaries.
    en_alerte_filter = request.GET.get("alerte") == "1"
    if en_alerte_filter:
        qs = [i for i in qs if i.en_alerte(branche)]

    page = _paginate(qs, request.GET.get("page"))

    # Annotate each row with its branch-scoped balance/alert (BR-BRA-07):
    # Vue par Branche shows that branch's figures; Vue Globale (branche=None)
    # shows the aggregated total, matching intrant_detail's behaviour.
    for item in page.object_list:
        item.qte_affichee = item.quantite_en_stock(branche)
        item.alerte_affichee = item.en_alerte(branche)

    categories = CategorieIntrant.objects.filter(actif=True).order_by(
        "ordre", "libelle"
    )

    return render(
        request,
        "intrants/intrant_list.html",
        {
            "page": page,
            "q": q,
            "afficher": afficher,
            "categorie_pk": categorie_pk,
            "categories": categories,
            "en_alerte_filter": en_alerte_filter,
            "active_branche": branche,
            "title": "كتالوج المدخلات",
        },
    )


@login_required(login_url=LOGIN_URL)
def intrant_detail(request, pk):
    """
    Intrant detail: catalogue info + current stock balance + recent movements.

    v1.4 (BR-BRA-07): StockIntrant is now one row per (branche, intrant).
    Vue par Branche shows that branch's row + movements; Vue Globale shows
    every branch's row side by side (`stocks_par_branche`) plus the
    aggregated total.
    """
    branche = get_active_branche(request)
    intrant = get_object_or_404(
        Intrant.objects.select_related("categorie").prefetch_related("stocks"),
        pk=pk,
    )

    # Stock balance(s)
    stock = None
    stocks_par_branche = []
    if branche is not None:
        stock = intrant.stocks.filter(branche=branche).first()
    else:
        stocks_par_branche = list(
            intrant.stocks.select_related("branche").order_by("branche__nom")
        )

    # Recent stock movements (last 20, scoped to the active branche)
    try:
        from stock.models import StockMouvement

        mouvements_qs = StockMouvement.objects.filter(intrant=intrant)
        if branche is not None:
            mouvements_qs = mouvements_qs.filter(branche=branche)
        mouvements = mouvements_qs.order_by("-date_mouvement", "-created_at")[:20]
    except Exception:
        mouvements = []

    fournisseurs = intrant.fournisseurs.filter(actif=True).order_by("nom")

    return render(
        request,
        "intrants/intrant_detail.html",
        {
            "intrant": intrant,
            "stock": stock,
            "stocks_par_branche": stocks_par_branche,
            "quantite_en_stock": intrant.quantite_en_stock(branche),
            "en_alerte": intrant.en_alerte(branche),
            "active_branche": branche,
            "mouvements": mouvements,
            "fournisseurs": fournisseurs,
            "title": intrant.designation,
        },
    )


@login_required(login_url=LOGIN_URL)
def intrant_create(request):
    if request.method == "POST":
        form = IntrantForm(request.POST)
        if form.is_valid():
            obj = form.save()
            messages.success(
                request,
                f"تم إنشاء المدخل « {obj.designation} » بنجاح. تم تهيئة بطاقة المخزون تلقائيًا.",
            )
            logger.info("Intrant pk=%s created by '%s'.", obj.pk, request.user)
            return redirect("intrants:intrant_detail", pk=obj.pk)
        messages.error(request, "يرجى تصحيح الأخطاء.")
    else:
        form = IntrantForm()
    return render(
        request,
        "intrants/intrant_form.html",
        {
            "form": form,
            "title": "مدخل جديد",
            "action_label": "إنشاء",
        },
    )


@login_required(login_url=LOGIN_URL)
def intrant_edit(request, pk):
    intrant = get_object_or_404(Intrant, pk=pk)
    if request.method == "POST":
        form = IntrantForm(request.POST, instance=intrant)
        if form.is_valid():
            form.save()
            messages.success(request, f"تم تحديث المدخل « {intrant.designation} ».")
            logger.info("Intrant pk=%s edited by '%s'.", pk, request.user)
            return redirect("intrants:intrant_detail", pk=pk)
        messages.error(request, "يرجى تصحيح الأخطاء.")
    else:
        form = IntrantForm(instance=intrant)
    return render(
        request,
        "intrants/intrant_form.html",
        {
            "form": form,
            "object": intrant,
            "title": f"تعديل — {intrant.designation}",
            "action_label": "حفظ",
        },
    )


@login_required(login_url=LOGIN_URL)
@require_POST
def intrant_toggle_active(request, pk):
    """Deactivate / reactivate an intrant. Does NOT delete."""
    intrant = get_object_or_404(Intrant, pk=pk)
    intrant.actif = not intrant.actif
    intrant.save(update_fields=["actif", "updated_at"])
    state = "مفعَّل" if intrant.actif else "معطَّل"
    messages.success(request, f"المدخل « {intrant.designation} » {state}.")
    logger.info("Intrant pk=%s set actif=%s by '%s'.", pk, intrant.actif, request.user)
    return redirect("intrants:intrant_list")


# ===========================================================================
# AJAX helpers
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def intrant_stock_json(request, pk):
    """
    Return the current stock balance and PMP for one intrant as JSON,
    scoped to the request's active branche (BR-BRA-07 — StockIntrant is now
    one row per (branche, intrant)). Used by BL Fournisseur / Consommation
    forms to display live stock for the branch the document is being
    created in. Spec §9 requires this for the intrant selection widget.
    """
    from django.http import JsonResponse

    branche = get_active_branche(request)
    intrant = get_object_or_404(Intrant, pk=pk)
    try:
        stock = intrant.stocks.get(branche=branche)
        data = {
            "quantite": float(stock.quantite),
            "prix_moyen_pondere": float(stock.prix_moyen_pondere),
            "unite_mesure": intrant.unite_mesure.libelle,
            "en_alerte": stock.en_alerte,
            "seuil_alerte": float(intrant.seuil_alerte),
        }
    except Exception:
        data = {
            "quantite": 0,
            "prix_moyen_pondere": 0,
            "unite_mesure": intrant.unite_mesure.libelle,
            "en_alerte": True,
            "seuil_alerte": float(intrant.seuil_alerte),
        }
    return JsonResponse(data)


@login_required(login_url=LOGIN_URL)
def fournisseur_intrants_json(request, pk):
    """
    Return the list of active intrants linked to a fournisseur.
    Used in BL Fournisseur line-item form to filter suggestions.
    """
    from django.http import JsonResponse

    fournisseur = get_object_or_404(Fournisseur, pk=pk)
    intrants = list(
        fournisseur.intrants.filter(actif=True)
        .values(
            "id", "designation", "unite_mesure__libelle", "categorie__libelle"
        )
        .order_by("designation")
    )
    return JsonResponse({"intrants": intrants})


@login_required(login_url=LOGIN_URL)
@require_POST
def intrant_create_ajax(request):
    """
    AJAX endpoint: create a new Intrant and return JSON.
    Used by the BL Fournisseur form modal to add a missing intrant on-the-fly
    and associate it with the current supplier.

    POST params:
        designation    — required
        categorie      — required (pk)
        unite_mesure   — required
        seuil_alerte   — optional, defaults to 0
        fournisseur_pk — optional, supplier pk to associate

    Returns:
        200 {"ok": true,  "pk": ..., "designation": ..., "label": ..., "unite_mesure": ...}
        400 {"ok": false, "errors": {...}}
    """
    from django.http import JsonResponse

    form = IntrantForm(request.POST)
    if form.is_valid():
        intrant = form.save()

        # Associate supplier if provided
        fournisseur_pk = request.POST.get("fournisseur_pk")
        if fournisseur_pk:
            try:
                fournisseur = Fournisseur.objects.get(pk=fournisseur_pk, actif=True)
                intrant.fournisseurs.add(fournisseur)
            except Fournisseur.DoesNotExist:
                pass

        logger.info(
            "Intrant pk=%s created via AJAX modal by '%s'.", intrant.pk, request.user
        )
        return JsonResponse(
            {
                "ok": True,
                "pk": intrant.pk,
                "designation": intrant.designation,
                "label": str(intrant),
                "unite_mesure": intrant.unite_mesure.libelle,
                "categorie": intrant.categorie.libelle,
            }
        )

    # Return field-level errors
    errors = {field: list(errs) for field, errs in form.errors.items()}
    return JsonResponse({"ok": False, "errors": errors}, status=400)
