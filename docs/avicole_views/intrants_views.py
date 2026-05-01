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

from intrants.forms import (
    BatimentForm,
    CategorieIntrantForm,
    FournisseurForm,
    IntrantForm,
    TypeFournisseurForm,
)
from intrants.models import (
    Batiment,
    CategorieIntrant,
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
    return render(request, "intrants/categorie_intrant_list.html", {
        "categories": categories,
        "title": "Catégories d'intrants",
    })


@login_required(login_url=LOGIN_URL)
def categorie_intrant_create(request):
    if request.method == "POST":
        form = CategorieIntrantForm(request.POST)
        if form.is_valid():
            cat = form.save()
            messages.success(request, f"Catégorie « {cat.libelle} » créée avec succès.")
            logger.info("CategorieIntrant pk=%s created by '%s'.", cat.pk, request.user)
            return redirect("intrants:categorie_intrant_list")
        messages.error(request, "Veuillez corriger les erreurs.")
    else:
        form = CategorieIntrantForm()
    return render(request, "intrants/categorie_intrant_form.html", {
        "form": form,
        "title": "Nouvelle catégorie d'intrant",
        "action_label": "Créer",
    })


@login_required(login_url=LOGIN_URL)
def categorie_intrant_edit(request, pk):
    cat = get_object_or_404(CategorieIntrant, pk=pk)
    if request.method == "POST":
        form = CategorieIntrantForm(request.POST, instance=cat)
        if form.is_valid():
            form.save()
            messages.success(request, f"Catégorie « {cat.libelle} » mise à jour.")
            return redirect("intrants:categorie_intrant_list")
        messages.error(request, "Veuillez corriger les erreurs.")
    else:
        form = CategorieIntrantForm(instance=cat)
    return render(request, "intrants/categorie_intrant_form.html", {
        "form": form,
        "object": cat,
        "title": f"Modifier — {cat.libelle}",
        "action_label": "Enregistrer",
    })


@login_required(login_url=LOGIN_URL)
@require_POST
def categorie_intrant_toggle_active(request, pk):
    """Toggle actif/inactif. POST-only."""
    cat = get_object_or_404(CategorieIntrant, pk=pk)
    cat.actif = not cat.actif
    cat.save(update_fields=["actif"])
    state = "activée" if cat.actif else "désactivée"
    messages.success(request, f"Catégorie « {cat.libelle} » {state}.")
    return redirect("intrants:categorie_intrant_list")


# ===========================================================================
# TypeFournisseur
# ===========================================================================

@login_required(login_url=LOGIN_URL)
def type_fournisseur_list(request):
    types = TypeFournisseur.objects.all().order_by("ordre", "libelle")
    return render(request, "intrants/type_fournisseur_list.html", {
        "types": types,
        "title": "Types de fournisseurs",
    })


@login_required(login_url=LOGIN_URL)
def type_fournisseur_create(request):
    if request.method == "POST":
        form = TypeFournisseurForm(request.POST)
        if form.is_valid():
            obj = form.save()
            messages.success(request, f"Type « {obj.libelle} » créé avec succès.")
            return redirect("intrants:type_fournisseur_list")
        messages.error(request, "Veuillez corriger les erreurs.")
    else:
        form = TypeFournisseurForm()
    return render(request, "intrants/type_fournisseur_form.html", {
        "form": form,
        "title": "Nouveau type de fournisseur",
        "action_label": "Créer",
    })


@login_required(login_url=LOGIN_URL)
def type_fournisseur_edit(request, pk):
    obj = get_object_or_404(TypeFournisseur, pk=pk)
    if request.method == "POST":
        form = TypeFournisseurForm(request.POST, instance=obj)
        if form.is_valid():
            form.save()
            messages.success(request, f"Type « {obj.libelle} » mis à jour.")
            return redirect("intrants:type_fournisseur_list")
        messages.error(request, "Veuillez corriger les erreurs.")
    else:
        form = TypeFournisseurForm(instance=obj)
    return render(request, "intrants/type_fournisseur_form.html", {
        "form": form,
        "object": obj,
        "title": f"Modifier — {obj.libelle}",
        "action_label": "Enregistrer",
    })


@login_required(login_url=LOGIN_URL)
@require_POST
def type_fournisseur_toggle_active(request, pk):
    obj = get_object_or_404(TypeFournisseur, pk=pk)
    obj.actif = not obj.actif
    obj.save(update_fields=["actif"])
    state = "activé" if obj.actif else "désactivé"
    messages.success(request, f"Type « {obj.libelle} » {state}.")
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
    return render(request, "intrants/fournisseur_list.html", {
        "page": page,
        "q": q,
        "afficher": afficher,
        "title": "Fournisseurs",
    })


@login_required(login_url=LOGIN_URL)
def fournisseur_detail(request, pk):
    """
    Supplier detail view with financial summary (dette, acompte, open invoices).
    """
    fournisseur = get_object_or_404(Fournisseur, pk=pk)

    # Financial snapshot via achats utils (lazy import — achats depends on intrants).
    try:
        from achats.utils import get_fournisseur_solde
        solde = get_fournisseur_solde(fournisseur)
    except Exception:
        solde = {
            "dette_globale": 0,
            "acompte_disponible": 0,
            "factures_ouvertes": [],
            "total_reglements": 0,
            "nb_factures_retard": 0,
        }

    # Recent BLs
    try:
        from achats.models import BLFournisseur
        bls_recents = (
            BLFournisseur.objects.filter(fournisseur=fournisseur)
            .order_by("-date_bl")[:10]
        )
    except Exception:
        bls_recents = []

    # Linked intrants
    intrants_lies = fournisseur.intrants.filter(actif=True).order_by("designation")

    return render(request, "intrants/fournisseur_detail.html", {
        "fournisseur": fournisseur,
        "solde": solde,
        "bls_recents": bls_recents,
        "intrants_lies": intrants_lies,
        "title": fournisseur.nom,
    })


@login_required(login_url=LOGIN_URL)
def fournisseur_create(request):
    if request.method == "POST":
        form = FournisseurForm(request.POST)
        if form.is_valid():
            obj = form.save()
            messages.success(request, f"Fournisseur « {obj.nom} » créé avec succès.")
            logger.info("Fournisseur pk=%s created by '%s'.", obj.pk, request.user)
            return redirect("intrants:fournisseur_detail", pk=obj.pk)
        messages.error(request, "Veuillez corriger les erreurs.")
    else:
        form = FournisseurForm()
    return render(request, "intrants/fournisseur_form.html", {
        "form": form,
        "title": "Nouveau fournisseur",
        "action_label": "Créer",
    })


@login_required(login_url=LOGIN_URL)
def fournisseur_edit(request, pk):
    fournisseur = get_object_or_404(Fournisseur, pk=pk)
    if request.method == "POST":
        form = FournisseurForm(request.POST, instance=fournisseur)
        if form.is_valid():
            form.save()
            messages.success(request, f"Fournisseur « {fournisseur.nom} » mis à jour.")
            return redirect("intrants:fournisseur_detail", pk=pk)
        messages.error(request, "Veuillez corriger les erreurs.")
    else:
        form = FournisseurForm(instance=fournisseur)
    return render(request, "intrants/fournisseur_form.html", {
        "form": form,
        "object": fournisseur,
        "title": f"Modifier — {fournisseur.nom}",
        "action_label": "Enregistrer",
    })


@login_required(login_url=LOGIN_URL)
@require_POST
def fournisseur_toggle_active(request, pk):
    """Deactivate / reactivate a supplier. Does NOT delete."""
    fournisseur = get_object_or_404(Fournisseur, pk=pk)
    fournisseur.actif = not fournisseur.actif
    fournisseur.save(update_fields=["actif", "updated_at"])
    state = "activé" if fournisseur.actif else "désactivé"
    messages.success(request, f"Fournisseur « {fournisseur.nom} » {state}.")
    logger.info(
        "Fournisseur pk=%s set actif=%s by '%s'.",
        pk, fournisseur.actif, request.user,
    )
    return redirect("intrants:fournisseur_list")


# ===========================================================================
# Batiment
# ===========================================================================

@login_required(login_url=LOGIN_URL)
def batiment_list(request):
    batiments = Batiment.objects.order_by("nom")
    return render(request, "intrants/batiment_list.html", {
        "batiments": batiments,
        "title": "Bâtiments",
    })


@login_required(login_url=LOGIN_URL)
def batiment_create(request):
    if request.method == "POST":
        form = BatimentForm(request.POST)
        if form.is_valid():
            obj = form.save()
            messages.success(request, f"Bâtiment « {obj.nom} » créé avec succès.")
            return redirect("intrants:batiment_list")
        messages.error(request, "Veuillez corriger les erreurs.")
    else:
        form = BatimentForm()
    return render(request, "intrants/batiment_form.html", {
        "form": form,
        "title": "Nouveau bâtiment",
        "action_label": "Créer",
    })


@login_required(login_url=LOGIN_URL)
def batiment_edit(request, pk):
    batiment = get_object_or_404(Batiment, pk=pk)
    if request.method == "POST":
        form = BatimentForm(request.POST, instance=batiment)
        if form.is_valid():
            form.save()
            messages.success(request, f"Bâtiment « {batiment.nom} » mis à jour.")
            return redirect("intrants:batiment_list")
        messages.error(request, "Veuillez corriger les erreurs.")
    else:
        form = BatimentForm(instance=batiment)
    return render(request, "intrants/batiment_form.html", {
        "form": form,
        "object": batiment,
        "title": f"Modifier — {batiment.nom}",
        "action_label": "Enregistrer",
    })


@login_required(login_url=LOGIN_URL)
@require_POST
def batiment_toggle_active(request, pk):
    batiment = get_object_or_404(Batiment, pk=pk)
    batiment.actif = not batiment.actif
    batiment.save(update_fields=["actif"])
    state = "activé" if batiment.actif else "désactivé"
    messages.success(request, f"Bâtiment « {batiment.nom} » {state}.")
    return redirect("intrants:batiment_list")


# ===========================================================================
# Intrant
# ===========================================================================

@login_required(login_url=LOGIN_URL)
def intrant_list(request):
    """
    Intrant catalogue with search (designation, category) and alert filter.
    """
    qs = (
        Intrant.objects.select_related("categorie", "stock")
        .prefetch_related("fournisseurs")
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
        qs = qs.filter(
            Q(designation__icontains=q) | Q(categorie__libelle__icontains=q)
        )

    # Alert filter — quantite <= seuil_alerte (cross-model comparison; done in Python)
    en_alerte_filter = request.GET.get("alerte") == "1"
    if en_alerte_filter:
        qs = [i for i in qs if i.en_alerte]

    page = _paginate(qs, request.GET.get("page"))

    categories = CategorieIntrant.objects.filter(actif=True).order_by("ordre", "libelle")

    return render(request, "intrants/intrant_list.html", {
        "page": page,
        "q": q,
        "afficher": afficher,
        "categorie_pk": categorie_pk,
        "categories": categories,
        "en_alerte_filter": request.GET.get("alerte") == "1",
        "title": "Catalogue des intrants",
    })



@login_required(login_url=LOGIN_URL)
def intrant_detail(request, pk):
    """
    Intrant detail: catalogue info + current stock balance + recent movements.
    """
    intrant = get_object_or_404(
        Intrant.objects.select_related("categorie", "stock"),
        pk=pk,
    )

    # Stock balance
    try:
        stock = intrant.stock
    except Exception:
        stock = None

    # Recent stock movements (last 20)
    try:
        from stock.models import StockMouvement
        mouvements = (
            StockMouvement.objects.filter(intrant=intrant)
            .order_by("-date_mouvement", "-created_at")[:20]
        )
    except Exception:
        mouvements = []

    fournisseurs = intrant.fournisseurs.filter(actif=True).order_by("nom")

    return render(request, "intrants/intrant_detail.html", {
        "intrant": intrant,
        "stock": stock,
        "mouvements": mouvements,
        "fournisseurs": fournisseurs,
        "title": intrant.designation,
    })


@login_required(login_url=LOGIN_URL)
def intrant_create(request):
    if request.method == "POST":
        form = IntrantForm(request.POST)
        if form.is_valid():
            obj = form.save()
            messages.success(
                request,
                f"Intrant « {obj.designation} » créé avec succès. "
                f"Une fiche de stock a été initialisée automatiquement.",
            )
            logger.info("Intrant pk=%s created by '%s'.", obj.pk, request.user)
            return redirect("intrants:intrant_detail", pk=obj.pk)
        messages.error(request, "Veuillez corriger les erreurs.")
    else:
        form = IntrantForm()
    return render(request, "intrants/intrant_form.html", {
        "form": form,
        "title": "Nouvel intrant",
        "action_label": "Créer",
    })


@login_required(login_url=LOGIN_URL)
def intrant_edit(request, pk):
    intrant = get_object_or_404(Intrant, pk=pk)
    if request.method == "POST":
        form = IntrantForm(request.POST, instance=intrant)
        if form.is_valid():
            form.save()
            messages.success(request, f"Intrant « {intrant.designation} » mis à jour.")
            logger.info("Intrant pk=%s edited by '%s'.", pk, request.user)
            return redirect("intrants:intrant_detail", pk=pk)
        messages.error(request, "Veuillez corriger les erreurs.")
    else:
        form = IntrantForm(instance=intrant)
    return render(request, "intrants/intrant_form.html", {
        "form": form,
        "object": intrant,
        "title": f"Modifier — {intrant.designation}",
        "action_label": "Enregistrer",
    })


@login_required(login_url=LOGIN_URL)
@require_POST
def intrant_toggle_active(request, pk):
    """Deactivate / reactivate an intrant. Does NOT delete."""
    intrant = get_object_or_404(Intrant, pk=pk)
    intrant.actif = not intrant.actif
    intrant.save(update_fields=["actif", "updated_at"])
    state = "activé" if intrant.actif else "désactivé"
    messages.success(request, f"Intrant « {intrant.designation} » {state}.")
    logger.info("Intrant pk=%s set actif=%s by '%s'.", pk, intrant.actif, request.user)
    return redirect("intrants:intrant_list")


# ===========================================================================
# AJAX helpers
# ===========================================================================

@login_required(login_url=LOGIN_URL)
def intrant_stock_json(request, pk):
    """
    Return the current stock balance and PMP for one intrant as JSON.
    Used by BL Fournisseur / Consommation forms to display live stock.
    Spec §9 requires this for the intrant selection widget.
    """
    from django.http import JsonResponse

    intrant = get_object_or_404(Intrant, pk=pk)
    try:
        stock = intrant.stock
        data = {
            "quantite": float(stock.quantite),
            "prix_moyen_pondere": float(stock.prix_moyen_pondere),
            "unite_mesure": intrant.unite_mesure,
            "en_alerte": stock.en_alerte,
            "seuil_alerte": float(intrant.seuil_alerte),
        }
    except Exception:
        data = {
            "quantite": 0,
            "prix_moyen_pondere": 0,
            "unite_mesure": intrant.unite_mesure,
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
        .values("id", "designation", "unite_mesure", "categorie__libelle")
        .order_by("designation")
    )
    return JsonResponse({"intrants": intrants})
