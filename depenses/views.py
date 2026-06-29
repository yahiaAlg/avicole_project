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
"""

import datetime
import logging
from calendar import monthrange

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db import transaction
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from depenses.forms import (
    CategorieDepenseForm,
    DashboardFilterForm,
    DepenseFilterForm,
    DepenseForm,
    AssocieForm,
    RetraitAssocieForm,
    RetraitFilterForm,
    EmployeForm,
    PointageForm,
    PointageFilterForm,
    GenererPointagesMoisForm,
    CongeEmployeForm,
    AcompteEmployeForm,
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
    CongeEmploye,
    AcompteEmploye,
    BulletinPaie,
)
from depenses.utils import (
    get_depenses_par_categorie,
    get_depenses_summary,
    get_retraits_associes_summary,
    get_rh_summary,
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

    # ── RH + Retraits summaries (for dashboard KPIs) ──────────────────────
    rh_summary = get_rh_summary(date_debut=date_debut, date_fin=date_fin)
    retraits_summary = get_retraits_associes_summary(
        date_debut=date_debut, date_fin=date_fin
    )

    # ── Bulletin status breakdown (global, for status donut) ──────────────
    bulletins_statuts = {
        "brouillon": BulletinPaie.objects.filter(
            statut=BulletinPaie.STATUT_BROUILLON
        ).count(),
        "valide": BulletinPaie.objects.filter(
            statut=BulletinPaie.STATUT_VALIDE
        ).count(),
        "paye": BulletinPaie.objects.filter(statut=BulletinPaie.STATUT_PAYE).count(),
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
        sal = (
            BulletinPaie.objects.filter(
                statut=BulletinPaie.STATUT_PAYE,
                date_paiement__gte=target,
                date_paiement__lte=mois_fin,
            ).aggregate(total=_Sum("montant_net"))["total"]
            or 0
        )
        acomp = (
            AcompteEmploye.objects.filter(
                date__gte=target, date__lte=mois_fin
            ).aggregate(total=_Sum("montant"))["total"]
            or 0
        )
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
            "title": "المصروفات التشغيلية",
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
                return redirect("depenses:depense_detail", pk=depense.pk)

            except Exception as exc:
                logger.exception("Error creating Depense: %s", exc)
                messages.error(request, f"خطأ أثناء التسجيل: {exc}")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        import datetime

        form = DepenseForm(initial={"date": datetime.date.today()})

    return render(
        request,
        "depenses/depense_form.html",
        {
            "form": form,
            "title": "تسجيل مصروف",
            "action_label": "حفظ المصروف",
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
                    f"تم تحديث المصروف « {depense.description[:60]} ».",
                )
                logger.info("Depense pk=%s updated by '%s'.", depense.pk, request.user)
                return redirect("depenses:depense_detail", pk=depense.pk)

            except Exception as exc:
                logger.exception("Error updating Depense pk=%s: %s", pk, exc)
                messages.error(request, f"خطأ أثناء التحديث: {exc}")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        form = DepenseForm(instance=depense)

    return render(
        request,
        "depenses/depense_form.html",
        {
            "form": form,
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
    """
    depense = get_object_or_404(Depense, pk=pk)
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
        if form.is_valid():
            try:
                with transaction.atomic():
                    retrait = form.save(commit=False)
                    retrait.enregistre_par = request.user
                    retrait.full_clean()
                    retrait.save()
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

    return render(
        request,
        "depenses/retrait_form.html",
        {"form": form, "title": "سحب جديد لشريك", "action_label": "حفظ السحب"},
    )


@login_required(login_url=LOGIN_URL)
def retrait_edit(request, pk):
    retrait = get_object_or_404(RetraitAssocie, pk=pk)
    if request.method == "POST":
        form = RetraitAssocieForm(request.POST, request.FILES, instance=retrait)
        if form.is_valid():
            try:
                with transaction.atomic():
                    updated = form.save(commit=False)
                    updated.full_clean()
                    updated.save()
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

    return render(
        request,
        "depenses/retrait_form.html",
        {
            "form": form,
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
    """Payroll/advances summary for a period (defaults to current month)."""
    today = datetime.date.today()
    premier_du_mois = today.replace(day=1)

    filter_form = DashboardFilterForm(request.GET or None)
    if filter_form.is_valid():
        date_debut = filter_form.cleaned_data.get("date_debut") or premier_du_mois
        date_fin = filter_form.cleaned_data.get("date_fin") or today
    else:
        date_debut, date_fin = premier_du_mois, today

    summary = get_rh_summary(date_debut=date_debut, date_fin=date_fin)

    bulletins_recents = BulletinPaie.objects.select_related("employe").order_by(
        "-annee", "-mois", "-updated_at"
    )[:10]
    acomptes_recents = AcompteEmploye.objects.select_related("employe").order_by(
        "-date"
    )[:10]

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
            "title": "لوحة تحكم — الموارد البشرية",
        },
    )


# ===========================================================================
# RH — Employés
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def employe_list(request):
    qs = Employe.objects.select_related("batiment", "binome").order_by("nom_complet")
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
        {"employes": qs, "actif_param": actif_param, "q": q, "title": "العمال"},
    )


@login_required(login_url=LOGIN_URL)
def employe_create(request):
    if request.method == "POST":
        form = EmployeForm(request.POST)
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
        form = EmployeForm()

    return render(
        request,
        "depenses/employe_form.html",
        {"form": form, "title": "عامل جديد", "action_label": "تسجيل العامل"},
    )


@login_required(login_url=LOGIN_URL)
def employe_edit(request, pk):
    employe = get_object_or_404(Employe, pk=pk)
    if request.method == "POST":
        form = EmployeForm(request.POST, instance=employe)
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
        form = EmployeForm(instance=employe)

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
    """Employee profile: leave balance, recent attendance, advances, payslips."""
    employe = get_object_or_404(
        Employe.objects.select_related("batiment", "binome"), pk=pk
    )
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
    qs = Pointage.objects.select_related("employe").order_by(
        "-date", "employe__nom_complet"
    )

    filter_form = PointageFilterForm(request.GET or None)
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
    generer_form = GenererPointagesMoisForm()

    return render(
        request,
        "depenses/pointage_list.html",
        {
            "page": page,
            "filter_form": filter_form,
            "generer_form": generer_form,
            "title": "تسجيلات الحضور",
        },
    )


@login_required(login_url=LOGIN_URL)
def pointage_create(request):
    if request.method == "POST":
        form = PointageForm(request.POST)
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
        form = PointageForm(initial={"date": datetime.date.today()})

    return render(
        request,
        "depenses/pointage_form.html",
        {"form": form, "title": "تسجيل حضور", "action_label": "حفظ"},
    )


@login_required(login_url=LOGIN_URL)
def pointage_edit(request, pk):
    pointage = get_object_or_404(Pointage, pk=pk)
    if request.method == "POST":
        form = PointageForm(request.POST, instance=pointage)
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
        form = PointageForm(instance=pointage)

    return render(
        request,
        "depenses/pointage_form.html",
        {
            "form": form,
            "pointage": pointage,
            "title": "تعديل تسجيل الحضور",
            "action_label": "حفظ التعديلات",
        },
    )


@login_required(login_url=LOGIN_URL)
@require_POST
def pointage_generer_mois(request):
    """
    Pre-fill a month of Pointage for one employee: PRESENT on regular days,
    REPOS on jour_repos_habituel. Existing rows for that employe+date are
    NEVER overwritten — only missing days are created (BR-RH-01).
    """
    form = GenererPointagesMoisForm(request.POST)
    if not form.is_valid():
        messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
        return redirect("depenses:pointage_list")

    employe = form.cleaned_data["employe"]
    annee = form.cleaned_data["annee"]
    mois = form.cleaned_data["mois"]

    premier_jour = datetime.date(annee, mois, 1)
    dernier_jour = datetime.date(annee, mois, monthrange(annee, mois)[1])

    jours_existants = set(
        Pointage.objects.filter(
            employe=employe, date__gte=premier_jour, date__lte=dernier_jour
        ).values_list("date", flat=True)
    )

    nouveaux = []
    jour = premier_jour
    while jour <= dernier_jour:
        if jour not in jours_existants:
            statut = (
                Pointage.STATUT_REPOS
                if jour.weekday() == employe.jour_repos_habituel
                else Pointage.STATUT_PRESENT
            )
            nouveaux.append(Pointage(employe=employe, date=jour, statut=statut))
        jour += datetime.timedelta(days=1)

    if nouveaux:
        Pointage.objects.bulk_create(nouveaux)

    messages.success(
        request,
        f"تم إنشاء {len(nouveaux)} تسجيل حضور لـ « {employe.nom_complet} » "
        f"({mois:02d}/{annee}). يرجى تصحيح الاستثناءات (غياب/عطلة/ساعات إضافية).",
    )
    logger.info(
        "pointage_generer_mois: employe=%s %02d/%s → %s rows created by '%s'.",
        employe.matricule,
        mois,
        annee,
        len(nouveaux),
        request.user,
    )
    return redirect(f"{reverse('depenses:pointage_list')}?employe={employe.pk}")


# ===========================================================================
# RH — Congés
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def conge_list(request):
    qs = CongeEmploye.objects.select_related("employe").order_by("-date_debut")
    employe_id = request.GET.get("employe")
    if employe_id:
        qs = qs.filter(employe_id=employe_id)

    page = _paginate(qs, request.GET.get("page"))
    return render(
        request,
        "depenses/conge_list.html",
        {"page": page, "title": "عطل العمال"},
    )


@login_required(login_url=LOGIN_URL)
def conge_create(request):
    """
    Record a paid-leave block (BR-RH-03) and immediately materialize it
    into Pointage rows (BR-RH-05) so payroll calculation sees it.
    """
    if request.method == "POST":
        form = CongeEmployeForm(request.POST)
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
        form = CongeEmployeForm()

    return render(
        request,
        "depenses/conge_form.html",
        {"form": form, "title": "عطلة جديدة", "action_label": "حفظ"},
    )


# ===========================================================================
# RH — Acomptes employés (BR-RH-04)
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def acompte_employe_list(request):
    qs = AcompteEmploye.objects.select_related("employe", "bulletin_paie").order_by(
        "-date"
    )
    employe_id = request.GET.get("employe")
    if employe_id:
        qs = qs.filter(employe_id=employe_id)

    total = qs.aggregate(total=Sum("montant"))["total"] or 0
    page = _paginate(qs, request.GET.get("page"))

    return render(
        request,
        "depenses/acompte_employe_list.html",
        {"page": page, "total": total, "title": "تسبيقات على الرواتب"},
    )


@login_required(login_url=LOGIN_URL)
def acompte_employe_create(request):
    if request.method == "POST":
        form = AcompteEmployeForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    acompte = form.save(commit=False)
                    acompte.enregistre_par = request.user
                    acompte.full_clean()
                    acompte.save()
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
        form = AcompteEmployeForm(initial={"date": datetime.date.today()})

    return render(
        request,
        "depenses/acompte_employe_form.html",
        {"form": form, "title": "تسبيق جديد", "action_label": "حفظ"},
    )


# ===========================================================================
# RH — Bulletins de paie  (BR-RH-02 / BR-RH-05)
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def bulletin_paie_list(request):
    qs = BulletinPaie.objects.select_related("employe").order_by("-annee", "-mois")

    filter_form = RHFilterForm(request.GET or None)
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
        {"page": page, "filter_form": filter_form, "title": "كشوف الرواتب"},
    )


@login_required(login_url=LOGIN_URL)
def bulletin_paie_generer(request):
    """
    (Re)compute a payslip from Pointage for employe+annee+mois and persist
    the snapshot. Only allowed while the payslip is still 'brouillon' (or
    does not exist yet) — once 'valide' or 'paye' it is locked (BR-RH-05).
    """
    if request.method == "POST":
        form = GenererBulletinPaieForm(request.POST)
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
            }
        )

    return render(
        request,
        "depenses/bulletin_paie_generer.html",
        {"form": form, "title": "حساب كشف راتب", "action_label": "حساب"},
    )


@login_required(login_url=LOGIN_URL)
def bulletin_paie_detail(request, pk):
    bulletin = get_object_or_404(
        BulletinPaie.objects.select_related("employe", "genere_par"), pk=pk
    )
    acomptes = bulletin.acomptes_deduits.order_by("-date")
    return render(
        request,
        "depenses/bulletin_paie_detail.html",
        {
            "bulletin": bulletin,
            "acomptes": acomptes,
            "title": f"كشف راتب — {bulletin.employe.nom_complet} — {bulletin.periode_label}",
        },
    )


@login_required(login_url=LOGIN_URL)
@require_POST
def bulletin_paie_valider(request, pk):
    """Lock a draft payslip's figures (brouillon → valide)."""
    bulletin = get_object_or_404(BulletinPaie, pk=pk)
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
    """Mark a validated payslip as paid (valide → paye)."""
    bulletin = get_object_or_404(BulletinPaie, pk=pk)
    if bulletin.statut != BulletinPaie.STATUT_VALIDE:
        messages.error(request, "يجب تأكيد الكشف أولاً قبل تسجيل الدفع.")
        return redirect("depenses:bulletin_paie_detail", pk=pk)

    if request.method == "POST":
        form = BulletinPaiementForm(request.POST, instance=bulletin)
        if form.is_valid():
            paye = form.save(commit=False)
            paye.statut = BulletinPaie.STATUT_PAYE
            paye.save()
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

    return render(
        request,
        "depenses/bulletin_paie_payer.html",
        {"form": form, "bulletin": bulletin, "title": "تسجيل دفع الراتب"},
    )


@login_required(login_url=LOGIN_URL)
def bulletin_paie_print(request, pk):
    """Print-optimised payslip voucher — no PDF library, @media print CSS."""
    bulletin = get_object_or_404(
        BulletinPaie.objects.select_related("employe", "employe__batiment"), pk=pk
    )
    acomptes = bulletin.acomptes_deduits.order_by("date")

    from core.models import CompanyInfo

    company = CompanyInfo.get_instance()

    return render(
        request,
        "depenses/bulletin_paie_print.html",
        {"bulletin": bulletin, "acomptes": acomptes, "company": company},
    )
