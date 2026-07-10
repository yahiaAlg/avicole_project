"""
depenses/views.py

Function-based views for the operational expense (dépenses) module:

  CategorieDepense : list, create, edit, toggle active
  Depense          : list, create, detail, edit, delete, print (expense voucher)
  Dashboard        : expense summary with category breakdown

  Associé           : list, create, edit, detail
  RetraitAssocié     : list, create, edit, delete  (BR-ASSOC-01 / BR-ASSOC-02)

  RH dashboard      : payroll/advances summary
  Employé           : list, create, detail, edit
  Pointage          : list, create, edit, generate-month
  Congé             : list, create  (materializes into Pointage — BR-RH-05)
  Acompte employé   : list, create
  Bulletin de paie  : list, generate, detail, validate, mark-paid, print

Business rules enforced here (complementing model.clean() and forms):
  BR-DEP-01  Goods-type facture fournisseur NEVER linked to a dépense.
  BR-DEP-02  AP and dépenses draw from mutually exclusive sources.
  BR-DEP-03  Only Service-type supplier invoices may optionally be linked —
             enforced in DepenseForm and double-checked in the view.
  BR-DEP-04  Dépenses may optionally be attributed to a lot (profitability).
  BR-ASSOC-01/02  Retraits are equity draws, always manual.
  BR-RH-01..05    See depenses/models.py module docstring.

All write operations use Post-Redirect-Get.
The print views render dedicated templates (vouchers) — no PDF library.
No signals are required for this module: business logic is orchestrated
explicitly here, calling into depenses.utils — see depenses/signals.py.

v1.4 (§3.5, BR-BRA-01/08/09): Depense.branche is a required FK — Vue par
Branche scopes the list/dashboard/create to the request's active branche
(BR-BRA-02); Vue Globale shows every branche combined. Associé and
RetraitAssocié stay company-wide and are NEVER branche-scoped (BR-BRA-08).
Employe.branche is a derived property read from `employe.batiment.branche`
(BR-BRA-09) — Pointage/CongeEmploye/AcompteEmploye/BulletinPaie inherit it
the same way, so every RH queryset below filters via the
`employe__batiment__branche` join rather than a stored field.
"""

import datetime
import logging
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db import transaction
from django.db.models import Q, Sum
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from core.views import (
    branche_matches,
    branche_object_or_404,
    build_piece_jointe_formset,
    get_active_branche,
    require_branche_context,
)
from depenses.forms import (
    AcompteEmployePieceJointeFormSet,
    BulletinPaiePieceJointeFormSet,
    CategorieDepenseForm,
    DashboardFilterForm,
    DepenseFilterForm,
    DepenseForm,
    DepensePieceJointeFormSet,
    AssocieForm,
    RetraitAssocieForm,
    RetraitAssociePieceJointeFormSet,
    RetraitFilterForm,
    EmployeForm,
    PointageForm,
    PointageFilterForm,
    JourFerieForm,
    CongeEmployeForm,
    AcompteEmployeForm,
    DetteEmployeForm,
    DetteEmployePieceJointeFormSet,
    RemboursementDetteForm,
    GenererBulletinPaieForm,
    BulletinPaiementForm,
    RHFilterForm,
)
from depenses.models import (
    CategorieDepense,
    Depense,
    Associe,
    RetraitAssocie,
    Employe,
    Pointage,
    JourFerie,
    CongeEmploye,
    AcompteEmploye,
    DetteEmploye,
    RemboursementDette,
    BulletinPaie,
)
from depenses.utils import (
    get_depenses_par_categorie,
    get_depenses_summary,
    get_retraits_associes_summary,
    get_rh_summary,
    get_dettes_summary,
    get_solde_conge,
    appliquer_conge_aux_pointages,
    calculer_donnees_paie,
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


def _ensure_employe_branche_access(request, obj):
    """
    404 when *obj* (an Employe, or any record whose `.branche` resolves
    through one — Pointage, CongeEmploye, AcompteEmploye, BulletinPaie)
    belongs to a branche other than the request's active one (BR-BRA-02).

    BR-BRA-09: Employe.branche is a derived property read from
    `employe.batiment.branche`, not a stored FK, so this uses
    `branche_matches` (reads `.branche`) rather than `branche_object_or_404`
    (which relies on a `branche_id` column). Vue Globale always passes.
    """
    if not branche_matches(request, obj):
        raise Http404("Cet enregistrement appartient à une autre branche.")


def _is_admin(request):
    """True when the logged-in user's profile role is 'admin' (BR-BRA-03)."""
    profile = getattr(request.user, "profile", None) or getattr(
        request.user, "userprofile", None
    )
    return bool(profile and profile.role == "admin")


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

    v1.4 (§3.5.5): Vue par Branche shows only the active branche's expense
    and payroll figures (BR-BRA-01/09); Vue Globale aggregates across every
    branche. Retraits associés (BR-BRA-08) are always company-wide and never
    scoped, regardless of the active branche.
    """
    import datetime
    from django.db.models import Sum, Count
    from decimal import Decimal

    branche = get_active_branche(request)

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
    summary_periode = get_depenses_summary(
        date_debut=date_debut, date_fin=date_fin, branche=branche
    )
    summary_annee = get_depenses_summary(
        date_debut=premier_de_lannee, date_fin=today, branche=branche
    )
    summary_precedent = get_depenses_summary(
        date_debut=prev_debut, date_fin=prev_fin, branche=branche
    )

    # ── Period-aware category breakdown (used in bottom table + donut) ────
    par_categorie_periode = get_depenses_par_categorie(
        date_debut=date_debut, date_fin=date_fin, branche=branche
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
        if branche is not None:
            qs = qs.filter(branche=branche)
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
    depenses_recentes_qs = Depense.objects.select_related("categorie", "lot")
    if branche is not None:
        depenses_recentes_qs = depenses_recentes_qs.filter(branche=branche)
    depenses_recentes = depenses_recentes_qs.order_by("-date", "-created_at")[:10]

    nb_categories_actives = CategorieDepense.objects.filter(actif=True).count()

    # ── RH + Retraits summaries (for dashboard KPIs) ──────────────────────
    rh_summary = get_rh_summary(
        date_debut=date_debut, date_fin=date_fin, branche=branche
    )
    # BR-BRA-08: never scoped to branche — always company-wide.
    retraits_summary = get_retraits_associes_summary(
        date_debut=date_debut, date_fin=date_fin
    )

    # ── Bulletin status breakdown (BR-BRA-09, via employe__batiment__branche) ─
    bulletins_statut_qs = BulletinPaie.objects.all()
    if branche is not None:
        bulletins_statut_qs = bulletins_statut_qs.filter(
            employe__batiment__branche=branche
        )
    bulletins_statuts = {
        "brouillon": bulletins_statut_qs.filter(
            statut=BulletinPaie.STATUT_BROUILLON
        ).count(),
        "valide": bulletins_statut_qs.filter(statut=BulletinPaie.STATUT_VALIDE).count(),
        "paye": bulletins_statut_qs.filter(statut=BulletinPaie.STATUT_PAYE).count(),
    }

    # ── 6-month payroll + retraits trend (mirrors expense trend) ──────────
    from django.db.models import Sum as _Sum, Count as _Count

    tendance_paie_retraits_6_mois = []
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
        sal_qs = BulletinPaie.objects.filter(
            statut=BulletinPaie.STATUT_PAYE,
            date_paiement__gte=target,
            date_paiement__lte=mois_fin,
        )
        acomp_qs = AcompteEmploye.objects.filter(date__gte=target, date__lte=mois_fin)
        if branche is not None:
            sal_qs = sal_qs.filter(employe__batiment__branche=branche)
            acomp_qs = acomp_qs.filter(employe__batiment__branche=branche)
        sal = sal_qs.aggregate(total=_Sum("montant_net"))["total"] or 0
        acomp = acomp_qs.aggregate(total=_Sum("montant"))["total"] or 0
        # BR-BRA-08: stakeholder withdrawals are never branche-scoped.
        retr = (
            RetraitAssocie.objects.filter(
                date__gte=target, date__lte=mois_fin
            ).aggregate(total=_Sum("montant"))["total"]
            or 0
        )
        tendance_paie_retraits_6_mois.append(
            {
                "label": target.strftime("%b %Y"),
                "salaires": float(sal),
                "acomptes": float(acomp),
                "retraits": float(retr),
            }
        )

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
        periode_label = "الشهر الجاري"
    elif date_debut == premier_de_lannee and date_fin == today:
        periode_label = "منذ الأول من يناير"
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
            # RH + Retraits
            "rh_summary": rh_summary,
            "retraits_summary": retraits_summary,
            "bulletins_statuts": bulletins_statuts,
            "tendance_paie_retraits_6_mois": tendance_paie_retraits_6_mois,
            "active_branche": branche,
            "title": "لوحة تحكم — المصروفات",
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
            "title": "فئات المصروفات",
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
                    f"تم إنشاء الفئة « {categorie.libelle} » بنجاح.",
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
                messages.error(request, f"خطأ أثناء الإنشاء: {exc}")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        form = CategorieDepenseForm()

    return render(
        request,
        "depenses/categorie_depense_form.html",
        {
            "form": form,
            "title": "فئة مصروف جديدة",
            "action_label": "إنشاء الفئة",
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
                    f"تم تحديث الفئة « {categorie.libelle} ».",
                )
                logger.info(
                    "CategorieDepense pk=%s updated by '%s'.",
                    categorie.pk,
                    request.user,
                )
                return redirect("depenses:categorie_depense_list")
            except Exception as exc:
                logger.exception("Error updating CategorieDepense pk=%s: %s", pk, exc)
                messages.error(request, f"خطأ أثناء التحديث: {exc}")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        form = CategorieDepenseForm(instance=categorie)

    return render(
        request,
        "depenses/categorie_depense_form.html",
        {
            "form": form,
            "categorie": categorie,
            "title": f"تعديل — {categorie.libelle}",
            "action_label": "حفظ التعديلات",
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
    state = "مفعَّلة" if categorie.actif else "معطَّلة"
    messages.success(request, f"الفئة « {categorie.libelle} » {state}.")
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

    v1.4 (BR-BRA-01/02): Vue par Branche shows only the active branche's
    dépenses; Vue Globale shows every branche combined.
    """
    branche = get_active_branche(request)

    qs = Depense.objects.select_related(
        "categorie", "lot", "branche", "enregistre_par"
    ).order_by("-date", "-created_at")
    if branche is not None:
        qs = qs.filter(branche=branche)

    filter_form = DepenseFilterForm(request.GET or None, branche=branche)
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
            "active_branche": branche,
            "title": "المصروفات التشغيلية",
        },
    )


# ===========================================================================
# Depense — Create
# ===========================================================================


@login_required(login_url=LOGIN_URL)
@require_branche_context
def depense_create(request):
    """
    Record a new operational expense.

    BR-DEP-03: the form already restricts facture_liee to Service-type
    invoices.  The view double-checks via model.clean() inside full_clean().
    BR-DEP-04: lot attribution is optional and purely informational here.
    BR-BRA-01/04: branche is a required, explicit field — pre-selected and
    locked to the request's active branche.

    v1.7: `?voyage=<pk>` pre-selects and locks the trip attribution — used
    by clients:voyage_create, which redirects here right after a new trip is
    saved so its transport cost gets logged immediately. On success, if the
    dépense is tied to a trip, redirect back to that trip's page instead of
    the dépense's own detail page.

    v1.8: `?lot=<pk>` pre-selects and locks the lot attribution — used by
    the production record form's "+ إضافة تكلفة يد عاملة" shortcut, so a
    production-labor cost can be logged against the lot at hand without
    leaving the harvest workflow. An optional `?next=<path>` (same-site
    only) sends the user back to that in-progress form afterwards instead
    of the dépense's own detail page; falls back to the voyage/detail
    redirect when absent.
    """
    branche = get_active_branche(request)

    voyage = None
    voyage_pk = request.POST.get("voyage") or request.GET.get("voyage")
    if voyage_pk:
        from clients.models import VoyageLivraison

        voyage = get_object_or_404(VoyageLivraison, pk=voyage_pk)

    lot = None
    lot_pk = request.POST.get("lot") or request.GET.get("lot")
    if lot_pk:
        from elevage.models import LotElevage

        lot = branche_object_or_404(request, LotElevage, pk=lot_pk)

    # Same-site-only redirect target, carried through GET/POST so it
    # survives the form round-trip (mirrors how `lot`/`voyage` are read
    # from either dict above).
    next_url = request.POST.get("next") or request.GET.get("next")
    if next_url and not url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        next_url = None

    if request.method == "POST":
        form = DepenseForm(
            request.POST, request.FILES, branche=branche, voyage=voyage, lot=lot
        )
        pj_formset = build_piece_jointe_formset(
            DepensePieceJointeFormSet, request, prefix="pj"
        )
        if form.is_valid() and pj_formset.is_valid():
            try:
                with transaction.atomic():
                    depense = form.save(commit=False)
                    depense.enregistre_par = request.user
                    # Trigger model-level clean() for BR-DEP-01/03 double-guard.
                    depense.full_clean()
                    depense.save()
                    pj_formset.instance = depense
                    pj_formset.save()

                messages.success(
                    request,
                    f"تم تسجيل المصروف « {depense.description[:60]} » ({depense.montant} دج).",
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
                if next_url:
                    return redirect(next_url)
                if depense.voyage_id:
                    return redirect("clients:voyage_detail", pk=depense.voyage_id)
                return redirect("depenses:depense_detail", pk=depense.pk)

            except Exception as exc:
                logger.exception("Error creating Depense: %s", exc)
                messages.error(request, f"خطأ أثناء التسجيل: {exc}")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        import datetime

        initial = {"date": datetime.date.today()}
        if voyage:
            initial["date"] = voyage.date_voyage
            initial["description"] = f"مصاريف رحلة توصيل {voyage.date_voyage}"
            # Best-effort: the "TRANSPORT" category is seeded by
            # seed_db_minimal (_seed_categories_depense) but may not exist
            # on every install — fall back silently if it's missing/renamed.
            transport_cat = CategorieDepense.objects.filter(
                code="TRANSPORT", actif=True
            ).first()
            if transport_cat:
                initial["categorie"] = transport_cat.pk
        elif lot:
            initial["description"] = f"يد عاملة — إنتاج الدفعة {lot.designation}"
            # Best-effort: "SALAIRES" is seeded by seed_db_minimal
            # (_seed_categories_depense) but may be missing/renamed.
            salaires_cat = CategorieDepense.objects.filter(
                code="SALAIRES", actif=True
            ).first()
            if salaires_cat:
                initial["categorie"] = salaires_cat.pk
        form = DepenseForm(initial=initial, branche=branche, voyage=voyage, lot=lot)
        pj_formset = build_piece_jointe_formset(
            DepensePieceJointeFormSet, request, prefix="pj"
        )

    return render(
        request,
        "depenses/depense_form.html",
        {
            "form": form,
            "pj_formset": pj_formset,
            "active_branche": branche,
            "title": "تسجيل مصروف",
            "action_label": "حفظ المصروف",
            "next": next_url,
        },
    )


# ===========================================================================
# Depense — Detail
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def depense_detail(request, pk):
    """v1.4 (BR-BRA-02): scoped to the request's active branche."""
    depense = branche_object_or_404(
        request,
        Depense.objects.select_related(
            "categorie", "lot", "branche", "facture_liee__fournisseur", "enregistre_par"
        ),
        pk=pk,
    )
    pieces_jointes = depense.pieces_jointes.select_related("uploaded_by").order_by(
        "-created_at"
    )
    return render(
        request,
        "depenses/depense_detail.html",
        {
            "depense": depense,
            "pieces_jointes": pieces_jointes,
            "title": f"مصروف — {depense.date} | {depense.categorie.libelle}",
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
    BR-BRA-02: the dépense must belong to the request's active branche;
    its branche field stays locked to that same branche on edit.
    """
    depense = branche_object_or_404(request, Depense, pk=pk)

    if request.method == "POST":
        form = DepenseForm(
            request.POST,
            request.FILES,
            instance=depense,
            branche=depense.branche,
            voyage=depense.voyage,
        )
        pj_formset = build_piece_jointe_formset(
            DepensePieceJointeFormSet, request, instance=depense, prefix="pj"
        )
        if form.is_valid() and pj_formset.is_valid():
            try:
                with transaction.atomic():
                    updated = form.save(commit=False)
                    # Re-run model-level validation on update.
                    updated.full_clean()
                    updated.save()
                    pj_formset.save()

                messages.success(
                    request,
                    f"تم تحديث المصروف « {depense.description[:60]} ».",
                )
                logger.info("Depense pk=%s updated by '%s'.", depense.pk, request.user)
                if updated.voyage_id:
                    return redirect("clients:voyage_detail", pk=updated.voyage_id)
                return redirect("depenses:depense_detail", pk=depense.pk)

            except Exception as exc:
                logger.exception("Error updating Depense pk=%s: %s", pk, exc)
                messages.error(request, f"خطأ أثناء التحديث: {exc}")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        form = DepenseForm(
            instance=depense, branche=depense.branche, voyage=depense.voyage
        )
        pj_formset = build_piece_jointe_formset(
            DepensePieceJointeFormSet, request, instance=depense, prefix="pj"
        )

    return render(
        request,
        "depenses/depense_form.html",
        {
            "form": form,
            "pj_formset": pj_formset,
            "depense": depense,
            "title": f"تعديل المصروف — {depense.date}",
            "action_label": "حفظ التعديلات",
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
    BR-BRA-02: scoped to the request's active branche.
    """
    depense = branche_object_or_404(request, Depense, pk=pk)
    description = depense.description[:60]
    date = depense.date
    try:
        depense.delete()
        messages.success(
            request,
            f"تم حذف المصروف « {description} » بتاريخ {date}.",
        )
        logger.info(
            "Depense pk=%s ('%s') deleted by '%s'.",
            pk,
            description,
            request.user,
        )
    except Exception as exc:
        logger.exception("Error deleting Depense pk=%s: %s", pk, exc)
        messages.error(request, f"خطأ أثناء الحذف: {exc}")
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
    BR-BRA-02: scoped to the request's active branche.
    """
    depense = branche_object_or_404(
        request,
        Depense.objects.select_related(
            "categorie", "lot", "branche", "facture_liee__fournisseur", "enregistre_par"
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


# ===========================================================================
# Associés — List
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def associe_list(request):
    qs = Associe.objects.annotate(total_retraits=Sum("retraits__montant")).order_by(
        "nom"
    )
    actif_param = request.GET.get("actif", "")
    if actif_param == "1":
        qs = qs.filter(actif=True)
    elif actif_param == "0":
        qs = qs.filter(actif=False)

    return render(
        request,
        "depenses/associe_list.html",
        {"associes": qs, "actif_param": actif_param, "title": "الشركاء"},
    )


# ===========================================================================
# Associés — Create / Edit
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def associe_create(request):
    if request.method == "POST":
        form = AssocieForm(request.POST)
        if form.is_valid():
            associe = form.save()
            messages.success(request, f"تم إنشاء الشريك « {associe.nom} ».")
            logger.info("Associe pk=%s created by '%s'.", associe.pk, request.user)
            return redirect("depenses:associe_detail", pk=associe.pk)
        messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        form = AssocieForm()

    return render(
        request,
        "depenses/associe_form.html",
        {"form": form, "title": "شريك جديد", "action_label": "إنشاء الشريك"},
    )


@login_required(login_url=LOGIN_URL)
def associe_edit(request, pk):
    associe = get_object_or_404(Associe, pk=pk)
    if request.method == "POST":
        form = AssocieForm(request.POST, instance=associe)
        if form.is_valid():
            form.save()
            messages.success(request, f"تم تحديث الشريك « {associe.nom} ».")
            logger.info("Associe pk=%s updated by '%s'.", associe.pk, request.user)
            return redirect("depenses:associe_detail", pk=associe.pk)
        messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        form = AssocieForm(instance=associe)

    return render(
        request,
        "depenses/associe_form.html",
        {
            "form": form,
            "associe": associe,
            "title": f"تعديل — {associe.nom}",
            "action_label": "حفظ التعديلات",
        },
    )


# ===========================================================================
# Associés — Detail (withdrawal history — BR-ASSOC-01)
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def associe_detail(request, pk):
    associe = get_object_or_404(Associe, pk=pk)
    retraits = associe.retraits.order_by("-date", "-created_at")
    page = _paginate(retraits, request.GET.get("page"))
    total = retraits.aggregate(total=Sum("montant"))["total"] or 0

    return render(
        request,
        "depenses/associe_detail.html",
        {
            "associe": associe,
            "page": page,
            "total": total,
            "title": f"الشريك — {associe.nom}",
        },
    )


# ===========================================================================
# Retraits associés — List
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def retrait_list(request):
    """List all stakeholder withdrawals (BR-ASSOC-01 history)."""
    qs = RetraitAssocie.objects.select_related("associe", "enregistre_par").order_by(
        "-date", "-created_at"
    )

    filter_form = RetraitFilterForm(request.GET or None)
    if filter_form.is_valid():
        cd = filter_form.cleaned_data
        if cd.get("associe"):
            qs = qs.filter(associe=cd["associe"])
        if cd.get("date_debut"):
            qs = qs.filter(date__gte=cd["date_debut"])
        if cd.get("date_fin"):
            qs = qs.filter(date__lte=cd["date_fin"])

    total = qs.aggregate(total=Sum("montant"))["total"] or 0
    page = _paginate(qs, request.GET.get("page"))

    return render(
        request,
        "depenses/retrait_list.html",
        {
            "page": page,
            "filter_form": filter_form,
            "total": total,
            "title": "سحوبات الشركاء",
        },
    )


# ===========================================================================
# Retraits associés — Create / Edit / Delete
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def retrait_create(request):
    """BR-ASSOC-02: always a manual entry."""
    if request.method == "POST":
        form = RetraitAssocieForm(request.POST, request.FILES)
        pj_formset = build_piece_jointe_formset(
            RetraitAssociePieceJointeFormSet, request, prefix="pj"
        )
        if form.is_valid() and pj_formset.is_valid():
            try:
                with transaction.atomic():
                    retrait = form.save(commit=False)
                    retrait.enregistre_par = request.user
                    retrait.full_clean()
                    retrait.save()
                    pj_formset.instance = retrait
                    pj_formset.save()
                messages.success(
                    request,
                    f"تم تسجيل سحب « {retrait.associe.nom} » ({retrait.montant} دج).",
                )
                logger.info(
                    "RetraitAssocie pk=%s created by '%s' (associe=%s, montant=%s).",
                    retrait.pk,
                    request.user,
                    retrait.associe.nom,
                    retrait.montant,
                )
                return redirect("depenses:associe_detail", pk=retrait.associe.pk)
            except Exception as exc:
                logger.exception("Error creating RetraitAssocie: %s", exc)
                messages.error(request, f"خطأ أثناء التسجيل: {exc}")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        form = RetraitAssocieForm(initial={"date": datetime.date.today()})
        pj_formset = build_piece_jointe_formset(
            RetraitAssociePieceJointeFormSet, request, prefix="pj"
        )

    return render(
        request,
        "depenses/retrait_form.html",
        {
            "form": form,
            "pj_formset": pj_formset,
            "title": "سحب جديد لشريك",
            "action_label": "حفظ السحب",
        },
    )


@login_required(login_url=LOGIN_URL)
def retrait_edit(request, pk):
    retrait = get_object_or_404(RetraitAssocie, pk=pk)
    if request.method == "POST":
        form = RetraitAssocieForm(request.POST, request.FILES, instance=retrait)
        pj_formset = build_piece_jointe_formset(
            RetraitAssociePieceJointeFormSet, request, instance=retrait, prefix="pj"
        )
        if form.is_valid() and pj_formset.is_valid():
            try:
                with transaction.atomic():
                    updated = form.save(commit=False)
                    updated.full_clean()
                    updated.save()
                    pj_formset.save()
                messages.success(request, "تم تحديث السحب.")
                logger.info(
                    "RetraitAssocie pk=%s updated by '%s'.", retrait.pk, request.user
                )
                return redirect("depenses:associe_detail", pk=retrait.associe.pk)
            except Exception as exc:
                logger.exception("Error updating RetraitAssocie pk=%s: %s", pk, exc)
                messages.error(request, f"خطأ أثناء التحديث: {exc}")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        form = RetraitAssocieForm(instance=retrait)
        pj_formset = build_piece_jointe_formset(
            RetraitAssociePieceJointeFormSet, request, instance=retrait, prefix="pj"
        )

    return render(
        request,
        "depenses/retrait_form.html",
        {
            "form": form,
            "pj_formset": pj_formset,
            "retrait": retrait,
            "title": "تعديل السحب",
            "action_label": "حفظ التعديلات",
        },
    )


@login_required(login_url=LOGIN_URL)
@require_POST
def retrait_delete(request, pk):
    retrait = get_object_or_404(RetraitAssocie, pk=pk)
    associe_pk = retrait.associe.pk
    try:
        retrait.delete()
        messages.success(request, "تم حذف السحب.")
        logger.info("RetraitAssocie pk=%s deleted by '%s'.", pk, request.user)
    except Exception as exc:
        logger.exception("Error deleting RetraitAssocie pk=%s: %s", pk, exc)
        messages.error(request, f"خطأ أثناء الحذف: {exc}")
    return redirect("depenses:associe_detail", pk=associe_pk)


# ===========================================================================
# RH — Dashboard
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def rh_dashboard(request):
    """
    Payroll/advances summary for a period (defaults to current month).

    v1.4 (§3.5.5 / BR-BRA-09): Vue par Branche shows only the active
    branche's employees and payroll figures (employee branche is derived
    from `employe.batiment.branche`); Vue Globale aggregates across every
    branche.
    """
    branche = get_active_branche(request)
    today = datetime.date.today()
    premier_du_mois = today.replace(day=1)

    filter_form = DashboardFilterForm(request.GET or None)
    if filter_form.is_valid():
        date_debut = filter_form.cleaned_data.get("date_debut") or premier_du_mois
        date_fin = filter_form.cleaned_data.get("date_fin") or today
    else:
        date_debut, date_fin = premier_du_mois, today

    summary = get_rh_summary(date_debut=date_debut, date_fin=date_fin, branche=branche)
    summary.update(get_dettes_summary(branche=branche))

    bulletins_recents_qs = BulletinPaie.objects.select_related("employe")
    acomptes_recents_qs = AcompteEmploye.objects.select_related("employe")
    if branche is not None:
        bulletins_recents_qs = bulletins_recents_qs.filter(
            employe__batiment__branche=branche
        )
        acomptes_recents_qs = acomptes_recents_qs.filter(
            employe__batiment__branche=branche
        )
    bulletins_recents = bulletins_recents_qs.order_by("-annee", "-mois", "-updated_at")[
        :10
    ]
    acomptes_recents = acomptes_recents_qs.order_by("-date")[:10]

    return render(
        request,
        "depenses/rh_dashboard.html",
        {
            "filter_form": filter_form,
            "date_debut": date_debut,
            "date_fin": date_fin,
            "summary": summary,
            "bulletins_recents": bulletins_recents,
            "acomptes_recents": acomptes_recents,
            "active_branche": branche,
            "title": "لوحة تحكم — الموارد البشرية",
        },
    )


# ===========================================================================
# RH — Employés
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def employe_list(request):
    """
    v1.4 (§3.5.5 / BR-BRA-09): Vue par Branche shows only the active
    branche's employees (derived from `batiment__branche`); Vue Globale
    shows every branche combined.
    """
    branche = get_active_branche(request)
    qs = Employe.objects.select_related("batiment", "binome").order_by("nom_complet")
    if branche is not None:
        qs = qs.filter(batiment__branche=branche)
    actif_param = request.GET.get("actif", "")
    if actif_param == "1":
        qs = qs.filter(actif=True)
    elif actif_param == "0":
        qs = qs.filter(actif=False)

    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(
            Q(nom_complet__icontains=q)
            | Q(matricule__icontains=q)
            | Q(fonction__icontains=q)
        )

    return render(
        request,
        "depenses/employe_list.html",
        {
            "employes": qs,
            "actif_param": actif_param,
            "q": q,
            "active_branche": branche,
            "title": "العمال",
        },
    )


@login_required(login_url=LOGIN_URL)
def employe_create(request):
    """
    BR-BRA-09: Employe.branche is derived from `batiment`; pass the active
    branche to EmployeForm so the batiment/binome pickers stay within it
    for a locked chef de branche/opérateur. Vue Globale leaves them
    unrestricted, so an admin may assign any branche's batiment.
    """
    branche = get_active_branche(request)
    if request.method == "POST":
        form = EmployeForm(request.POST, branche=branche)
        if form.is_valid():
            try:
                with transaction.atomic():
                    employe = form.save(commit=False)
                    employe.full_clean()
                    employe.save()
                messages.success(request, f"تم تسجيل العامل « {employe.nom_complet} ».")
                logger.info("Employe pk=%s created by '%s'.", employe.pk, request.user)
                return redirect("depenses:employe_detail", pk=employe.pk)
            except Exception as exc:
                logger.exception("Error creating Employe: %s", exc)
                messages.error(request, f"خطأ أثناء التسجيل: {exc}")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        form = EmployeForm(branche=branche)

    return render(
        request,
        "depenses/employe_form.html",
        {
            "form": form,
            "active_branche": branche,
            "title": "عامل جديد",
            "action_label": "تسجيل العامل",
        },
    )


@login_required(login_url=LOGIN_URL)
def employe_edit(request, pk):
    """BR-BRA-02/09: scoped to the request's active branche (derived)."""
    employe = get_object_or_404(Employe, pk=pk)
    _ensure_employe_branche_access(request, employe)
    branche = get_active_branche(request)
    if request.method == "POST":
        form = EmployeForm(request.POST, instance=employe, branche=branche)
        if form.is_valid():
            try:
                with transaction.atomic():
                    updated = form.save(commit=False)
                    updated.full_clean()
                    updated.save()
                messages.success(request, f"تم تحديث « {employe.nom_complet} ».")
                logger.info("Employe pk=%s updated by '%s'.", employe.pk, request.user)
                return redirect("depenses:employe_detail", pk=employe.pk)
            except Exception as exc:
                logger.exception("Error updating Employe pk=%s: %s", pk, exc)
                messages.error(request, f"خطأ أثناء التحديث: {exc}")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        form = EmployeForm(instance=employe, branche=branche)

    return render(
        request,
        "depenses/employe_form.html",
        {
            "form": form,
            "employe": employe,
            "title": f"تعديل — {employe.nom_complet}",
            "action_label": "حفظ التعديلات",
        },
    )


@login_required(login_url=LOGIN_URL)
def employe_detail(request, pk):
    """
    Employee profile: leave balance, recent attendance, advances, payslips.
    BR-BRA-02/09: scoped to the request's active branche (derived).
    """
    employe = get_object_or_404(
        Employe.objects.select_related("batiment", "binome"), pk=pk
    )
    _ensure_employe_branche_access(request, employe)
    solde_conge = get_solde_conge(employe)
    pointages_recents = employe.pointages.order_by("-date")[:31]
    acomptes_en_attente = employe.acomptes.filter(bulletin_paie__isnull=True).order_by(
        "-date"
    )
    bulletins_recents = employe.bulletins_paie.order_by("-annee", "-mois")[:12]

    return render(
        request,
        "depenses/employe_detail.html",
        {
            "employe": employe,
            "solde_conge": solde_conge,
            "pointages_recents": pointages_recents,
            "acomptes_en_attente": acomptes_en_attente,
            "bulletins_recents": bulletins_recents,
            "title": f"العامل — {employe.nom_complet}",
        },
    )


# ===========================================================================
# RH — Pointage
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def pointage_list(request):
    """
    v1.4 (§3.5.5 / BR-BRA-09): Vue par Branche shows only the active
    branche's attendance (derived via `employe__batiment__branche`); Vue
    Globale shows every branche combined.
    """
    branche = get_active_branche(request)
    qs = Pointage.objects.select_related("employe").order_by(
        "-date", "employe__nom_complet"
    )
    if branche is not None:
        qs = qs.filter(employe__batiment__branche=branche)

    filter_form = PointageFilterForm(request.GET or None, branche=branche)
    if filter_form.is_valid():
        cd = filter_form.cleaned_data
        if cd.get("employe"):
            qs = qs.filter(employe=cd["employe"])
        if cd.get("date_debut"):
            qs = qs.filter(date__gte=cd["date_debut"])
        if cd.get("date_fin"):
            qs = qs.filter(date__lte=cd["date_fin"])
        if cd.get("statut"):
            qs = qs.filter(statut=cd["statut"])

    page = _paginate(qs, request.GET.get("page"))

    return render(
        request,
        "depenses/pointage_list.html",
        {
            "page": page,
            "filter_form": filter_form,
            "active_branche": branche,
            "title": "تسجيلات الحضور",
        },
    )


@login_required(login_url=LOGIN_URL)
def pointage_create(request):
    """BR-BRA-09: scope the employe picker to the request's active branche."""
    branche = get_active_branche(request)
    if request.method == "POST":
        form = PointageForm(request.POST, branche=branche)
        if form.is_valid():
            try:
                with transaction.atomic():
                    pointage = form.save(commit=False)
                    pointage.full_clean()
                    pointage.save()
                messages.success(
                    request,
                    f"تم تسجيل الحضور — {pointage.employe.nom_complet} — {pointage.date}.",
                )
                logger.info(
                    "Pointage pk=%s created by '%s'.", pointage.pk, request.user
                )
                return redirect("depenses:pointage_list")
            except Exception as exc:
                logger.exception("Error creating Pointage: %s", exc)
                messages.error(request, f"خطأ أثناء التسجيل: {exc}")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        form = PointageForm(initial={"date": datetime.date.today()}, branche=branche)

    return render(
        request,
        "depenses/pointage_form.html",
        {
            "form": form,
            "active_branche": branche,
            "title": "تسجيل حضور",
            "action_label": "حفظ",
        },
    )


@login_required(login_url=LOGIN_URL)
def pointage_edit(request, pk):
    """BR-BRA-02/09: scoped to the request's active branche (derived)."""
    pointage = get_object_or_404(Pointage.objects.select_related("employe"), pk=pk)
    _ensure_employe_branche_access(request, pointage)
    branche = get_active_branche(request)
    if request.method == "POST":
        form = PointageForm(request.POST, instance=pointage, branche=branche)
        if form.is_valid():
            try:
                with transaction.atomic():
                    updated = form.save(commit=False)
                    updated.full_clean()
                    updated.save()
                messages.success(request, "تم تحديث تسجيل الحضور.")
                logger.info(
                    "Pointage pk=%s updated by '%s'.", pointage.pk, request.user
                )
                return redirect("depenses:pointage_list")
            except Exception as exc:
                logger.exception("Error updating Pointage pk=%s: %s", pk, exc)
                messages.error(request, f"خطأ أثناء التحديث: {exc}")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        form = PointageForm(instance=pointage, branche=branche)

    return render(
        request,
        "depenses/pointage_form.html",
        {
            "form": form,
            "pointage": pointage,
            "active_branche": branche,
            "title": "تعديل تسجيل الحضور",
            "action_label": "حفظ التعديلات",
        },
    )


# ===========================================================================
# RH — Jours fériés / cérémoniels (BR-RH-07)
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def jour_ferie_list(request):
    """
    List + inline-create ceremonial/holiday dates. Company-wide, not
    branche-scoped (like Associe) — a ceremonial day applies to everyone.
    """
    if request.method == "POST":
        form = JourFerieForm(request.POST)
        if form.is_valid():
            jour = form.save()
            messages.success(
                request, f"تمت إضافة يوم « {jour.nom} — {jour.date} » بنجاح."
            )
            logger.info("JourFerie pk=%s created by '%s'.", jour.pk, request.user)
            return redirect("depenses:jour_ferie_list")
        messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        form = JourFerieForm()

    jours = JourFerie.objects.order_by("-date")
    return render(
        request,
        "depenses/jour_ferie_list.html",
        {
            "form": form,
            "jours": jours,
            "title": "أيام الأعياد والاحتفالات",
        },
    )


@login_required(login_url=LOGIN_URL)
@require_POST
def jour_ferie_toggle_active(request, pk):
    jour = get_object_or_404(JourFerie, pk=pk)
    jour.actif = not jour.actif
    jour.save(update_fields=["actif"])
    state = "مفعَّل" if jour.actif else "معطَّل"
    messages.success(request, f"يوم « {jour.nom} » {state}.")
    return redirect("depenses:jour_ferie_list")


@login_required(login_url=LOGIN_URL)
@require_POST
def jour_ferie_delete(request, pk):
    jour = get_object_or_404(JourFerie, pk=pk)
    nom = f"{jour.nom} — {jour.date}"
    jour.delete()
    messages.success(request, f"تم حذف « {nom} ».")
    logger.info("JourFerie '%s' deleted by '%s'.", nom, request.user)
    return redirect("depenses:jour_ferie_list")


# ===========================================================================
# RH — Congés
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def conge_list(request):
    """
    v1.4 (§3.5.5 / BR-BRA-09): Vue par Branche shows only the active
    branche's leave records (derived via `employe__batiment__branche`);
    Vue Globale shows every branche combined.
    """
    branche = get_active_branche(request)
    qs = CongeEmploye.objects.select_related("employe").order_by("-date_debut")
    if branche is not None:
        qs = qs.filter(employe__batiment__branche=branche)
    employe_id = request.GET.get("employe")
    if employe_id:
        qs = qs.filter(employe_id=employe_id)

    page = _paginate(qs, request.GET.get("page"))
    return render(
        request,
        "depenses/conge_list.html",
        {"page": page, "active_branche": branche, "title": "عطل العمال"},
    )


@login_required(login_url=LOGIN_URL)
def conge_create(request):
    """
    Record a paid-leave block (BR-RH-03) and immediately materialize it
    into Pointage rows (BR-RH-05) so payroll calculation sees it.

    BR-BRA-09: scope the employe picker to the request's active branche.
    """
    branche = get_active_branche(request)
    if request.method == "POST":
        form = CongeEmployeForm(request.POST, branche=branche)
        if form.is_valid():
            try:
                with transaction.atomic():
                    conge = form.save(commit=False)
                    conge.full_clean()
                    conge.save()
                    nb_jours_appliques = appliquer_conge_aux_pointages(conge)

                messages.success(
                    request,
                    f"تم تسجيل عطلة « {conge.employe.nom_complet} » "
                    f"({conge.nb_jours} يوم) وتطبيقها على {nb_jours_appliques} تسجيل حضور.",
                )
                logger.info(
                    "CongeEmploye pk=%s created by '%s' (employe=%s, jours=%s).",
                    conge.pk,
                    request.user,
                    conge.employe.matricule,
                    conge.nb_jours,
                )
                return redirect("depenses:employe_detail", pk=conge.employe.pk)
            except Exception as exc:
                logger.exception("Error creating CongeEmploye: %s", exc)
                messages.error(request, f"خطأ أثناء التسجيل: {exc}")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        form = CongeEmployeForm(branche=branche)

    return render(
        request,
        "depenses/conge_form.html",
        {
            "form": form,
            "active_branche": branche,
            "title": "عطلة جديدة",
            "action_label": "حفظ",
        },
    )


# ===========================================================================
# RH — Acomptes employés (BR-RH-04)
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def acompte_employe_list(request):
    """
    v1.4 (§3.5.5 / BR-BRA-09): Vue par Branche shows only the active
    branche's salary advances; Vue Globale shows every branche combined.
    """
    branche = get_active_branche(request)
    qs = AcompteEmploye.objects.select_related("employe", "bulletin_paie").order_by(
        "-date"
    )
    if branche is not None:
        qs = qs.filter(employe__batiment__branche=branche)
    employe_id = request.GET.get("employe")
    if employe_id:
        qs = qs.filter(employe_id=employe_id)

    total = qs.aggregate(total=Sum("montant"))["total"] or 0
    page = _paginate(qs, request.GET.get("page"))

    return render(
        request,
        "depenses/acompte_employe_list.html",
        {
            "page": page,
            "total": total,
            "active_branche": branche,
            "title": "تسبيقات على الرواتب",
        },
    )


@login_required(login_url=LOGIN_URL)
def acompte_employe_create(request):
    """BR-BRA-09: scope the employe picker to the request's active branche."""
    branche = get_active_branche(request)
    if request.method == "POST":
        form = AcompteEmployeForm(request.POST, branche=branche)
        pj_formset = build_piece_jointe_formset(
            AcompteEmployePieceJointeFormSet, request, prefix="pj"
        )
        if form.is_valid() and pj_formset.is_valid():
            try:
                with transaction.atomic():
                    acompte = form.save(commit=False)
                    acompte.enregistre_par = request.user
                    acompte.full_clean()
                    acompte.save()
                    pj_formset.instance = acompte
                    pj_formset.save()
                messages.success(
                    request,
                    f"تم تسجيل تسبيق « {acompte.employe.nom_complet} » ({acompte.montant} دج).",
                )
                logger.info(
                    "AcompteEmploye pk=%s created by '%s'.", acompte.pk, request.user
                )
                return redirect("depenses:employe_detail", pk=acompte.employe.pk)
            except Exception as exc:
                logger.exception("Error creating AcompteEmploye: %s", exc)
                messages.error(request, f"خطأ أثناء التسجيل: {exc}")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        form = AcompteEmployeForm(
            initial={"date": datetime.date.today()}, branche=branche
        )
        pj_formset = build_piece_jointe_formset(
            AcompteEmployePieceJointeFormSet, request, prefix="pj"
        )

    return render(
        request,
        "depenses/acompte_employe_form.html",
        {
            "form": form,
            "pj_formset": pj_formset,
            "active_branche": branche,
            "title": "تسبيق جديد",
            "action_label": "حفظ",
        },
    )


@login_required(login_url=LOGIN_URL)
@require_POST
def acompte_employe_ajouter_piece_jointe(request, pk):
    """AcompteEmploye has no edit view — attach proof after the fact here."""
    acompte = get_object_or_404(AcompteEmploye.objects.select_related("employe"), pk=pk)
    _ensure_employe_branche_access(request, acompte)
    pj_formset = build_piece_jointe_formset(
        AcompteEmployePieceJointeFormSet, request, instance=acompte, prefix="pj"
    )
    if pj_formset.is_valid():
        pj_formset.save()
        messages.success(request, "تم إضافة المرفقات.")
    else:
        messages.error(request, "يرجى تصحيح الأخطاء في المرفقات.")
    return redirect("depenses:employe_detail", pk=acompte.employe.pk)


# ===========================================================================
# RH — Dettes employés (DetteEmploye)  (BR-BRA-09)
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def dette_employe_list(request):
    """
    v1.4 (§3.5.5 / BR-BRA-09): Vue par Branche shows only the active
    branche's employee debts; Vue Globale shows every branche combined.
    """
    branche = get_active_branche(request)
    qs = (
        DetteEmploye.objects.select_related("employe")
        .prefetch_related("remboursements")
        .order_by("-date")
    )
    if branche is not None:
        qs = qs.filter(employe__batiment__branche=branche)
    employe_id = request.GET.get("employe")
    if employe_id:
        qs = qs.filter(employe_id=employe_id)

    dettes = list(qs)
    total_montant = sum((d.montant for d in dettes), Decimal("0.00"))
    total_restant = sum((d.montant_restant for d in dettes), Decimal("0.00"))
    page = _paginate(dettes, request.GET.get("page"))

    return render(
        request,
        "depenses/dette_employe_list.html",
        {
            "page": page,
            "total_montant": total_montant,
            "total_restant": total_restant,
            "active_branche": branche,
            "title": "ديون العمال",
        },
    )


@login_required(login_url=LOGIN_URL)
def dette_employe_create(request):
    """BR-BRA-09: scope the employe picker to the request's active branche."""
    branche = get_active_branche(request)
    if request.method == "POST":
        form = DetteEmployeForm(request.POST, branche=branche)
        pj_formset = build_piece_jointe_formset(
            DetteEmployePieceJointeFormSet, request, prefix="pj"
        )
        if form.is_valid() and pj_formset.is_valid():
            try:
                with transaction.atomic():
                    dette = form.save(commit=False)
                    dette.enregistre_par = request.user
                    dette.full_clean()
                    dette.save()
                    pj_formset.instance = dette
                    pj_formset.save()
                messages.success(
                    request,
                    f"تم تسجيل دين « {dette.employe.nom_complet} » ({dette.montant} دج).",
                )
                logger.info(
                    "DetteEmploye pk=%s created by '%s'.", dette.pk, request.user
                )
                return redirect("depenses:employe_detail", pk=dette.employe.pk)
            except Exception as exc:
                logger.exception("Error creating DetteEmploye: %s", exc)
                messages.error(request, f"خطأ أثناء التسجيل: {exc}")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        form = DetteEmployeForm(
            initial={"date": datetime.date.today()}, branche=branche
        )
        pj_formset = build_piece_jointe_formset(
            DetteEmployePieceJointeFormSet, request, prefix="pj"
        )

    return render(
        request,
        "depenses/dette_employe_form.html",
        {
            "form": form,
            "pj_formset": pj_formset,
            "active_branche": branche,
            "title": "دين جديد",
            "action_label": "حفظ",
        },
    )


@login_required(login_url=LOGIN_URL)
@require_POST
def dette_employe_ajouter_piece_jointe(request, pk):
    """DetteEmploye has no edit view — attach proof after the fact here."""
    dette = get_object_or_404(DetteEmploye.objects.select_related("employe"), pk=pk)
    _ensure_employe_branche_access(request, dette)
    pj_formset = build_piece_jointe_formset(
        DetteEmployePieceJointeFormSet, request, instance=dette, prefix="pj"
    )
    if pj_formset.is_valid():
        pj_formset.save()
        messages.success(request, "تم إضافة المرفقات.")
    else:
        messages.error(request, "يرجى تصحيح الأخطاء في المرفقات.")
    return redirect("depenses:employe_detail", pk=dette.employe.pk)


# ===========================================================================
# RH — Bulletins de paie  (BR-RH-02 / BR-RH-05)
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def bulletin_paie_list(request):
    """
    v1.4 (§3.5.5 / BR-BRA-09): Vue par Branche shows only the active
    branche's payslips; Vue Globale shows every branche combined.
    """
    branche = get_active_branche(request)
    qs = BulletinPaie.objects.select_related("employe").order_by("-annee", "-mois")
    if branche is not None:
        qs = qs.filter(employe__batiment__branche=branche)

    filter_form = RHFilterForm(request.GET or None, branche=branche)
    if filter_form.is_valid():
        cd = filter_form.cleaned_data
        if cd.get("employe"):
            qs = qs.filter(employe=cd["employe"])
        if cd.get("annee"):
            qs = qs.filter(annee=cd["annee"])
        if cd.get("mois"):
            qs = qs.filter(mois=cd["mois"])

    page = _paginate(qs, request.GET.get("page"))
    return render(
        request,
        "depenses/bulletin_paie_list.html",
        {
            "page": page,
            "filter_form": filter_form,
            "active_branche": branche,
            "title": "كشوف الرواتب",
        },
    )


@login_required(login_url=LOGIN_URL)
def bulletin_paie_generer(request):
    """
    (Re)compute a payslip from Pointage for employe+annee+mois and persist
    the snapshot. Only allowed while the payslip is still 'brouillon' (or
    does not exist yet) — once 'valide' or 'paye' it is locked (BR-RH-05).

    BR-BRA-09: scope the employe picker to the request's active branche.
    """
    branche = get_active_branche(request)
    if request.method == "POST":
        form = GenererBulletinPaieForm(request.POST, branche=branche)
        if form.is_valid():
            employe = form.cleaned_data["employe"]
            annee = form.cleaned_data["annee"]
            mois = form.cleaned_data["mois"]

            existant = BulletinPaie.objects.filter(
                employe=employe, annee=annee, mois=mois
            ).first()
            if existant and existant.statut != BulletinPaie.STATUT_BROUILLON:
                messages.error(
                    request,
                    "لا يمكن إعادة حساب كشف راتب مصادق عليه أو مدفوع. "
                    "هذا الكشف موجود ومؤكد بالفعل.",
                )
                return redirect("depenses:bulletin_paie_detail", pk=existant.pk)

            try:
                with transaction.atomic():
                    # Unlink any acomptes from a previous draft computation
                    # for this exact payslip so re-generation is idempotent.
                    if existant:
                        existant.acomptes_deduits.update(bulletin_paie=None)

                    donnees = calculer_donnees_paie(employe, annee, mois)
                    acomptes_a_deduire = donnees.pop("acomptes_a_deduire")

                    # Recomputing attendance shouldn't wipe out debt
                    # repayments already entered manually on this draft —
                    # keep total_dettes and re-net against the fresh brut.
                    total_dettes_existant = (
                        existant.total_dettes if existant else Decimal("0.00")
                    )
                    donnees["total_dettes"] = total_dettes_existant
                    donnees["montant_net"] = (
                        donnees["montant_net"] - total_dettes_existant
                    )

                    bulletin, _created = BulletinPaie.objects.update_or_create(
                        employe=employe,
                        annee=annee,
                        mois=mois,
                        defaults={
                            **donnees,
                            "statut": BulletinPaie.STATUT_BROUILLON,
                            "genere_par": request.user,
                        },
                    )
                    acomptes_a_deduire.update(bulletin_paie=bulletin)

                messages.success(
                    request,
                    f"تم حساب كشف الراتب « {employe.nom_complet} — "
                    f"{mois:02d}/{annee} » — صافي: {bulletin.montant_net} دج.",
                )
                logger.info(
                    "BulletinPaie pk=%s (re)generated by '%s' (employe=%s, %02d/%s).",
                    bulletin.pk,
                    request.user,
                    employe.matricule,
                    mois,
                    annee,
                )
                return redirect("depenses:bulletin_paie_detail", pk=bulletin.pk)
            except Exception as exc:
                logger.exception("Error generating BulletinPaie: %s", exc)
                messages.error(request, f"خطأ أثناء الحساب: {exc}")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        form = GenererBulletinPaieForm(
            initial={
                "annee": datetime.date.today().year,
                "mois": datetime.date.today().month,
            },
            branche=branche,
        )

    return render(
        request,
        "depenses/bulletin_paie_generer.html",
        {
            "form": form,
            "active_branche": branche,
            "title": "حساب كشف راتب",
            "action_label": "حساب",
        },
    )


@login_required(login_url=LOGIN_URL)
def bulletin_paie_detail(request, pk):
    """BR-BRA-02/09: scoped to the request's active branche (derived)."""
    bulletin = get_object_or_404(
        BulletinPaie.objects.select_related("employe", "genere_par"), pk=pk
    )
    _ensure_employe_branche_access(request, bulletin)
    acomptes = bulletin.acomptes_deduits.order_by("-date")
    pieces_jointes = bulletin.pieces_jointes.select_related("uploaded_by").order_by(
        "-created_at"
    )
    remboursements_dettes = bulletin.remboursements_dettes.select_related(
        "dette"
    ).order_by("-created_at")
    # Manual debt repayment can only be added/removed while the payslip is
    # still a draft (BR-RH-05 — locked figures once validated).
    remboursement_form = None
    if bulletin.statut == BulletinPaie.STATUT_BROUILLON:
        remboursement_form = RemboursementDetteForm(employe=bulletin.employe)
    return render(
        request,
        "depenses/bulletin_paie_detail.html",
        {
            "bulletin": bulletin,
            "acomptes": acomptes,
            "pieces_jointes": pieces_jointes,
            "remboursements_dettes": remboursements_dettes,
            "remboursement_form": remboursement_form,
            "title": f"كشف راتب — {bulletin.employe.nom_complet} — {bulletin.periode_label}",
        },
    )


@login_required(login_url=LOGIN_URL)
@require_POST
def bulletin_paie_ajouter_remboursement_dette(request, pk):
    """
    Manually deduct an amount from one of the employee's debts on this
    (still-draft) payslip. Updates BulletinPaie.total_dettes and
    montant_net accordingly. BR-BRA-02/09 scoped.
    """
    bulletin = get_object_or_404(BulletinPaie.objects.select_related("employe"), pk=pk)
    _ensure_employe_branche_access(request, bulletin)
    if bulletin.statut != BulletinPaie.STATUT_BROUILLON:
        messages.error(
            request, "لا يمكن تعديل خصومات الديون إلا على كشف في حالة مسودة."
        )
        return redirect("depenses:bulletin_paie_detail", pk=pk)

    form = RemboursementDetteForm(request.POST, employe=bulletin.employe)
    if form.is_valid():
        try:
            with transaction.atomic():
                remboursement = form.save(commit=False)
                remboursement.bulletin_paie = bulletin
                remboursement.enregistre_par = request.user
                remboursement.full_clean()
                remboursement.save()

                bulletin.total_dettes = bulletin.total_dettes + remboursement.montant
                bulletin.montant_net = bulletin.montant_net - remboursement.montant
                bulletin.save(
                    update_fields=["total_dettes", "montant_net", "updated_at"]
                )
            messages.success(
                request,
                f"تم خصم {remboursement.montant} دج من دين « "
                f"{remboursement.dette.employe.nom_complet} ».",
            )
            logger.info(
                "RemboursementDette pk=%s added to BulletinPaie pk=%s by '%s'.",
                remboursement.pk,
                pk,
                request.user,
            )
        except Exception as exc:
            logger.exception("Error adding RemboursementDette: %s", exc)
            messages.error(request, f"خطأ أثناء الخصم: {exc}")
    else:
        for err in form.non_field_errors():
            messages.error(request, err)
        for field, errs in form.errors.items():
            if field == "__all__":
                continue
            for err in errs:
                messages.error(request, err)
    return redirect("depenses:bulletin_paie_detail", pk=pk)


@login_required(login_url=LOGIN_URL)
@require_POST
def bulletin_paie_retirer_remboursement_dette(request, pk, remboursement_pk):
    """Undo a manual debt-repayment installment from a draft payslip."""
    bulletin = get_object_or_404(BulletinPaie.objects.select_related("employe"), pk=pk)
    _ensure_employe_branche_access(request, bulletin)
    if bulletin.statut != BulletinPaie.STATUT_BROUILLON:
        messages.error(
            request, "لا يمكن تعديل خصومات الديون إلا على كشف في حالة مسودة."
        )
        return redirect("depenses:bulletin_paie_detail", pk=pk)

    remboursement = get_object_or_404(
        RemboursementDette, pk=remboursement_pk, bulletin_paie=bulletin
    )
    with transaction.atomic():
        montant = remboursement.montant
        remboursement.delete()
        bulletin.total_dettes = bulletin.total_dettes - montant
        bulletin.montant_net = bulletin.montant_net + montant
        bulletin.save(update_fields=["total_dettes", "montant_net", "updated_at"])
    messages.success(request, "تم إلغاء خصم الدين.")
    logger.info(
        "RemboursementDette pk=%s removed from BulletinPaie pk=%s by '%s'.",
        remboursement_pk,
        pk,
        request.user,
    )
    return redirect("depenses:bulletin_paie_detail", pk=pk)


@login_required(login_url=LOGIN_URL)
@require_POST
def bulletin_paie_valider(request, pk):
    """Lock a draft payslip's figures (brouillon → valide). BR-BRA-02/09 scoped."""
    bulletin = get_object_or_404(BulletinPaie.objects.select_related("employe"), pk=pk)
    _ensure_employe_branche_access(request, bulletin)
    if bulletin.statut != BulletinPaie.STATUT_BROUILLON:
        messages.error(request, "هذا الكشف ليس في حالة مسودة.")
        return redirect("depenses:bulletin_paie_detail", pk=pk)

    bulletin.statut = BulletinPaie.STATUT_VALIDE
    bulletin.save(update_fields=["statut", "updated_at"])
    messages.success(request, f"تم تأكيد كشف الراتب « {bulletin.periode_label} ».")
    logger.info("BulletinPaie pk=%s validated by '%s'.", pk, request.user)
    return redirect("depenses:bulletin_paie_detail", pk=pk)


@login_required(login_url=LOGIN_URL)
def bulletin_paie_payer(request, pk):
    """Mark a validated payslip as paid (valide → paye). BR-BRA-02/09 scoped."""
    bulletin = get_object_or_404(BulletinPaie.objects.select_related("employe"), pk=pk)
    _ensure_employe_branche_access(request, bulletin)
    if bulletin.statut != BulletinPaie.STATUT_VALIDE:
        messages.error(request, "يجب تأكيد الكشف أولاً قبل تسجيل الدفع.")
        return redirect("depenses:bulletin_paie_detail", pk=pk)

    if request.method == "POST":
        form = BulletinPaiementForm(request.POST, instance=bulletin)
        pj_formset = build_piece_jointe_formset(
            BulletinPaiePieceJointeFormSet, request, instance=bulletin, prefix="pj"
        )
        if form.is_valid() and pj_formset.is_valid():
            paye = form.save(commit=False)
            paye.statut = BulletinPaie.STATUT_PAYE
            paye.save()
            pj_formset.save()
            messages.success(
                request,
                f"تم تسجيل دفع « {bulletin.periode_label} » ({bulletin.montant_net} دج).",
            )
            logger.info("BulletinPaie pk=%s marked paid by '%s'.", pk, request.user)
            return redirect("depenses:bulletin_paie_detail", pk=pk)
        messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        form = BulletinPaiementForm(
            instance=bulletin, initial={"date_paiement": datetime.date.today()}
        )
        pj_formset = build_piece_jointe_formset(
            BulletinPaiePieceJointeFormSet, request, instance=bulletin, prefix="pj"
        )

    return render(
        request,
        "depenses/bulletin_paie_payer.html",
        {
            "form": form,
            "pj_formset": pj_formset,
            "bulletin": bulletin,
            "title": "تسجيل دفع الراتب",
        },
    )


@login_required(login_url=LOGIN_URL)
@require_POST
def bulletin_paie_repasser_brouillon(request, pk):
    """
    Admin-only escape hatch: force a 'valide' or 'paye' payslip back to
    'brouillon' so its figures can be recomputed/edited again. Bypasses
    the normal one-way brouillon → valide → paye flow (BR-RH-05).
    """
    bulletin = get_object_or_404(BulletinPaie.objects.select_related("employe"), pk=pk)
    _ensure_employe_branche_access(request, bulletin)
    if not _is_admin(request):
        messages.error(request, "هذا الإجراء متاح للمدير فقط.")
        return redirect("depenses:bulletin_paie_detail", pk=pk)
    if bulletin.statut == BulletinPaie.STATUT_BROUILLON:
        messages.error(request, "هذا الكشف في حالة مسودة أصلاً.")
        return redirect("depenses:bulletin_paie_detail", pk=pk)

    ancien_statut = bulletin.get_statut_display()
    bulletin.statut = BulletinPaie.STATUT_BROUILLON
    bulletin.save(update_fields=["statut", "updated_at"])
    messages.success(
        request,
        f"تم إرجاع كشف « {bulletin.periode_label} » من « {ancien_statut} » إلى مسودة.",
    )
    logger.info(
        "BulletinPaie pk=%s reverted to brouillon by admin '%s'.", pk, request.user
    )
    return redirect("depenses:bulletin_paie_detail", pk=pk)


@login_required(login_url=LOGIN_URL)
@require_POST
def bulletin_paie_delete(request, pk):
    """
    Admin-only hard delete, allowed regardless of statut (brouillon,
    valide, or payé). Unlinks acomptes (SET_NULL) and cascades
    remboursements_dettes, per BulletinPaie's FK definitions.
    """
    bulletin = get_object_or_404(BulletinPaie.objects.select_related("employe"), pk=pk)
    _ensure_employe_branche_access(request, bulletin)
    if not _is_admin(request):
        messages.error(request, "هذا الإجراء متاح للمدير فقط.")
        return redirect("depenses:bulletin_paie_detail", pk=pk)

    periode = bulletin.periode_label
    employe_nom = bulletin.employe.nom_complet
    try:
        with transaction.atomic():
            bulletin.delete()
        messages.success(
            request, f"تم حذف كشف راتب « {employe_nom} — {periode} » نهائياً."
        )
        logger.info(
            "BulletinPaie pk=%s ('%s' — %s) deleted by admin '%s'.",
            pk,
            employe_nom,
            periode,
            request.user,
        )
    except Exception as exc:
        logger.exception("Error deleting BulletinPaie pk=%s: %s", pk, exc)
        messages.error(request, f"خطأ أثناء الحذف: {exc}")
        return redirect("depenses:bulletin_paie_detail", pk=pk)

    return redirect("depenses:bulletin_paie_list")


@login_required(login_url=LOGIN_URL)
def bulletin_paie_print(request, pk):
    """Print-optimised payslip voucher — no PDF library, @media print CSS.
    BR-BRA-02/09: scoped to the request's active branche (derived)."""
    bulletin = get_object_or_404(
        BulletinPaie.objects.select_related("employe", "employe__batiment"), pk=pk
    )
    _ensure_employe_branche_access(request, bulletin)
    acomptes = bulletin.acomptes_deduits.order_by("date")
    remboursements_dettes = bulletin.remboursements_dettes.select_related(
        "dette"
    ).order_by("created_at")

    from core.models import CompanyInfo

    company = CompanyInfo.get_instance()

    return render(
        request,
        "depenses/bulletin_paie_print.html",
        {
            "bulletin": bulletin,
            "acomptes": acomptes,
            "remboursements_dettes": remboursements_dettes,
            "company": company,
        },
    )
