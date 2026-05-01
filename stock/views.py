"""
stock/views.py

Function-based views for stock management:

  StockIntrant     : list, detail  (balance updated exclusively by signals)
  StockProduitFini : list, detail  (balance updated exclusively by signals)
  StockMouvement   : list          (immutable audit trail)
  StockAjustement  : list, create  (immutable after creation — no edit)

All write operations use Post-Redirect-Get.
StockMouvement records have no create/edit/delete views — they are generated
automatically by signals in achats, elevage, production, and stock apps.
"""

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from stock.forms import StockAjustementForm
from stock.models import StockAjustement, StockIntrant, StockMouvement, StockProduitFini

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


# ===========================================================================
# StockIntrant — List
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def stock_intrant_list(request):
    """
    Input-goods stock list.

    Filters:
      ?alerte=1          — only items at or below their alert threshold
      ?categorie=<pk>    — filter by CategorieIntrant
      ?q=<search>        — search by designation or category label
    """
    from intrants.models import CategorieIntrant

    qs = StockIntrant.objects.select_related("intrant__categorie").order_by(
        "intrant__categorie__libelle", "intrant__designation"
    )

    # Category filter
    categorie_pk = request.GET.get("categorie", "")
    if categorie_pk:
        qs = qs.filter(intrant__categorie_id=categorie_pk)

    # Search
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(
            Q(intrant__designation__icontains=q)
            | Q(intrant__categorie__libelle__icontains=q)
        )

    # Alert filter — requires Python-level evaluation (cross-field comparison)
    en_alerte = request.GET.get("alerte") == "1"
    if en_alerte:
        qs = [s for s in qs if s.en_alerte]

    page = _paginate(qs, request.GET.get("page"))
    categories = CategorieIntrant.objects.filter(actif=True).order_by(
        "ordre", "libelle"
    )

    # Summary totals (only meaningful without alert filter)
    valeur_totale = None
    if not en_alerte:
        try:
            valeur_totale = sum(
                float(s.valeur_stock)
                for s in (qs if hasattr(qs, "__iter__") else qs.all())
            )
        except Exception:
            valeur_totale = None

    return render(
        request,
        "stock/stock_intrant_list.html",
        {
            "page": page,
            "q": q,
            "categorie_pk": categorie_pk,
            "categories": categories,
            "en_alerte": en_alerte,
            "valeur_totale": valeur_totale,
            "title": "Stock Intrants",
        },
    )


# ===========================================================================
# StockIntrant — Detail
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def stock_intrant_detail(request, pk):
    """
    Stock detail for one intrant: current balance, PMP, recent movements,
    and pending adjustments.
    """
    stock = get_object_or_404(
        StockIntrant.objects.select_related("intrant__categorie"),
        pk=pk,
    )
    mouvements = (
        StockMouvement.objects.filter(intrant=stock.intrant)
        .select_related("created_by")
        .order_by("-date_mouvement", "-created_at")[:50]
    )
    ajustements = (
        StockAjustement.objects.filter(intrant=stock.intrant)
        .select_related("effectue_par")
        .order_by("-date_ajustement")[:10]
    )

    return render(
        request,
        "stock/stock_intrant_detail.html",
        {
            "stock": stock,
            "intrant": stock.intrant,
            "mouvements": mouvements,
            "ajustements": ajustements,
            "title": f"Stock — {stock.intrant.designation}",
        },
    )


# ===========================================================================
# StockProduitFini — List
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def stock_produit_fini_list(request):
    """
    Finished-goods stock list.

    Filters:
      ?alerte=1           — only items at or below their alert threshold
      ?type_produit=<val> — filter by ProduitFini.type_produit
      ?q=<search>         — search by designation or type label
    """
    from production.models import ProduitFini

    qs = StockProduitFini.objects.select_related("produit_fini").order_by(
        "produit_fini__type_produit", "produit_fini__designation"
    )

    type_produit = request.GET.get("type_produit", "")
    if type_produit:
        qs = qs.filter(produit_fini__type_produit=type_produit)

    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(produit_fini__designation__icontains=q)

    en_alerte = request.GET.get("alerte") == "1"
    if en_alerte:
        qs = [s for s in qs if s.en_alerte]

    page = _paginate(qs, request.GET.get("page"))

    valeur_totale = None
    if not en_alerte:
        try:
            valeur_totale = sum(
                float(s.valeur_stock)
                for s in (qs if isinstance(qs, list) else qs.all())
            )
        except Exception:
            valeur_totale = None

    return render(
        request,
        "stock/stock_produit_fini_list.html",
        {
            "page": page,
            "q": q,
            "type_produit": type_produit,
            "type_choices": ProduitFini.TYPE_CHOICES,
            "en_alerte": en_alerte,
            "valeur_totale": valeur_totale,
            "title": "Stock Produits Finis",
        },
    )


# ===========================================================================
# StockProduitFini — Detail
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def stock_produit_fini_detail(request, pk):
    """
    Stock detail for one produit fini: balance, average production cost,
    recent movements, and adjustments.
    """
    stock = get_object_or_404(
        StockProduitFini.objects.select_related("produit_fini"),
        pk=pk,
    )
    mouvements = (
        StockMouvement.objects.filter(produit_fini=stock.produit_fini)
        .select_related("created_by")
        .order_by("-date_mouvement", "-created_at")[:50]
    )
    ajustements = (
        StockAjustement.objects.filter(produit_fini=stock.produit_fini)
        .select_related("effectue_par")
        .order_by("-date_ajustement")[:10]
    )

    return render(
        request,
        "stock/stock_produit_fini_detail.html",
        {
            "stock": stock,
            "produit_fini": stock.produit_fini,
            "mouvements": mouvements,
            "ajustements": ajustements,
            "title": f"Stock — {stock.produit_fini.designation}",
        },
    )


# ===========================================================================
# StockMouvement — List  (unified audit trail)
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def stock_mouvement_list(request):
    """
    Full movement audit trail across both stock segments.

    Filters:
      ?segment=intrant|produit_fini
      ?type_mouvement=entree|sortie|ajustement
      ?source=<source_code>
      ?date_debut=YYYY-MM-DD
      ?date_fin=YYYY-MM-DD
      ?q=<search>   — searches intrant/produit designation and reference label
    """
    qs = StockMouvement.objects.select_related(
        "intrant", "produit_fini", "created_by"
    ).order_by("-date_mouvement", "-created_at")

    # Segment filter
    segment = request.GET.get("segment", "")
    if segment == "intrant":
        qs = qs.filter(intrant__isnull=False)
    elif segment == "produit_fini":
        qs = qs.filter(produit_fini__isnull=False)

    # Type / source filters
    type_mouvement = request.GET.get("type_mouvement", "")
    if type_mouvement:
        qs = qs.filter(type_mouvement=type_mouvement)

    source = request.GET.get("source", "")
    if source:
        qs = qs.filter(source=source)

    # Date range
    date_debut = request.GET.get("date_debut", "")
    date_fin = request.GET.get("date_fin", "")
    if date_debut:
        qs = qs.filter(date_mouvement__gte=date_debut)
    if date_fin:
        qs = qs.filter(date_mouvement__lte=date_fin)

    # Search
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(
            Q(intrant__designation__icontains=q)
            | Q(produit_fini__designation__icontains=q)
            | Q(reference_label__icontains=q)
            | Q(notes__icontains=q)
        )

    page = _paginate(qs, request.GET.get("page"))

    return render(
        request,
        "stock/stock_mouvement_list.html",
        {
            "page": page,
            "q": q,
            "segment": segment,
            "type_mouvement": type_mouvement,
            "source": source,
            "date_debut": date_debut,
            "date_fin": date_fin,
            "type_choices": StockMouvement.TYPE_CHOICES,
            "source_choices": StockMouvement.SOURCE_CHOICES,
            "title": "Mouvements de stock",
        },
    )


# ===========================================================================
# StockAjustement — List
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def stock_ajustement_list(request):
    """
    Manual adjustment history.

    Filters:
      ?segment=intrant|produit_fini
      ?date_debut, ?date_fin
    """
    qs = StockAjustement.objects.select_related(
        "intrant", "produit_fini", "effectue_par"
    ).order_by("-date_ajustement", "-created_at")

    segment = request.GET.get("segment", "")
    if segment == StockAjustement.SEGMENT_INTRANT:
        qs = qs.filter(segment=StockAjustement.SEGMENT_INTRANT)
    elif segment == StockAjustement.SEGMENT_PRODUIT_FINI:
        qs = qs.filter(segment=StockAjustement.SEGMENT_PRODUIT_FINI)

    date_debut = request.GET.get("date_debut", "")
    date_fin = request.GET.get("date_fin", "")
    if date_debut:
        qs = qs.filter(date_ajustement__gte=date_debut)
    if date_fin:
        qs = qs.filter(date_ajustement__lte=date_fin)

    page = _paginate(qs, request.GET.get("page"))

    return render(
        request,
        "stock/stock_ajustement_list.html",
        {
            "page": page,
            "segment": segment,
            "date_debut": date_debut,
            "date_fin": date_fin,
            "segment_choices": StockAjustement.SEGMENT_CHOICES,
            "title": "Ajustements de stock",
        },
    )


# ===========================================================================
# StockAjustement — Create
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def stock_ajustement_create(request):
    """
    Record a manual stock correction.

    BR-INT-04: a mandatory justification reason is required.
    The post_save signal applies the correction to StockIntrant /
    StockProduitFini and creates a StockMouvement of type AJUSTEMENT.

    The view pre-populates quantite_avant from the current stock balance when
    the segment + item are known (passed as GET params for deep-link from
    stock detail pages):
      ?segment=intrant&intrant=<pk>
      ?segment=produit_fini&produit_fini=<pk>
    """
    initial = {}
    current_stock = None

    segment_param = request.GET.get("segment", "")
    intrant_pk = request.GET.get("intrant", "")
    produit_fini_pk = request.GET.get("produit_fini", "")

    # Pre-fill quantite_avant from live stock balance (deep-link from detail pages)
    if segment_param == StockAjustement.SEGMENT_INTRANT and intrant_pk:
        try:
            current_stock = StockIntrant.objects.select_related("intrant").get(
                intrant_id=intrant_pk
            )
            initial = {
                "segment": StockAjustement.SEGMENT_INTRANT,
                "intrant": current_stock.intrant,
                "quantite_avant": current_stock.quantite,
            }
        except StockIntrant.DoesNotExist:
            pass

    elif segment_param == StockAjustement.SEGMENT_PRODUIT_FINI and produit_fini_pk:
        try:
            current_stock = StockProduitFini.objects.select_related("produit_fini").get(
                produit_fini_id=produit_fini_pk
            )
            initial = {
                "segment": StockAjustement.SEGMENT_PRODUIT_FINI,
                "produit_fini": current_stock.produit_fini,
                "quantite_avant": current_stock.quantite,
            }
        except StockProduitFini.DoesNotExist:
            pass

    if request.method == "POST":
        form = StockAjustementForm(request.POST)
        if form.is_valid():
            try:
                ajustement = form.save(commit=False)
                ajustement.effectue_par = request.user

                # Snapshot the current quantite_avant from live stock just before
                # saving, in case the user's pre-loaded value is stale.
                if (
                    ajustement.segment == StockAjustement.SEGMENT_INTRANT
                    and ajustement.intrant_id
                ):
                    try:
                        live = StockIntrant.objects.get(
                            intrant_id=ajustement.intrant_id
                        )
                        ajustement.quantite_avant = live.quantite
                    except StockIntrant.DoesNotExist:
                        pass

                elif (
                    ajustement.segment == StockAjustement.SEGMENT_PRODUIT_FINI
                    and ajustement.produit_fini_id
                ):
                    try:
                        live = StockProduitFini.objects.get(
                            produit_fini_id=ajustement.produit_fini_id
                        )
                        ajustement.quantite_avant = live.quantite
                    except StockProduitFini.DoesNotExist:
                        pass

                ajustement.save()  # triggers post_save → balance update + mouvement

                item_name = (
                    ajustement.intrant.designation
                    if ajustement.intrant_id
                    else ajustement.produit_fini.designation
                )
                delta = ajustement.quantite_apres - ajustement.quantite_avant
                sign = "+" if delta >= 0 else ""
                messages.success(
                    request,
                    f"Ajustement enregistré pour « {item_name} » : "
                    f"{sign}{delta} (avant : {ajustement.quantite_avant} → "
                    f"après : {ajustement.quantite_apres}).",
                )
                logger.info(
                    "StockAjustement pk=%s created by '%s' (segment=%s, delta=%s).",
                    ajustement.pk,
                    request.user,
                    ajustement.segment,
                    delta,
                )
                return redirect("stock:stock_ajustement_list")

            except Exception as exc:
                logger.exception("Error creating StockAjustement: %s", exc)
                messages.error(request, f"Erreur lors de l'ajustement : {exc}")

        else:
            messages.error(request, "Veuillez corriger les erreurs.")

    else:
        form = StockAjustementForm(initial=initial)

    return render(
        request,
        "stock/stock_ajustement_form.html",
        {
            "form": form,
            "current_stock": current_stock,
            "title": "Nouvel ajustement de stock",
            "action_label": "Enregistrer l'ajustement",
        },
    )


# ===========================================================================
# Dashboard — stock overview
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def stock_dashboard(request):
    """
    Stock overview dashboard:
      - Count and value of intrant stock
      - Count and value of finished-goods stock
      - Items in alert state
      - Recent movements (last 15)
    """
    # Intrant summary
    intrant_stocks = StockIntrant.objects.select_related("intrant__categorie").all()
    nb_intrants = intrant_stocks.count()
    valeur_intrants = sum(float(s.valeur_stock) for s in intrant_stocks)
    intrants_en_alerte = [s for s in intrant_stocks if s.en_alerte]

    # Produit fini summary
    pf_stocks = StockProduitFini.objects.select_related("produit_fini").all()
    nb_pf = pf_stocks.count()
    valeur_pf = sum(float(s.valeur_stock) for s in pf_stocks)
    pf_en_alerte = [s for s in pf_stocks if s.en_alerte]

    # Recent movements
    mouvements_recents = StockMouvement.objects.select_related(
        "intrant", "produit_fini"
    ).order_by("-date_mouvement", "-created_at")[:15]

    return render(
        request,
        "stock/stock_dashboard.html",
        {
            "nb_intrants": nb_intrants,
            "valeur_intrants": round(valeur_intrants, 2),
            "intrants_en_alerte": intrants_en_alerte,
            "nb_pf": nb_pf,
            "valeur_pf": round(valeur_pf, 2),
            "pf_en_alerte": pf_en_alerte,
            "mouvements_recents": mouvements_recents,
            "title": "Tableau de bord — Stock",
        },
    )


# ===========================================================================
# AJAX helpers
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def stock_intrant_balance_json(request, pk):
    """
    Return the current balance for one StockIntrant as JSON.
    Called when the user changes the intrant selection in adjustment form.

    Returns:
        {"quantite": ..., "prix_moyen_pondere": ..., "unite_mesure": ...,
         "en_alerte": ..., "seuil_alerte": ...}
    """
    from intrants.models import Intrant

    intrant = get_object_or_404(Intrant, pk=pk)
    try:
        stock = intrant.stock
        data = {
            "quantite": float(stock.quantite),
            "prix_moyen_pondere": float(stock.prix_moyen_pondere),
            "valeur_stock": float(stock.valeur_stock),
            "unite_mesure": intrant.unite_mesure,
            "en_alerte": stock.en_alerte,
            "seuil_alerte": float(intrant.seuil_alerte),
        }
    except Exception:
        data = {
            "quantite": 0,
            "prix_moyen_pondere": 0,
            "valeur_stock": 0,
            "unite_mesure": intrant.unite_mesure,
            "en_alerte": True,
            "seuil_alerte": float(intrant.seuil_alerte),
        }
    return JsonResponse(data)


@login_required(login_url=LOGIN_URL)
def stock_produit_fini_balance_json(request, pk):
    """
    Return the current balance for one StockProduitFini as JSON.

    Returns:
        {"quantite": ..., "cout_moyen_production": ..., "unite_mesure": ...,
         "en_alerte": ...}
    """
    from production.models import ProduitFini

    produit = get_object_or_404(ProduitFini, pk=pk)
    try:
        stock = produit.stock
        data = {
            "quantite": float(stock.quantite),
            "cout_moyen_production": float(stock.cout_moyen_production),
            "valeur_stock": float(stock.valeur_stock),
            "unite_mesure": produit.unite_mesure,
            "en_alerte": stock.en_alerte,
            "seuil_alerte": float(stock.seuil_alerte),
        }
    except Exception:
        data = {
            "quantite": 0,
            "cout_moyen_production": 0,
            "valeur_stock": 0,
            "unite_mesure": produit.unite_mesure,
            "en_alerte": True,
            "seuil_alerte": 0,
        }
    return JsonResponse(data)


@login_required(login_url=LOGIN_URL)
def stock_intrant_json(request, pk):
    """
    Return the current stock balance and PMP for one intrant as JSON.
    Used by forms to display live stock information.
    """
    from django.http import JsonResponse
    from intrants.models import Intrant

    intrant = get_object_or_404(Intrant, pk=pk)
    try:
        stock = intrant.stock
        data = {
            "quantite": float(stock.quantite),
            "prix_moyen_pondere": float(stock.prix_moyen_pondere),
            "valeur_stock": float(stock.valeur_stock),
            "unite_mesure": intrant.unite_mesure,
            "en_alerte": stock.en_alerte,
            "seuil_alerte": float(intrant.seuil_alerte),
        }
    except Exception:
        data = {
            "quantite": 0,
            "prix_moyen_pondere": 0,
            "valeur_stock": 0,
            "unite_mesure": intrant.unite_mesure,
            "en_alerte": True,
            "seuil_alerte": float(intrant.seuil_alerte),
        }
    return JsonResponse(data)
