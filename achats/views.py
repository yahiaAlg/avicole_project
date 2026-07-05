"""
achats/views.py

Function-based views for the full supplier procurement cycle:

  BLFournisseur      : list, create, edit, detail, print
  FactureFournisseur : list, create, detail, print
  ReglementFournisseur: list, create  (no edit — BR-REG-06)
  AcompteFournisseur : list, detail   (created automatically by FIFO engine)

All write operations use Post-Redirect-Get.
State-changing actions (delete brouillon BL) are POST-only.

AJAX endpoints:
  bl_lignes_total_json  — return line totals for selected BLs (used on invoice
                          creation form to display the computed montant_total
                          before the user submits).

v1.4 (§3.5, BR-BRA-01/05): every BL/Facture/Règlement/Acompte carries a
required `branche` FK; Vue par Branche scopes every list/detail to the
request's active branche (BR-BRA-02), Vue Globale shows every branche
combined. Creation views require a concrete active branche
(@require_branche_context — BR-BRA-04) and lock the form's branche field
to it.
"""

import logging
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django import forms

from achats.forms import (
    AcompteFournisseurPieceJointeFormSet,
    BLFournisseurForm,
    BLFournisseurLigneFormSet,
    BLFournisseurPieceJointeFormSet,
    FactureFournisseurForm,
    FactureFournisseurPieceJointeFormSet,
    ReglementFournisseurForm,
    ReglementFournisseurPieceJointeFormSet,
)
from achats.models import (
    AcompteFournisseur,
    AllocationReglement,
    BLFournisseur,
    BLFournisseurLigne,
    FactureFournisseur,
    ReglementFournisseur,
)
from core.views import (
    branche_object_or_404,
    build_piece_jointe_formset,
    get_active_branche,
    require_branche_context,
)
from intrants.models import Fournisseur

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


def _auto_reference_bl(branche):
    """Return the next BLF reference for *branche* without committing."""
    from achats.utils import generer_reference_bl_fournisseur

    return generer_reference_bl_fournisseur(branche)


def _auto_reference_facture(branche):
    """Return the next FRN reference for *branche* without committing."""
    from achats.utils import generer_reference_facture_fournisseur

    return generer_reference_facture_fournisseur(branche)


# ===========================================================================
# BL Fournisseur — List
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def bl_fournisseur_list(request):
    """
    BL list with search (reference, supplier name), statut filter, and
    optional supplier filter passed as ?fournisseur=<pk>.

    v1.4 (BR-BRA-01/02): Vue par Branche shows only the active branche's
    BLs; Vue Globale shows every branche's BLs combined.
    """
    branche = get_active_branche(request)
    qs = BLFournisseur.objects.select_related(
        "fournisseur", "branche", "created_by"
    ).order_by("-date_bl", "-created_at")
    if branche is not None:
        qs = qs.filter(branche=branche)

    # Statut filter
    statut = request.GET.get("statut", "")
    if statut:
        qs = qs.filter(statut=statut)

    # Document type filter
    type_doc = request.GET.get("type_doc", "")
    if type_doc:
        qs = qs.filter(type_document=type_doc)

    # Supplier filter (from fournisseur detail page deep-link)
    fournisseur_pk = request.GET.get("fournisseur", "")
    if fournisseur_pk:
        qs = qs.filter(fournisseur_id=fournisseur_pk)

    # Search
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(
            Q(reference__icontains=q)
            | Q(fournisseur__nom__icontains=q)
            | Q(reference_fournisseur__icontains=q)
            | Q(numero_autorisation__icontains=q)
        )

    page = _paginate(qs, request.GET.get("page"))
    fournisseurs = Fournisseur.objects.filter(actif=True).order_by("nom")

    # Count expired authorizations for dashboard alert
    import datetime as _dt

    nb_expires_qs = BLFournisseur.objects.filter(
        type_document=BLFournisseur.TYPE_AUTORISATION_ACCES,
        statut=BLFournisseur.STATUT_AUTORISE,
        date_expiration_autorisation__lt=_dt.date.today(),
    )
    if branche is not None:
        nb_expires_qs = nb_expires_qs.filter(branche=branche)
    nb_expires = nb_expires_qs.count()

    return render(
        request,
        "achats/bl_fournisseur_list.html",
        {
            "page": page,
            "q": q,
            "statut": statut,
            "type_doc": type_doc,
            "fournisseur_pk": fournisseur_pk,
            "fournisseurs": fournisseurs,
            "statut_choices": BLFournisseur.STATUT_CHOICES,
            "type_document_choices": BLFournisseur.TYPE_DOCUMENT_CHOICES,
            "nb_expires": nb_expires,
            "active_branche": branche,
            "title": "وصولات استلام الموردين",
        },
    )


# ===========================================================================
# BL Fournisseur — Create
# ===========================================================================


@login_required(login_url=LOGIN_URL)
@require_branche_context
def bl_fournisseur_create(request, fournisseur_pk=None):
    """
    Create a new BL Fournisseur with its lines (inline formset).

    When called via the scoped URL (fournisseur_pk is set), the fournisseur
    field is pre-filled and hidden, mirroring bl_client_create_for_client.
    Reference is auto-generated and pre-filled; the user may override it.
    Saving the form + formset is wrapped in a DB transaction.

    BR-BRA-01/04: the BL belongs to the request's active branche — locked
    on the form; Vue Globale cannot reach this view
    (@require_branche_context).
    """
    from intrants.models import Fournisseur as FournisseurModel

    branche = get_active_branche(request)

    fournisseur = (
        get_object_or_404(FournisseurModel, pk=fournisseur_pk)
        if fournisseur_pk
        else None
    )

    if request.method == "POST":
        form = BLFournisseurForm(request.POST, request.FILES, branche=branche)
        formset = BLFournisseurLigneFormSet(request.POST, prefix="lignes")
        pj_formset = build_piece_jointe_formset(
            BLFournisseurPieceJointeFormSet, request, prefix="pj"
        )

        if form.is_valid() and formset.is_valid() and pj_formset.is_valid():
            try:
                with transaction.atomic():
                    bl = form.save(commit=False)
                    bl.created_by = request.user
                    bl.save()
                    formset.instance = bl
                    formset.save()
                    pj_formset.instance = bl
                    pj_formset.save()
                    # If the BL was created directly with statut=RECU, the
                    # post_save signal fired before lines existed (lines are
                    # saved by formset.save() above).  Process stock entries
                    # now that all lines are persisted.
                    if bl.statut == BLFournisseur.STATUT_RECU:
                        from achats.signals import traiter_entrees_stock_bl

                        traiter_entrees_stock_bl(bl)

                messages.success(
                    request,
                    f"تم إنشاء وصل الاستلام {bl.reference} بنجاح ({bl.lignes.count()} سطر).",
                )
                logger.info(
                    "BLFournisseur pk=%s (%s) created by '%s'.",
                    bl.pk,
                    bl.reference,
                    request.user,
                )
                return redirect("achats:bl_fournisseur_detail", pk=bl.pk)

            except Exception as exc:
                logger.exception("Error creating BLFournisseur: %s", exc)
                messages.error(request, f"خطأ أثناء الإنشاء: {exc}")

        else:
            messages.error(request, "يرجى تصحيح الأخطاء في النموذج.")

    else:
        initial_ref = _auto_reference_bl(branche)
        initial = {"reference": initial_ref}
        if fournisseur:
            initial["fournisseur"] = fournisseur
        form = BLFournisseurForm(initial=initial, branche=branche)
        if fournisseur:
            form.fields["fournisseur"].widget = forms.HiddenInput()
            form.fields["fournisseur"].initial = fournisseur
        formset = BLFournisseurLigneFormSet(prefix="lignes")
        pj_formset = build_piece_jointe_formset(
            BLFournisseurPieceJointeFormSet, request, prefix="pj"
        )

    return render(
        request,
        "achats/bl_fournisseur_form.html",
        {
            "form": form,
            "formset": formset,
            "pj_formset": pj_formset,
            "title": "وصل استلام جديد",
            "action_label": "إنشاء",
            "fournisseur": fournisseur,
            "active_branche": branche,
            "categories_intrant": __import__(
                "intrants.models", fromlist=["CategorieIntrant"]
            )
            .CategorieIntrant.objects.filter(actif=True)
            .order_by("ordre", "libelle"),
        },
    )


# ===========================================================================
# BL Fournisseur — Edit
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def bl_fournisseur_edit(request, pk):
    """
    Edit an existing BL Fournisseur.

    BR-BLF-02: locked (Facturé) BLs are redirected back with an error.
    The form itself also disables all fields on locked instances.
    BR-BRA-02: the BL must belong to the request's active branche.
    """
    bl = branche_object_or_404(
        request,
        BLFournisseur.objects.select_related("fournisseur"),
        pk=pk,
    )

    if bl.est_verrouille:
        messages.error(
            request,
            f"BR-BLF-02: وصل الاستلام {bl.reference} مقفل (فاتورة محررة) ولا يمكن تعديله.",
        )
        return redirect("achats:bl_fournisseur_detail", pk=pk)

    if request.method == "POST":
        form = BLFournisseurForm(
            request.POST, request.FILES, instance=bl, branche=bl.branche
        )
        formset = BLFournisseurLigneFormSet(request.POST, instance=bl, prefix="lignes")
        pj_formset = build_piece_jointe_formset(
            BLFournisseurPieceJointeFormSet, request, instance=bl, prefix="pj"
        )

        if form.is_valid() and formset.is_valid() and pj_formset.is_valid():
            try:
                with transaction.atomic():
                    form.save()
                    formset.save()
                    pj_formset.save()

                messages.success(request, f"تم تحديث وصل الاستلام {bl.reference}.")
                logger.info("BLFournisseur pk=%s updated by '%s'.", pk, request.user)
                return redirect("achats:bl_fournisseur_detail", pk=pk)

            except Exception as exc:
                logger.exception("Error updating BLFournisseur pk=%s: %s", pk, exc)
                messages.error(request, f"خطأ أثناء التحديث: {exc}")

        else:
            messages.error(request, "يرجى تصحيح الأخطاء.")

    else:
        form = BLFournisseurForm(instance=bl, branche=bl.branche)
        formset = BLFournisseurLigneFormSet(instance=bl, prefix="lignes")
        pj_formset = build_piece_jointe_formset(
            BLFournisseurPieceJointeFormSet, request, instance=bl, prefix="pj"
        )

    return render(
        request,
        "achats/bl_fournisseur_form.html",
        {
            "form": form,
            "formset": formset,
            "pj_formset": pj_formset,
            "object": bl,
            "title": f"تعديل وصل الاستلام — {bl.reference}",
            "action_label": "حفظ",
            "categories_intrant": __import__(
                "intrants.models", fromlist=["CategorieIntrant"]
            )
            .CategorieIntrant.objects.filter(actif=True)
            .order_by("ordre", "libelle"),
        },
    )


# ===========================================================================
# BL Fournisseur — Detail
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def bl_fournisseur_detail(request, pk):
    """
    BL detail: header + lines + linked invoices.

    BR-BRA-02: the BL must belong to the request's active branche.
    """
    bl = branche_object_or_404(
        request,
        BLFournisseur.objects.select_related("fournisseur", "branche", "created_by"),
        pk=pk,
    )
    lignes = bl.lignes.select_related("intrant").all()
    factures = bl.factures.order_by("-date_facture")
    pieces_jointes = bl.pieces_jointes.select_related("uploaded_by").order_by(
        "-created_at"
    )

    # Determine admin status: staff OR profile role == "admin"
    try:
        is_admin = request.user.is_staff or request.user.profile.role == "admin"
    except Exception:
        is_admin = request.user.is_staff

    # Statuts the user may switch to (FACTURE is system-only)
    STATUT_TRANSITIONS = {
        BLFournisseur.STATUT_AUTORISE: [
            (BLFournisseur.STATUT_RECU, "تأكيد الاستلام (مغادرة البوابة)"),
            (BLFournisseur.STATUT_LITIGE, "تعليق / نزاع"),
        ],
        BLFournisseur.STATUT_BROUILLON: [
            (BLFournisseur.STATUT_RECU, "Marquer comme Reçu"),
            (BLFournisseur.STATUT_LITIGE, "Signaler en litige"),
        ],
        BLFournisseur.STATUT_RECU: [
            (BLFournisseur.STATUT_LITIGE, "Signaler en litige"),
            (BLFournisseur.STATUT_BROUILLON, "Repasser en Brouillon"),
        ],
        BLFournisseur.STATUT_LITIGE: [
            (BLFournisseur.STATUT_RECU, "Marquer comme Reçu"),
            (BLFournisseur.STATUT_BROUILLON, "Repasser en Brouillon"),
            (BLFournisseur.STATUT_AUTORISE, "Réactiver l'autorisation"),
        ],
        BLFournisseur.STATUT_FACTURE: [],
    }

    next_statut_label = None
    next_statut_value = None
    if bl.statut == BLFournisseur.STATUT_AUTORISE:
        if not bl.est_expire:
            next_statut_value = BLFournisseur.STATUT_RECU
            next_statut_label = "تأكيد الاستلام (مغادرة البوابة)"
    elif bl.statut == BLFournisseur.STATUT_BROUILLON:
        next_statut_value = BLFournisseur.STATUT_RECU
        next_statut_label = "Marquer comme Reçu"
    elif bl.statut == BLFournisseur.STATUT_RECU:
        next_statut_value = BLFournisseur.STATUT_LITIGE
        next_statut_label = "Signaler en litige"
    elif bl.statut == BLFournisseur.STATUT_LITIGE:
        next_statut_value = BLFournisseur.STATUT_RECU
        next_statut_label = "Marquer comme Reçu"

    return render(
        request,
        "achats/bl_fournisseur_detail.html",
        {
            "bl": bl,
            "lignes": lignes,
            "factures": factures,
            "pieces_jointes": pieces_jointes,
            "montant_total": bl.montant_total,
            "title": f"وصل الاستلام {bl.reference}",
            "is_admin": is_admin,
            "statut_transitions": STATUT_TRANSITIONS.get(bl.statut, []),
            "next_statut_value": next_statut_value,
            "next_statut_label": next_statut_label,
            "est_expire": bl.est_expire,
        },
    )


# ===========================================================================
# BL Fournisseur — Change Statut
# ===========================================================================


@login_required(login_url=LOGIN_URL)
@require_POST
def bl_fournisseur_change_statut(request, pk):
    """
    Change the statut of a BLFournisseur from the detail page.

    Allowed transitions (FACTURE is system-only — set by invoice signal):
      brouillon → recu | litige
      recu      → litige | brouillon
      litige    → recu  | brouillon
      facture   → (none — locked)

    Admins (is_staff or profile.role=="admin") may choose any allowed target
    via the 'statut' POST field.  Regular users send a fixed 'next_statut'
    field pre-filled by the template button.
    """
    bl = branche_object_or_404(request, BLFournisseur, pk=pk)

    if bl.est_verrouille:
        messages.error(
            request,
            f"BR-BLF-02: وصل الاستلام {bl.reference} مقفل (فاتورة محررة) ولا يمكن تعديله.",
        )
        return redirect("achats:bl_fournisseur_detail", pk=pk)

    ALLOWED_TARGETS = {
        BLFournisseur.STATUT_AUTORISE: {
            BLFournisseur.STATUT_RECU,
            BLFournisseur.STATUT_LITIGE,
        },
        BLFournisseur.STATUT_BROUILLON: {
            BLFournisseur.STATUT_RECU,
            BLFournisseur.STATUT_LITIGE,
        },
        BLFournisseur.STATUT_RECU: {
            BLFournisseur.STATUT_LITIGE,
            BLFournisseur.STATUT_BROUILLON,
        },
        BLFournisseur.STATUT_LITIGE: {
            BLFournisseur.STATUT_RECU,
            BLFournisseur.STATUT_BROUILLON,
            BLFournisseur.STATUT_AUTORISE,
        },
        BLFournisseur.STATUT_FACTURE: set(),
    }

    try:
        is_admin = request.user.is_staff or request.user.profile.role == "admin"
    except Exception:
        is_admin = request.user.is_staff

    new_statut = (
        request.POST.get("statut") if is_admin else request.POST.get("next_statut")
    )

    allowed = ALLOWED_TARGETS.get(bl.statut, set())
    if new_statut not in allowed:
        messages.error(
            request,
            f"تحويل الحالة غير مسموح به: « {bl.get_statut_display()} » ← « {new_statut} ».",
        )
        return redirect("achats:bl_fournisseur_detail", pk=pk)

    # BR-BLF-05: block confirmation of an expired autorisation d'accès.
    if new_statut == BLFournisseur.STATUT_RECU and bl.est_expire:
        messages.error(
            request,
            f"BR-BLF-05: تفويض الوصول {bl.reference} منتهي الصلاحية "
            f"({bl.date_expiration_autorisation}). "
            "لا يمكن تأكيد الاستلام — تواصل مع المورد للتجديد.",
        )
        return redirect("achats:bl_fournisseur_detail", pk=pk)

    old_display = bl.get_statut_display()
    bl.statut = new_statut
    bl.save(update_fields=["statut", "updated_at"])

    new_display = bl.get_statut_display()
    messages.success(
        request,
        f"تم تغيير حالة وصل الاستلام {bl.reference}: {old_display} ← {new_display}.",
    )
    logger.info(
        "BLFournisseur pk=%s statut changed %s → %s by '%s'.",
        pk,
        old_display,
        new_display,
        request.user,
    )
    return redirect("achats:bl_fournisseur_detail", pk=pk)


# ===========================================================================
# BL Fournisseur — Print
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def bl_fournisseur_print(request, pk):
    """
    Printable BL view.  Renders a minimal template with @media print CSS.
    """
    from core.models import CompanyInfo

    bl = branche_object_or_404(
        request,
        BLFournisseur.objects.select_related("fournisseur"),
        pk=pk,
    )
    lignes = bl.lignes.select_related("intrant").all()
    company = CompanyInfo.get_instance()

    return render(
        request,
        "achats/bl_fournisseur_print.html",
        {
            "bl": bl,
            "lignes": lignes,
            "montant_total": bl.montant_total,
            "company": company,
        },
    )


# ===========================================================================
# BL Fournisseur — Delete (brouillon only)
# ===========================================================================


@login_required(login_url=LOGIN_URL)
@require_POST
def bl_fournisseur_delete(request, pk):
    """
    Delete a BL only when it is in BROUILLON status.
    RECU / FACTURE / LITIGE BLs cannot be deleted (stock impact or locked).
    """
    bl = branche_object_or_404(request, BLFournisseur, pk=pk)

    DELETABLE_STATUTS = {BLFournisseur.STATUT_BROUILLON, BLFournisseur.STATUT_AUTORISE}
    if bl.statut not in DELETABLE_STATUTS:
        messages.error(
            request,
            f"يمكن حذف الوصولات في حالة المسودة أو التفويض فقط. الوصل {bl.reference} في حالة « {bl.get_statut_display()} ».",
        )
        return redirect("achats:bl_fournisseur_detail", pk=pk)

    ref = bl.reference
    bl.delete()
    messages.success(request, f"تم حذف وصل الاستلام {ref}.")
    logger.info("BLFournisseur pk=%s (%s) deleted by '%s'.", pk, ref, request.user)
    return redirect("achats:bl_fournisseur_list")


# ===========================================================================
# Facture Fournisseur — List
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def facture_fournisseur_list(request):
    """
    Invoice list with search (reference, supplier), statut filter,
    overdue filter, and optional supplier deep-link via ?fournisseur=<pk>.

    v1.4 (BR-BRA-01/02): Vue par Branche shows only the active branche's
    invoices; Vue Globale shows every branche's invoices combined.
    """
    branche = get_active_branche(request)
    qs = FactureFournisseur.objects.select_related("fournisseur", "branche").order_by(
        "-date_facture", "-created_at"
    )
    if branche is not None:
        qs = qs.filter(branche=branche)

    statut = request.GET.get("statut", "")
    if statut:
        qs = qs.filter(statut=statut)

    fournisseur_pk = request.GET.get("fournisseur", "")
    if fournisseur_pk:
        qs = qs.filter(fournisseur_id=fournisseur_pk)

    # Overdue filter (past due_date, not paid)
    if request.GET.get("retard") == "1":
        import datetime

        qs = qs.exclude(statut=FactureFournisseur.STATUT_PAYE).filter(
            date_echeance__lt=datetime.date.today()
        )

    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(reference__icontains=q) | Q(fournisseur__nom__icontains=q))

    page = _paginate(qs, request.GET.get("page"))
    fournisseurs = Fournisseur.objects.filter(actif=True).order_by("nom")

    return render(
        request,
        "achats/facture_fournisseur_list.html",
        {
            "page": page,
            "q": q,
            "statut": statut,
            "fournisseur_pk": fournisseur_pk,
            "retard": request.GET.get("retard", ""),
            "fournisseurs": fournisseurs,
            "statut_choices": FactureFournisseur.STATUT_CHOICES,
            "active_branche": branche,
            "title": "فواتير الموردين",
        },
    )


# ===========================================================================
# Facture Fournisseur — Create
# ===========================================================================


@login_required(login_url=LOGIN_URL)
@require_branche_context
def facture_fournisseur_create(request):
    """
    Create a supplier invoice by selecting a supplier and their Reçu BLs.

    Workflow:
      Step 1 (GET, no ?fournisseur): show supplier selector.
      Step 2 (GET with ?fournisseur=<pk>): show invoice form with filtered BL list.
      Step 3 (POST): validate & save.

    BR-FAF-01: montant_total is computed from BL lines by the post_save signal —
               the form excludes that field.
    BR-FAF-02: bls queryset is restricted to Reçu BLs for the selected supplier.
    BR-BRA-01/04: the invoice belongs to the request's active branche — only
               that branche's Reçu BLs can be selected; Vue Globale cannot
               reach this view (@require_branche_context).
    """
    branche = get_active_branche(request)

    # Resolve fournisseur from POST body or GET param
    fournisseur = None
    fournisseur_pk = request.POST.get("fournisseur") or request.GET.get(
        "fournisseur", ""
    )
    if fournisseur_pk:
        fournisseur = get_object_or_404(Fournisseur, pk=fournisseur_pk, actif=True)

    # Step 1: no supplier selected yet — show selector
    if not fournisseur and request.method == "GET":
        fournisseurs = Fournisseur.objects.filter(actif=True).order_by("nom")
        return render(
            request,
            "achats/facture_fournisseur_select_fournisseur.html",
            {
                "fournisseurs": fournisseurs,
                "active_branche": branche,
                "title": "فاتورة جديدة — اختر موردًا",
            },
        )

    if request.method == "POST":
        form = FactureFournisseurForm(
            request.POST,
            fournisseur=fournisseur,
            branche=branche,
        )
        pj_formset = build_piece_jointe_formset(
            FactureFournisseurPieceJointeFormSet, request, prefix="pj"
        )
        if form.is_valid() and pj_formset.is_valid():
            try:
                with transaction.atomic():
                    facture = form.save(commit=False)
                    facture.created_by = request.user
                    # montant_total is set to 0 here; the post_save signal
                    # (facture_fournisseur_post_save) will recompute it from BL
                    # lines immediately after the M2M relation is saved.
                    facture.save()
                    form.save_m2m()  # persist bls M2M
                    pj_formset.instance = facture
                    pj_formset.save()

                messages.success(
                    request,
                    f"تم إنشاء الفاتورة {facture.reference}. المبلغ المحسوب: {facture.montant_total} دج.",
                )
                logger.info(
                    "FactureFournisseur pk=%s created by '%s'.",
                    facture.pk,
                    request.user,
                )
                return redirect("achats:facture_fournisseur_detail", pk=facture.pk)

            except Exception as exc:
                logger.exception("Error creating FactureFournisseur: %s", exc)
                messages.error(request, f"خطأ أثناء الإنشاء: {exc}")

        else:
            messages.error(request, "يرجى تصحيح الأخطاء.")

    else:
        # Step 2: supplier selected, show form pre-filtered
        initial_ref = _auto_reference_facture(branche)
        form = FactureFournisseurForm(
            fournisseur=fournisseur,
            branche=branche,
            initial={"reference": initial_ref},
        )
        pj_formset = build_piece_jointe_formset(
            FactureFournisseurPieceJointeFormSet, request, prefix="pj"
        )

    # Expose available BL amounts for the template's running total widget —
    # scoped to the active branche (BR-BRA-01), mirroring the form's own
    # bls queryset.
    bls_recu = []
    if fournisseur:
        bls_recu = (
            BLFournisseur.objects.filter(
                fournisseur=fournisseur,
                branche=branche,
                statut=BLFournisseur.STATUT_RECU,
            )
            .prefetch_related("lignes")
            .order_by("date_bl")
        )

    return render(
        request,
        "achats/facture_fournisseur_form.html",
        {
            "form": form,
            "pj_formset": pj_formset,
            "fournisseur": fournisseur,
            "bls_recu": bls_recu,
            "active_branche": branche,
            "title": "فاتورة مورد جديدة",
            "action_label": "إنشاء الفاتورة",
        },
    )


# ===========================================================================
# Facture Fournisseur — Detail
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def facture_fournisseur_detail(request, pk):
    """
    Invoice detail: header, linked BLs/lines, payment allocations, balance.
    """
    facture = branche_object_or_404(
        request,
        FactureFournisseur.objects.select_related("fournisseur", "created_by"),
        pk=pk,
    )
    bls = facture.bls.prefetch_related("lignes__intrant").order_by("date_bl")
    allocations = facture.allocations.select_related("reglement").order_by(
        "reglement__date_reglement"
    )
    pieces_jointes = facture.pieces_jointes.select_related("uploaded_by").order_by(
        "-created_at"
    )
    pj_formset = build_piece_jointe_formset(
        FactureFournisseurPieceJointeFormSet, request, instance=facture, prefix="pj"
    )

    # Determine admin status: staff OR profile role == "admin" (same pattern
    # as bl_fournisseur_detail) — controls visibility of the cascade-delete
    # button (facture + its BLs + its règlements).
    try:
        is_admin = request.user.is_staff or request.user.profile.role == "admin"
    except Exception:
        is_admin = request.user.is_staff

    return render(
        request,
        "achats/facture_fournisseur_detail.html",
        {
            "facture": facture,
            "bls": bls,
            "allocations": allocations,
            "pieces_jointes": pieces_jointes,
            "pj_formset": pj_formset,
            "is_admin": is_admin,
            "title": f"فاتورة {facture.reference}",
        },
    )


# ===========================================================================
# Facture Fournisseur — Ajouter des pièces jointes (pas d'édition possible)
# ===========================================================================


@login_required(login_url=LOGIN_URL)
@require_POST
def facture_fournisseur_ajouter_piece_jointe(request, pk):
    """
    FactureFournisseur has no edit view (BR-FAF-03 — locked once created),
    so adding proof documents after the fact goes through this dedicated
    POST-only action instead of a full edit form.
    """
    facture = branche_object_or_404(request, FactureFournisseur, pk=pk)
    pj_formset = build_piece_jointe_formset(
        FactureFournisseurPieceJointeFormSet, request, instance=facture, prefix="pj"
    )
    if pj_formset.is_valid():
        pj_formset.save()
        messages.success(request, "تم إضافة المرفقات.")
    else:
        messages.error(request, "يرجى تصحيح الأخطاء في المرفقات.")
    return redirect("achats:facture_fournisseur_detail", pk=pk)


# ===========================================================================
# Facture Fournisseur — Print
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def facture_fournisseur_print(request, pk):
    """Printable invoice — @media print CSS handled in template."""
    from core.models import CompanyInfo

    facture = branche_object_or_404(
        request,
        FactureFournisseur.objects.select_related("fournisseur"),
        pk=pk,
    )
    bls = facture.bls.prefetch_related("lignes__intrant").order_by("date_bl")
    allocations = facture.allocations.select_related("reglement").order_by(
        "reglement__date_reglement"
    )
    company = CompanyInfo.get_instance()

    return render(
        request,
        "achats/facture_fournisseur_print.html",
        {
            "facture": facture,
            "bls": bls,
            "allocations": allocations,
            "company": company,
        },
    )


# ===========================================================================
# Facture Fournisseur — Toggle litige
# ===========================================================================


@login_required(login_url=LOGIN_URL)
@require_POST
def facture_fournisseur_toggle_litige(request, pk):
    """
    Toggle an invoice between EN_LITIGE and its previous payment status.
    POST-only.  Cannot be applied to fully paid invoices.
    """
    facture = branche_object_or_404(request, FactureFournisseur, pk=pk)

    if facture.statut == FactureFournisseur.STATUT_PAYE:
        messages.error(request, "لا يمكن وضع فاتورة مدفوعة بالكامل في حالة نزاع.")
        return redirect("achats:facture_fournisseur_detail", pk=pk)

    if facture.statut == FactureFournisseur.STATUT_EN_LITIGE:
        # Recompute the correct status from current balance
        facture.statut = (
            FactureFournisseur.STATUT_NON_PAYE
        )  # recalculer_solde will fix it
        facture.recalculer_solde()
        messages.success(request, f"تم سحب الفاتورة {facture.reference} من النزاع.")
    else:
        facture.statut = FactureFournisseur.STATUT_EN_LITIGE
        facture.save(update_fields=["statut", "updated_at"])
        messages.success(request, f"تم وضع الفاتورة {facture.reference} في حالة نزاع.")

    logger.info(
        "FactureFournisseur pk=%s statut changed to '%s' by '%s'.",
        pk,
        facture.statut,
        request.user,
    )
    return redirect("achats:facture_fournisseur_detail", pk=pk)


# ===========================================================================
# Facture Fournisseur — Delete (cascade, admin-only)
# ===========================================================================


@login_required(login_url=LOGIN_URL)
@require_POST
def facture_fournisseur_delete(request, pk):
    """
    ADMIN-ONLY hard delete: remove a FactureFournisseur together with every
    BL it includes and every ReglementFournisseur that paid it.

    This intentionally bypasses BR-FAF-03/BR-BLF-02 (BL lock) and BR-REG-06
    (règlement immutability) — restricted to admins (is_staff or
    profile.role=="admin") and POST-only. See
    achats.utils.supprimer_facture_fournisseur_cascade for the full cascade
    and its side effects, including on OTHER invoices a shared règlement
    also paid.
    """
    try:
        is_admin = request.user.is_staff or request.user.profile.role == "admin"
    except Exception:
        is_admin = request.user.is_staff

    if not is_admin:
        messages.error(request, "غير مسموح: هذا الإجراء متاح للمدراء فقط.")
        return redirect("achats:facture_fournisseur_detail", pk=pk)

    facture = branche_object_or_404(request, FactureFournisseur, pk=pk)

    from achats.utils import supprimer_facture_fournisseur_cascade

    try:
        summary = supprimer_facture_fournisseur_cascade(facture)
    except Exception as exc:
        logger.exception("Error deleting FactureFournisseur pk=%s: %s", pk, exc)
        messages.error(request, f"خطأ أثناء الحذف: {exc}")
        return redirect("achats:facture_fournisseur_detail", pk=pk)

    msg = (
        f"تم حذف الفاتورة {summary['facture_reference']} نهائيًا مع "
        f"{len(summary['bls_references'])} وصل استلام و "
        f"{len(summary['reglements_references'])} تسوية مرتبطة."
    )
    if summary["factures_tierces_impactees"]:
        autres = ", ".join(sorted(set(summary["factures_tierces_impactees"])))
        msg += (
            f" تنبيه: تأثرت فواتير أخرى ({autres}) لأنها شاركت في نفس "
            "التسويات المحذوفة — يرجى مراجعة أرصدتها."
        )
    messages.success(request, msg)
    logger.info(
        "FactureFournisseur %s deleted (admin cascade) by '%s'. Summary: %s",
        summary["facture_reference"],
        request.user,
        summary,
    )
    return redirect("achats:facture_fournisseur_list")


# ===========================================================================
# Règlement Fournisseur — List
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def reglement_fournisseur_list(request):
    """
    Payment list with search (supplier name, reference) and date filter.

    v1.4 (BR-BRA-01/02): Vue par Branche shows only the active branche's
    payments; Vue Globale shows every branche's payments combined.
    """
    branche = get_active_branche(request)
    qs = ReglementFournisseur.objects.select_related(
        "fournisseur", "branche", "created_by"
    ).order_by("-date_reglement", "-created_at")
    if branche is not None:
        qs = qs.filter(branche=branche)

    fournisseur_pk = request.GET.get("fournisseur", "")
    if fournisseur_pk:
        qs = qs.filter(fournisseur_id=fournisseur_pk)

    date_debut = request.GET.get("date_debut", "")
    date_fin = request.GET.get("date_fin", "")
    if date_debut:
        qs = qs.filter(date_reglement__gte=date_debut)
    if date_fin:
        qs = qs.filter(date_reglement__lte=date_fin)

    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(
            Q(fournisseur__nom__icontains=q) | Q(reference_paiement__icontains=q)
        )

    page = _paginate(qs, request.GET.get("page"))
    fournisseurs = Fournisseur.objects.filter(actif=True).order_by("nom")

    return render(
        request,
        "achats/reglement_fournisseur_list.html",
        {
            "page": page,
            "q": q,
            "fournisseur_pk": fournisseur_pk,
            "date_debut": date_debut,
            "date_fin": date_fin,
            "fournisseurs": fournisseurs,
            "active_branche": branche,
            "title": "تسويات الموردين",
        },
    )


# ===========================================================================
# Règlement Fournisseur — Create
# ===========================================================================


@login_required(login_url=LOGIN_URL)
@require_branche_context
def reglement_fournisseur_create(request):
    """
    Record a supplier payment.

    The FIFO allocation engine (achats.utils.appliquer_reglement_fifo) runs
    automatically via the post_save signal after the record is committed,
    scoped to this règlement's branche (BR-BRA-01) — it only settles open
    invoices in the same branche.

    BR-REG-06: règlements are immutable — no edit view is provided.
    BR-BRA-04: Vue Globale cannot reach this view (@require_branche_context);
               the active branche is pre-selected and locked on the form.

    Optional GET params:
      ?fournisseur=<pk>  — pre-select supplier
      ?facture=<pk>      — pre-fill montant with facture.reste_a_payer
    """
    branche = get_active_branche(request)

    # Pre-select supplier via ?fournisseur=<pk>
    fournisseur_pk = request.GET.get("fournisseur", "")
    fournisseur = None
    if fournisseur_pk:
        fournisseur = get_object_or_404(Fournisseur, pk=fournisseur_pk, actif=True)

    # Optional facture context — pre-fill montant with reste_a_payer.
    # BR-BRA-01: only a facture in the active branche can be pre-filled.
    facture_pk = request.GET.get("facture", "")
    facture_obj = None
    facture_reste = None
    if facture_pk:
        try:
            facture_obj = FactureFournisseur.objects.get(pk=facture_pk, branche=branche)
            facture_reste = facture_obj.reste_a_payer
            # Auto-resolve fournisseur from facture if not already set
            if not fournisseur and facture_obj.fournisseur.actif:
                fournisseur = facture_obj.fournisseur
        except FactureFournisseur.DoesNotExist:
            pass

    if request.method == "POST":
        form = ReglementFournisseurForm(
            request.POST, request.FILES, fournisseur=fournisseur, branche=branche
        )
        pj_formset = build_piece_jointe_formset(
            ReglementFournisseurPieceJointeFormSet, request, prefix="pj"
        )
        if form.is_valid() and pj_formset.is_valid():
            try:
                reglement = form.save(commit=False)
                reglement.created_by = request.user
                reglement.save()  # triggers post_save → FIFO allocation
                pj_formset.instance = reglement
                pj_formset.save()

                # Refresh from DB so the template can show updated allocation info
                reglement.refresh_from_db()

                messages.success(
                    request,
                    f"تم تسجيل تسوية بقيمة {reglement.montant} دج لـ {reglement.fournisseur.nom}. تم تطبيق توزيعات FIFO.",
                )
                logger.info(
                    "ReglementFournisseur pk=%s (%s DZD, %s) created by '%s'.",
                    reglement.pk,
                    reglement.montant,
                    reglement.fournisseur.nom,
                    request.user,
                )
                return redirect("achats:reglement_fournisseur_detail", pk=reglement.pk)

            except Exception as exc:
                logger.exception("Error creating ReglementFournisseur: %s", exc)
                messages.error(request, f"خطأ أثناء التسجيل: {exc}")

        else:
            messages.error(request, "يرجى تصحيح الأخطاء.")

    else:
        # Show the supplier's current debt for reference
        form = ReglementFournisseurForm(fournisseur=fournisseur, branche=branche)
        pj_formset = build_piece_jointe_formset(
            ReglementFournisseurPieceJointeFormSet, request, prefix="pj"
        )

    # Debt summary for the sidebar — scoped to the active branche, mirroring
    # the FIFO engine's own scope (BR-BRA-01).
    solde = None
    if fournisseur:
        try:
            from achats.utils import get_fournisseur_solde

            solde = get_fournisseur_solde(fournisseur, branche=branche)
        except Exception:
            pass

    return render(
        request,
        "achats/reglement_fournisseur_form.html",
        {
            "form": form,
            "pj_formset": pj_formset,
            "fournisseur": fournisseur,
            "solde": solde,
            "facture_obj": facture_obj,
            "facture_reste": facture_reste,
            "active_branche": branche,
            "title": "تسوية مورد جديدة",
            "action_label": "حفظ التسوية",
        },
    )


# ===========================================================================
# Règlement Fournisseur — Detail
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def reglement_fournisseur_detail(request, pk):
    """
    Payment detail: amount, mode, and all allocation lines created by the
    FIFO engine, plus any overpayment acompte.
    """
    reglement = branche_object_or_404(
        request,
        ReglementFournisseur.objects.select_related("fournisseur", "created_by"),
        pk=pk,
    )
    allocations = reglement.allocations.select_related("facture").order_by(
        "facture__date_facture"
    )
    try:
        acompte = reglement.acompte
    except AcompteFournisseur.DoesNotExist:
        acompte = None
    pieces_jointes = reglement.pieces_jointes.select_related("uploaded_by").order_by(
        "-created_at"
    )
    pj_formset = build_piece_jointe_formset(
        ReglementFournisseurPieceJointeFormSet, request, instance=reglement, prefix="pj"
    )

    return render(
        request,
        "achats/reglement_fournisseur_detail.html",
        {
            "reglement": reglement,
            "allocations": allocations,
            "acompte": acompte,
            "pieces_jointes": pieces_jointes,
            "pj_formset": pj_formset,
            "title": f"تسوية — {reglement.fournisseur.nom} ({reglement.date_reglement})",
        },
    )


# ===========================================================================
# Règlement Fournisseur — Ajouter des pièces jointes (immutable — BR-REG-06)
# ===========================================================================


@login_required(login_url=LOGIN_URL)
@require_POST
def reglement_fournisseur_ajouter_piece_jointe(request, pk):
    """
    ReglementFournisseur rows are immutable (BR-REG-06 — no edit view), so
    proof documents added after creation go through this dedicated
    POST-only action.
    """
    reglement = branche_object_or_404(request, ReglementFournisseur, pk=pk)
    pj_formset = build_piece_jointe_formset(
        ReglementFournisseurPieceJointeFormSet, request, instance=reglement, prefix="pj"
    )
    if pj_formset.is_valid():
        pj_formset.save()
        messages.success(request, "تم إضافة المرفقات.")
    else:
        messages.error(request, "يرجى تصحيح الأخطاء في المرفقات.")
    return redirect("achats:reglement_fournisseur_detail", pk=pk)


# ===========================================================================
# Acompte Fournisseur — List
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def acompte_fournisseur_list(request):
    """
    List of overpayment credits created by the FIFO engine.
    Filterable by supplier and utilise status.

    v1.4 (BR-BRA-01/02): Vue par Branche shows only the active branche's
    credits; Vue Globale shows every branche's credits combined.
    """
    branche = get_active_branche(request)
    qs = AcompteFournisseur.objects.select_related(
        "fournisseur", "branche", "reglement"
    ).order_by("-date")
    if branche is not None:
        qs = qs.filter(branche=branche)

    fournisseur_pk = request.GET.get("fournisseur", "")
    if fournisseur_pk:
        qs = qs.filter(fournisseur_id=fournisseur_pk)

    utilise = request.GET.get("utilise", "")
    if utilise == "0":
        qs = qs.filter(utilise=False)
    elif utilise == "1":
        qs = qs.filter(utilise=True)

    page = _paginate(qs, request.GET.get("page"))
    fournisseurs = Fournisseur.objects.filter(actif=True).order_by("nom")

    return render(
        request,
        "achats/acompte_fournisseur_list.html",
        {
            "page": page,
            "fournisseur_pk": fournisseur_pk,
            "utilise": utilise,
            "fournisseurs": fournisseurs,
            "active_branche": branche,
            "title": "السلف المسبقة للموردين",
        },
    )


# ===========================================================================
# Acompte Fournisseur — Detail
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def acompte_fournisseur_detail(request, pk):
    """BR-BRA-02: the acompte must belong to the request's active branche."""
    acompte = branche_object_or_404(
        request,
        AcompteFournisseur.objects.select_related(
            "fournisseur", "reglement", "branche"
        ),
        pk=pk,
    )
    pieces_jointes = acompte.pieces_jointes.select_related("uploaded_by").order_by(
        "-created_at"
    )
    pj_formset = build_piece_jointe_formset(
        AcompteFournisseurPieceJointeFormSet, request, instance=acompte, prefix="pj"
    )
    return render(
        request,
        "achats/acompte_fournisseur_detail.html",
        {
            "acompte": acompte,
            "pieces_jointes": pieces_jointes,
            "pj_formset": pj_formset,
            "active_branche": get_active_branche(request),
            "title": f"دفعة مسبقة — {acompte.fournisseur.nom}",
        },
    )


# ===========================================================================
# Acompte Fournisseur — Ajouter des pièces jointes
#
# AcompteFournisseur rows are created automatically by the FIFO engine
# (no create/edit view of their own), so proof documents (e.g. an overpaid
# règlement's supporting doc) are attached after the fact here.
# ===========================================================================


@login_required(login_url=LOGIN_URL)
@require_POST
def acompte_fournisseur_ajouter_piece_jointe(request, pk):
    acompte = branche_object_or_404(request, AcompteFournisseur, pk=pk)
    pj_formset = build_piece_jointe_formset(
        AcompteFournisseurPieceJointeFormSet, request, instance=acompte, prefix="pj"
    )
    if pj_formset.is_valid():
        pj_formset.save()
        messages.success(request, "تم إضافة المرفقات.")
    else:
        messages.error(request, "يرجى تصحيح الأخطاء في المرفقات.")
    return redirect("achats:acompte_fournisseur_detail", pk=pk)


# ===========================================================================
# Tableau de bord fournisseur (financial snapshot)
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def fournisseur_tableau_de_bord(request, pk):
    """
    Financial dashboard for one supplier:
      - debt, available credit, overdue invoices
      - aged-debt breakdown (buckets)
      - recent payments

    v1.4 (§3.5.3 ¶4, §3.5.5): Fournisseur stays global but its BL/Facture/
    Règlement/Acompte are branch-scoped, so this dashboard reflects Vue par
    Branche by default (exactly what that branch's chef de branche sees) and
    sums every branche in Vue Globale.
    """
    from achats.utils import (
        get_fournisseur_solde,
        get_supplier_aging_buckets,
        get_autorisations_en_attente,
        get_autorisations_expirees,
    )

    branche = get_active_branche(request)

    fournisseur = get_object_or_404(Fournisseur, pk=pk)
    solde = get_fournisseur_solde(fournisseur, branche=branche)
    aging = get_supplier_aging_buckets(fournisseur=fournisseur, branche=branche)

    reglements_recents = ReglementFournisseur.objects.filter(
        fournisseur=fournisseur
    ).order_by("-date_reglement")
    bls_ouverts = BLFournisseur.objects.filter(
        fournisseur=fournisseur,
        statut=BLFournisseur.STATUT_RECU,
    ).order_by("-date_bl")
    if branche is not None:
        reglements_recents = reglements_recents.filter(branche=branche)
        bls_ouverts = bls_ouverts.filter(branche=branche)
    reglements_recents = reglements_recents[:10]

    autorisations_en_attente = get_autorisations_en_attente(
        fournisseur=fournisseur, branche=branche
    )
    autorisations_expirees = [
        a
        for a in get_autorisations_expirees(branche=branche)
        if a.fournisseur_id == fournisseur.pk
    ]

    return render(
        request,
        "achats/fournisseur_tableau_de_bord.html",
        {
            "fournisseur": fournisseur,
            "solde": solde,
            "aging": aging[0] if aging else None,
            "reglements_recents": reglements_recents,
            "bls_ouverts": bls_ouverts,
            "autorisations_en_attente": autorisations_en_attente,
            "autorisations_expirees": autorisations_expirees,
            "active_branche": branche,
            "title": f"لوحة تحكم — {fournisseur.nom}",
        },
    )


# ===========================================================================
# AJAX helpers
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def bl_lignes_total_json(request):
    """
    Return the computed montant_total for a list of BL PKs.
    Called by the invoice-creation form's JavaScript to show a running total
    before the user submits.

    Query param: ?bls=1,2,3  (comma-separated BL PKs)

    Returns:
        {"montant_total": "12345.67", "lignes": [...]}

    BR-BRA-02: BLs outside the request's active branche are silently
    excluded — a chef de branche/opérateur must never total another
    branche's deliveries even by guessing PKs in the request.
    """
    pks_raw = request.GET.get("bls", "")
    if not pks_raw:
        return JsonResponse({"montant_total": "0.00", "lignes": []})

    try:
        pks = [int(p) for p in pks_raw.split(",") if p.strip().isdigit()]
    except ValueError:
        return JsonResponse({"error": "Paramètre 'bls' invalide."}, status=400)

    bls = BLFournisseur.objects.filter(pk__in=pks).prefetch_related("lignes__intrant")
    branche = get_active_branche(request)
    if branche is not None:
        bls = bls.filter(branche=branche)

    total = Decimal("0")
    lignes_data = []
    for bl in bls:
        for ligne in bl.lignes.all():
            lt = ligne.montant_total
            total += lt
            lignes_data.append(
                {
                    "bl_reference": bl.reference,
                    "intrant": ligne.intrant.designation,
                    "quantite": str(ligne.quantite),
                    "prix_unitaire": str(ligne.prix_unitaire),
                    "montant_total": str(lt),
                }
            )

    return JsonResponse(
        {
            "montant_total": str(total),
            "lignes": lignes_data,
        }
    )


@login_required(login_url=LOGIN_URL)
def fournisseur_dette_json(request, pk):
    """
    Return current debt summary for a supplier as JSON.
    Used on the règlement creation form to display live balance.

    v1.4 (§3.5.3 ¶4): a règlement is created within one branche, so the
    balance shown here is that branche's own debt to the supplier (or the
    Vue Globale total when no branche is active).

    Returns:
        {"dette_globale": "...", "acompte_disponible": "...",
         "nb_factures_ouvertes": N}
    """
    fournisseur = get_object_or_404(Fournisseur, pk=pk)
    branche = get_active_branche(request)
    try:
        from achats.utils import get_fournisseur_solde

        solde = get_fournisseur_solde(fournisseur, branche=branche)
        factures_list = [
            {
                "pk": f.pk,
                "reference": f.reference,
                "reste_a_payer": str(f.reste_a_payer),
                "date_facture": f.date_facture.strftime("%d/%m/%Y"),
                "est_en_retard": f.est_en_retard,
            }
            for f in solde["factures_ouvertes"]
        ]
        data = {
            "dette_globale": str(solde["dette_globale"]),
            "acompte_disponible": str(solde["acompte_disponible"]),
            "nb_factures_ouvertes": len(factures_list),
            "nb_factures_retard": solde["nb_factures_retard"],
            "factures_ouvertes": factures_list,
        }
    except Exception as exc:
        logger.exception("fournisseur_dette_json error for pk=%s: %s", pk, exc)
        data = {
            "dette_globale": "0.00",
            "acompte_disponible": "0.00",
            "nb_factures_ouvertes": 0,
            "nb_factures_retard": 0,
        }
    return JsonResponse(data)
