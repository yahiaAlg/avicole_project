"""
depenses/views.py

Function-based views for the operational expense (dépenses) module.

  CategorieDepense : list, create, edit, toggle active
  Depense          : list, create, detail, edit, delete, print (expense voucher)
  Dashboard        : expense summary with category breakdown

Business rules enforced here (complementing model.clean() and forms):
  BR-DEP-01  Goods-type facture fournisseur NEVER linked to a dépense.
  BR-DEP-02  AP and dépenses draw from mutually exclusive sources.
  BR-DEP-03  Only Service-type supplier invoices may optionally be linked —
             enforced in DepenseForm and double-checked in the view.
  BR-DEP-04  Dépenses may optionally be attributed to a lot (profitability).

All write operations use Post-Redirect-Get.
The print view renders a dedicated template (expense voucher) — no PDF library.
No signals are required for this module: dépenses have no automated
side-effects on stock, payments, or other domain objects.
"""

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db import transaction
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from depenses.forms import (
    CategorieDepenseForm,
    DashboardFilterForm,
    DepenseFilterForm,
    DepenseForm,
)
from depenses.models import CategorieDepense, Depense
from depenses.utils import get_depenses_par_categorie, get_depenses_summary

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
# Dashboard
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def depenses_dashboard(request):
    """
    Expense module dashboard with date-range filtering.

    When no filter is submitted the default period is the current month.
    The "previous period" comparison always covers an equal-length window
    immediately preceding the selected range, so the delta % is meaningful
    regardless of the chosen span.

    Quick-preset buttons (Ce mois / Mois préc. / 3 mois / YTD) are handled
    purely in the template via JS that fills the date inputs and submits.
    """
    import datetime
    from django.db.models import Sum, Count
    from decimal import Decimal

    today = datetime.date.today()
    premier_du_mois = today.replace(day=1)
    premier_de_lannee = today.replace(month=1, day=1)

    # ── Parse / validate the date-range filter ────────────────────────────
    filter_form = DashboardFilterForm(request.GET or None)

    if filter_form.is_valid():
        date_debut = filter_form.cleaned_data.get("date_debut") or premier_du_mois
        date_fin = filter_form.cleaned_data.get("date_fin") or today
    else:
        # Invalid input (e.g. bad date string) → fall back to current month
        date_debut = premier_du_mois
        date_fin = today

    # Clamp date_fin to today so we never show "future" data
    if date_fin > today:
        date_fin = today

    # ── Previous period (same duration, immediately preceding) ────────────
    duree = (date_fin - date_debut).days  # inclusive span - 1
    prev_fin = date_debut - datetime.timedelta(days=1)
    prev_debut = prev_fin - datetime.timedelta(days=duree)

    # ── Summaries ─────────────────────────────────────────────────────────
    summary_periode = get_depenses_summary(date_debut=date_debut, date_fin=date_fin)
    summary_annee = get_depenses_summary(date_debut=premier_de_lannee, date_fin=today)
    summary_precedent = get_depenses_summary(date_debut=prev_debut, date_fin=prev_fin)

    # ── Period-aware category breakdown (used in bottom table + donut) ────
    par_categorie_periode = get_depenses_par_categorie(
        date_debut=date_debut, date_fin=date_fin
    )

    # ── 6-month monthly trend (always absolute — not filtered) ────────────
    tendance_6_mois = []
    for i in range(5, -1, -1):
        target = premier_du_mois
        for _ in range(i):
            target = (target - datetime.timedelta(days=1)).replace(day=1)
        if i == 0:
            mois_fin = today
        else:
            next_m = (target.replace(day=28) + datetime.timedelta(days=4)).replace(
                day=1
            )
            mois_fin = next_m - datetime.timedelta(days=1)
        qs = Depense.objects.filter(date__gte=target, date__lte=mois_fin)
        agg = qs.aggregate(total=Sum("montant"), nb=Count("pk"))
        tendance_6_mois.append(
            {
                "label": target.strftime("%b %Y"),
                "mois": target.strftime("%m/%Y"),
                "total": float(agg["total"] or 0),
                "nb": agg["nb"] or 0,
            }
        )

    # ── Recent expenses (always the 10 most recent, unfiltered) ──────────
    depenses_recentes = Depense.objects.select_related("categorie", "lot").order_by(
        "-date", "-created_at"
    )[:10]

    nb_categories_actives = CategorieDepense.objects.filter(actif=True).count()

    # ── Period-over-period delta % ────────────────────────────────────────
    total_periode = summary_periode["total"]
    total_precedent = summary_precedent["total"]
    if total_precedent and total_precedent > 0:
        delta_pct = round(
            float((total_periode - total_precedent) / total_precedent * 100), 1
        )
    else:
        delta_pct = None

    # ── Label helper for the template (human-readable period description) ─
    if date_debut == premier_du_mois and date_fin == today:
        periode_label = "Mois en cours"
    elif date_debut == premier_de_lannee and date_fin == today:
        periode_label = "Depuis le 1ᵉʳ janvier"
    else:
        periode_label = (
            f"{date_debut.strftime('%d/%m/%Y')} → {date_fin.strftime('%d/%m/%Y')}"
        )

    return render(
        request,
        "depenses/dashboard.html",
        {
            # filter
            "filter_form": filter_form,
            "date_debut": date_debut,
            "date_fin": date_fin,
            "periode_label": periode_label,
            # summaries
            "summary_periode": summary_periode,
            "summary_annee": summary_annee,
            "summary_precedent": summary_precedent,
            # breakdowns
            "par_categorie_periode": par_categorie_periode,
            # trend
            "tendance_6_mois": tendance_6_mois,
            # misc
            "depenses_recentes": depenses_recentes,
            "nb_categories_actives": nb_categories_actives,
            "today": today,
            "premier_du_mois": premier_du_mois,
            "premier_de_lannee": premier_de_lannee,
            "delta_pct": delta_pct,
            "title": "Tableau de bord — Dépenses",
        },
    )


# ===========================================================================
# CategorieDepense — List
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def categorie_depense_list(request):
    """
    List all expense categories, including inactive ones.

    Filters:
      ?actif=0  — include inactive categories (default: active only shown first)
    """
    qs = CategorieDepense.objects.order_by("ordre", "libelle")

    actif_param = request.GET.get("actif", "")
    if actif_param == "1":
        qs = qs.filter(actif=True)
    elif actif_param == "0":
        qs = qs.filter(actif=False)

    # Annotate with depenses count for display
    qs = qs.annotate(nb_depenses=Sum("depenses__montant"))

    return render(
        request,
        "depenses/categorie_depense_list.html",
        {
            "categories": qs,
            "actif_param": actif_param,
            "title": "Catégories de dépenses",
        },
    )


# ===========================================================================
# CategorieDepense — Create
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def categorie_depense_create(request):
    if request.method == "POST":
        form = CategorieDepenseForm(request.POST)
        if form.is_valid():
            try:
                categorie = form.save()
                messages.success(
                    request,
                    f"Catégorie « {categorie.libelle} » créée avec succès.",
                )
                logger.info(
                    "CategorieDepense pk=%s ('%s') created by '%s'.",
                    categorie.pk,
                    categorie.code,
                    request.user,
                )
                return redirect("depenses:categorie_depense_list")
            except Exception as exc:
                logger.exception("Error creating CategorieDepense: %s", exc)
                messages.error(request, f"Erreur lors de la création : {exc}")
        else:
            messages.error(request, "Veuillez corriger les erreurs ci-dessous.")
    else:
        form = CategorieDepenseForm()

    return render(
        request,
        "depenses/categorie_depense_form.html",
        {
            "form": form,
            "title": "Nouvelle catégorie de dépense",
            "action_label": "Créer la catégorie",
        },
    )


# ===========================================================================
# CategorieDepense — Edit
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def categorie_depense_edit(request, pk):
    categorie = get_object_or_404(CategorieDepense, pk=pk)

    if request.method == "POST":
        form = CategorieDepenseForm(request.POST, instance=categorie)
        if form.is_valid():
            try:
                form.save()
                messages.success(
                    request,
                    f"Catégorie « {categorie.libelle} » mise à jour.",
                )
                logger.info(
                    "CategorieDepense pk=%s updated by '%s'.",
                    categorie.pk,
                    request.user,
                )
                return redirect("depenses:categorie_depense_list")
            except Exception as exc:
                logger.exception("Error updating CategorieDepense pk=%s: %s", pk, exc)
                messages.error(request, f"Erreur lors de la mise à jour : {exc}")
        else:
            messages.error(request, "Veuillez corriger les erreurs ci-dessous.")
    else:
        form = CategorieDepenseForm(instance=categorie)

    return render(
        request,
        "depenses/categorie_depense_form.html",
        {
            "form": form,
            "categorie": categorie,
            "title": f"Modifier — {categorie.libelle}",
            "action_label": "Enregistrer les modifications",
        },
    )


# ===========================================================================
# CategorieDepense — Toggle Active  (POST-only)
# ===========================================================================


@login_required(login_url=LOGIN_URL)
@require_POST
def categorie_depense_toggle_active(request, pk):
    """
    Soft-activate or soft-deactivate a category.
    Deactivated categories no longer appear in the Depense creation form
    but existing records that reference them are preserved.
    """
    categorie = get_object_or_404(CategorieDepense, pk=pk)
    categorie.actif = not categorie.actif
    categorie.save(update_fields=["actif"])
    state = "activée" if categorie.actif else "désactivée"
    messages.success(request, f"Catégorie « {categorie.libelle} » {state}.")
    logger.info(
        "CategorieDepense pk=%s toggled actif=%s by '%s'.",
        categorie.pk,
        categorie.actif,
        request.user,
    )
    return redirect("depenses:categorie_depense_list")


# ===========================================================================
# Depense — List
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def depense_list(request):
    """
    List all dépenses with filtering.

    Spec §9.10 columns: Date | Category | Description | Amount | Method |
                        Lot (if attributed)
    Filters (spec §9.10): Category, date range, lot, method
    """
    qs = Depense.objects.select_related("categorie", "lot", "enregistre_par").order_by(
        "-date", "-created_at"
    )

    filter_form = DepenseFilterForm(request.GET or None)
    if filter_form.is_valid():
        cd = filter_form.cleaned_data

        if cd.get("categorie"):
            qs = qs.filter(categorie=cd["categorie"])

        if cd.get("date_debut"):
            qs = qs.filter(date__gte=cd["date_debut"])

        if cd.get("date_fin"):
            qs = qs.filter(date__lte=cd["date_fin"])

        if cd.get("lot"):
            qs = qs.filter(lot=cd["lot"])

        if cd.get("mode_paiement"):
            qs = qs.filter(mode_paiement=cd["mode_paiement"])

    # Quick text search (not in spec filter form, but practical)
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(
            Q(description__icontains=q)
            | Q(reference_document__icontains=q)
            | Q(notes__icontains=q)
        )

    # Header totals
    total = qs.aggregate(total=Sum("montant"))["total"] or 0

    page = _paginate(qs, request.GET.get("page"))

    return render(
        request,
        "depenses/depense_list.html",
        {
            "page": page,
            "filter_form": filter_form,
            "q": q,
            "total": total,
            "title": "Dépenses opérationnelles",
        },
    )


# ===========================================================================
# Depense — Create
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def depense_create(request):
    """
    Record a new operational expense.

    BR-DEP-03: the form already restricts facture_liee to Service-type
    invoices.  The view double-checks via model.clean() inside full_clean().
    BR-DEP-04: lot attribution is optional and purely informational here.
    """
    if request.method == "POST":
        form = DepenseForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                with transaction.atomic():
                    depense = form.save(commit=False)
                    depense.enregistre_par = request.user
                    # Trigger model-level clean() for BR-DEP-01/03 double-guard.
                    depense.full_clean()
                    depense.save()

                messages.success(
                    request,
                    f"Dépense « {depense.description[:60]} » enregistrée "
                    f"({depense.montant} DZD).",
                )
                logger.info(
                    "Depense pk=%s created by '%s' "
                    "(categorie=%s, montant=%s DZD, date=%s).",
                    depense.pk,
                    request.user,
                    depense.categorie.code,
                    depense.montant,
                    depense.date,
                )
                return redirect("depenses:depense_detail", pk=depense.pk)

            except Exception as exc:
                logger.exception("Error creating Depense: %s", exc)
                messages.error(request, f"Erreur lors de l'enregistrement : {exc}")
        else:
            messages.error(request, "Veuillez corriger les erreurs ci-dessous.")
    else:
        import datetime

        form = DepenseForm(initial={"date": datetime.date.today()})

    return render(
        request,
        "depenses/depense_form.html",
        {
            "form": form,
            "title": "Enregistrer une dépense",
            "action_label": "Enregistrer la dépense",
        },
    )


# ===========================================================================
# Depense — Detail
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def depense_detail(request, pk):
    depense = get_object_or_404(
        Depense.objects.select_related(
            "categorie", "lot", "facture_liee__fournisseur", "enregistre_par"
        ),
        pk=pk,
    )
    return render(
        request,
        "depenses/depense_detail.html",
        {
            "depense": depense,
            "title": f"Dépense — {depense.date} | {depense.categorie.libelle}",
        },
    )


# ===========================================================================
# Depense — Edit
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def depense_edit(request, pk):
    """
    Edit an existing dépense.

    BR-DEP-03 is re-enforced via DepenseForm and model.clean() on save.
    """
    depense = get_object_or_404(Depense, pk=pk)

    if request.method == "POST":
        form = DepenseForm(request.POST, request.FILES, instance=depense)
        if form.is_valid():
            try:
                with transaction.atomic():
                    updated = form.save(commit=False)
                    # Re-run model-level validation on update.
                    updated.full_clean()
                    updated.save()

                messages.success(
                    request,
                    f"Dépense « {depense.description[:60]} » mise à jour.",
                )
                logger.info("Depense pk=%s updated by '%s'.", depense.pk, request.user)
                return redirect("depenses:depense_detail", pk=depense.pk)

            except Exception as exc:
                logger.exception("Error updating Depense pk=%s: %s", pk, exc)
                messages.error(request, f"Erreur lors de la mise à jour : {exc}")
        else:
            messages.error(request, "Veuillez corriger les erreurs ci-dessous.")
    else:
        form = DepenseForm(instance=depense)

    return render(
        request,
        "depenses/depense_form.html",
        {
            "form": form,
            "depense": depense,
            "title": f"Modifier la dépense — {depense.date}",
            "action_label": "Enregistrer les modifications",
        },
    )


# ===========================================================================
# Depense — Delete  (POST-only)
# ===========================================================================


@login_required(login_url=LOGIN_URL)
@require_POST
def depense_delete(request, pk):
    """
    Hard-delete a dépense record.

    Per spec: incorrect entries should normally be corrected via a
    counter-entry; hard-delete is available within the same business day
    before any period close.  No soft-delete is implemented (spec §14.2).
    """
    depense = get_object_or_404(Depense, pk=pk)
    description = depense.description[:60]
    date = depense.date
    try:
        depense.delete()
        messages.success(
            request,
            f"Dépense « {description} » du {date} supprimée.",
        )
        logger.info(
            "Depense pk=%s ('%s') deleted by '%s'.",
            pk,
            description,
            request.user,
        )
    except Exception as exc:
        logger.exception("Error deleting Depense pk=%s: %s", pk, exc)
        messages.error(request, f"Erreur lors de la suppression : {exc}")
        return redirect("depenses:depense_detail", pk=pk)

    return redirect("depenses:depense_list")


# ===========================================================================
# Depense — Print (Expense Voucher / Pièce Justificative)
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def depense_print(request, pk):
    """
    Print-optimised expense voucher (Pièce Justificative de Dépense).

    Spec §16.8 content:
      - Document title, farm name, voucher number (= PK)
      - Date, category, description, amount
      - Payment method, lot (if any), external reference
      - Approved-by / signature field, notes

    Renders a dedicated template with @media print CSS — no PDF library.
    """
    depense = get_object_or_404(
        Depense.objects.select_related(
            "categorie", "lot", "facture_liee__fournisseur", "enregistre_par"
        ),
        pk=pk,
    )

    from core.models import CompanyInfo

    company = CompanyInfo.get_instance()

    return render(
        request,
        "depenses/depense_print.html",
        {
            "depense": depense,
            "company": company,
        },
    )
