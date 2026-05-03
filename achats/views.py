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
    BLFournisseurForm,
    BLFournisseurLigneFormSet,
    FactureFournisseurForm,
    ReglementFournisseurForm,
)
from achats.models import (
    AcompteFournisseur,
    AllocationReglement,
    BLFournisseur,
    BLFournisseurLigne,
    FactureFournisseur,
    ReglementFournisseur,
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


def _auto_reference_bl():
    """Return the next BLF reference without committing."""
    from achats.utils import generer_reference_bl_fournisseur

    return generer_reference_bl_fournisseur()


def _auto_reference_facture():
    """Return the next FRN reference without committing."""
    from achats.utils import generer_reference_facture_fournisseur

    return generer_reference_facture_fournisseur()


# ===========================================================================
# BL Fournisseur — List
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def bl_fournisseur_list(request):
    """
    BL list with search (reference, supplier name), statut filter, and
    optional supplier filter passed as ?fournisseur=<pk>.
    """
    qs = BLFournisseur.objects.select_related("fournisseur", "created_by").order_by(
        "-date_bl", "-created_at"
    )

    # Statut filter
    statut = request.GET.get("statut", "")
    if statut:
        qs = qs.filter(statut=statut)

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
        )

    page = _paginate(qs, request.GET.get("page"))
    fournisseurs = Fournisseur.objects.filter(actif=True).order_by("nom")

    return render(
        request,
        "achats/bl_fournisseur_list.html",
        {
            "page": page,
            "q": q,
            "statut": statut,
            "fournisseur_pk": fournisseur_pk,
            "fournisseurs": fournisseurs,
            "statut_choices": BLFournisseur.STATUT_CHOICES,
            "title": "BL Fournisseurs",
        },
    )


# ===========================================================================
# BL Fournisseur — Create
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def bl_fournisseur_create(request, fournisseur_pk=None):
    """
    Create a new BL Fournisseur with its lines (inline formset).

    When called via the scoped URL (fournisseur_pk is set), the fournisseur
    field is pre-filled and hidden, mirroring bl_client_create_for_client.
    Reference is auto-generated and pre-filled; the user may override it.
    Saving the form + formset is wrapped in a DB transaction.
    """
    from intrants.models import Fournisseur as FournisseurModel

    fournisseur = (
        get_object_or_404(FournisseurModel, pk=fournisseur_pk)
        if fournisseur_pk
        else None
    )

    if request.method == "POST":
        form = BLFournisseurForm(request.POST, request.FILES)
        formset = BLFournisseurLigneFormSet(request.POST, prefix="lignes")

        if form.is_valid() and formset.is_valid():
            try:
                with transaction.atomic():
                    bl = form.save(commit=False)
                    bl.created_by = request.user
                    bl.save()
                    formset.instance = bl
                    formset.save()

                messages.success(
                    request,
                    f"BL {bl.reference} créé avec succès "
                    f"({bl.lignes.count()} ligne(s)).",
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
                messages.error(request, f"Erreur lors de la création : {exc}")

        else:
            messages.error(request, "Veuillez corriger les erreurs dans le formulaire.")

    else:
        initial_ref = _auto_reference_bl()
        initial = {"reference": initial_ref}
        if fournisseur:
            initial["fournisseur"] = fournisseur
        form = BLFournisseurForm(initial=initial)
        if fournisseur:
            form.fields["fournisseur"].widget = forms.HiddenInput()
            form.fields["fournisseur"].initial = fournisseur
        formset = BLFournisseurLigneFormSet(prefix="lignes")

    return render(
        request,
        "achats/bl_fournisseur_form.html",
        {
            "form": form,
            "formset": formset,
            "title": "Nouveau BL Fournisseur",
            "action_label": "Créer",
            "fournisseur": fournisseur,
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
    """
    bl = get_object_or_404(
        BLFournisseur.objects.select_related("fournisseur"),
        pk=pk,
    )

    if bl.est_verrouille:
        messages.error(
            request,
            f"BR-BLF-02 : le BL {bl.reference} est verrouillé (statut Facturé) "
            "et ne peut plus être modifié.",
        )
        return redirect("achats:bl_fournisseur_detail", pk=pk)

    if request.method == "POST":
        form = BLFournisseurForm(request.POST, request.FILES, instance=bl)
        formset = BLFournisseurLigneFormSet(request.POST, instance=bl, prefix="lignes")

        if form.is_valid() and formset.is_valid():
            try:
                with transaction.atomic():
                    form.save()
                    formset.save()

                messages.success(request, f"BL {bl.reference} mis à jour.")
                logger.info("BLFournisseur pk=%s updated by '%s'.", pk, request.user)
                return redirect("achats:bl_fournisseur_detail", pk=pk)

            except Exception as exc:
                logger.exception("Error updating BLFournisseur pk=%s: %s", pk, exc)
                messages.error(request, f"Erreur lors de la mise à jour : {exc}")

        else:
            messages.error(request, "Veuillez corriger les erreurs.")

    else:
        form = BLFournisseurForm(instance=bl)
        formset = BLFournisseurLigneFormSet(instance=bl, prefix="lignes")

    return render(
        request,
        "achats/bl_fournisseur_form.html",
        {
            "form": form,
            "formset": formset,
            "object": bl,
            "title": f"Modifier BL — {bl.reference}",
            "action_label": "Enregistrer",
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
    """
    bl = get_object_or_404(
        BLFournisseur.objects.select_related("fournisseur", "created_by"),
        pk=pk,
    )
    lignes = bl.lignes.select_related("intrant__stock").all()
    factures = bl.factures.order_by("-date_facture")

    # Determine admin status: staff OR profile role == "admin"
    try:
        is_admin = request.user.is_staff or request.user.profile.role == "admin"
    except Exception:
        is_admin = request.user.is_staff

    # Statuts the user may switch to (FACTURE is system-only)
    STATUT_TRANSITIONS = {
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
        ],
        BLFournisseur.STATUT_FACTURE: [],
    }

    next_statut_label = None
    next_statut_value = None
    if bl.statut == BLFournisseur.STATUT_BROUILLON:
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
            "montant_total": bl.montant_total,
            "title": f"BL {bl.reference}",
            "is_admin": is_admin,
            "statut_transitions": STATUT_TRANSITIONS.get(bl.statut, []),
            "next_statut_value": next_statut_value,
            "next_statut_label": next_statut_label,
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
    bl = get_object_or_404(BLFournisseur, pk=pk)

    if bl.est_verrouille:
        messages.error(
            request,
            f"BR-BLF-02 : le BL {bl.reference} est verrouillé (Facturé) "
            "et ne peut pas être modifié.",
        )
        return redirect("achats:bl_fournisseur_detail", pk=pk)

    ALLOWED_TARGETS = {
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
            f"Transition de statut invalide : « {bl.get_statut_display()} » "
            f"→ « {new_statut} » non autorisée.",
        )
        return redirect("achats:bl_fournisseur_detail", pk=pk)

    old_display = bl.get_statut_display()
    bl.statut = new_statut
    bl.save(update_fields=["statut", "updated_at"])

    new_display = bl.get_statut_display()
    messages.success(
        request,
        f"Statut du BL {bl.reference} changé : {old_display} → {new_display}.",
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

    bl = get_object_or_404(
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
    bl = get_object_or_404(BLFournisseur, pk=pk)

    if bl.statut != BLFournisseur.STATUT_BROUILLON:
        messages.error(
            request,
            f"Seuls les BLs en statut Brouillon peuvent être supprimés. "
            f"BL {bl.reference} est en statut « {bl.get_statut_display()} ».",
        )
        return redirect("achats:bl_fournisseur_detail", pk=pk)

    ref = bl.reference
    bl.delete()
    messages.success(request, f"BL {ref} supprimé.")
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
    """
    qs = FactureFournisseur.objects.select_related("fournisseur").order_by(
        "-date_facture", "-created_at"
    )

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
            "title": "Factures Fournisseurs",
        },
    )


# ===========================================================================
# Facture Fournisseur — Create
# ===========================================================================


@login_required(login_url=LOGIN_URL)
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
    """
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
                "title": "Nouvelle facture — Choisir un fournisseur",
            },
        )

    if request.method == "POST":
        form = FactureFournisseurForm(
            request.POST,
            fournisseur=fournisseur,
        )
        if form.is_valid():
            try:
                with transaction.atomic():
                    facture = form.save(commit=False)
                    facture.created_by = request.user
                    # montant_total is set to 0 here; the post_save signal
                    # (facture_fournisseur_post_save) will recompute it from BL
                    # lines immediately after the M2M relation is saved.
                    facture.save()
                    form.save_m2m()  # persist bls M2M

                messages.success(
                    request,
                    f"Facture {facture.reference} créée. "
                    f"Montant calculé : {facture.montant_total} DZD.",
                )
                logger.info(
                    "FactureFournisseur pk=%s created by '%s'.",
                    facture.pk,
                    request.user,
                )
                return redirect("achats:facture_fournisseur_detail", pk=facture.pk)

            except Exception as exc:
                logger.exception("Error creating FactureFournisseur: %s", exc)
                messages.error(request, f"Erreur lors de la création : {exc}")

        else:
            messages.error(request, "Veuillez corriger les erreurs.")

    else:
        # Step 2: supplier selected, show form pre-filtered
        initial_ref = _auto_reference_facture()
        form = FactureFournisseurForm(
            fournisseur=fournisseur,
            initial={"reference": initial_ref},
        )

    # Expose available BL amounts for the template's running total widget
    bls_recu = []
    if fournisseur:
        bls_recu = (
            BLFournisseur.objects.filter(
                fournisseur=fournisseur,
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
            "fournisseur": fournisseur,
            "bls_recu": bls_recu,
            "title": "Nouvelle facture fournisseur",
            "action_label": "Créer la facture",
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
    facture = get_object_or_404(
        FactureFournisseur.objects.select_related("fournisseur", "created_by"),
        pk=pk,
    )
    bls = facture.bls.prefetch_related("lignes__intrant").order_by("date_bl")
    allocations = facture.allocations.select_related("reglement").order_by(
        "reglement__date_reglement"
    )

    return render(
        request,
        "achats/facture_fournisseur_detail.html",
        {
            "facture": facture,
            "bls": bls,
            "allocations": allocations,
            "title": f"Facture {facture.reference}",
        },
    )


# ===========================================================================
# Facture Fournisseur — Print
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def facture_fournisseur_print(request, pk):
    """Printable invoice — @media print CSS handled in template."""
    from core.models import CompanyInfo

    facture = get_object_or_404(
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
    facture = get_object_or_404(FactureFournisseur, pk=pk)

    if facture.statut == FactureFournisseur.STATUT_PAYE:
        messages.error(
            request, "Une facture entièrement payée ne peut pas être mise en litige."
        )
        return redirect("achats:facture_fournisseur_detail", pk=pk)

    if facture.statut == FactureFournisseur.STATUT_EN_LITIGE:
        # Recompute the correct status from current balance
        facture.statut = (
            FactureFournisseur.STATUT_NON_PAYE
        )  # recalculer_solde will fix it
        facture.recalculer_solde()
        messages.success(request, f"Facture {facture.reference} retirée du litige.")
    else:
        facture.statut = FactureFournisseur.STATUT_EN_LITIGE
        facture.save(update_fields=["statut", "updated_at"])
        messages.success(request, f"Facture {facture.reference} marquée En litige.")

    logger.info(
        "FactureFournisseur pk=%s statut changed to '%s' by '%s'.",
        pk,
        facture.statut,
        request.user,
    )
    return redirect("achats:facture_fournisseur_detail", pk=pk)


# ===========================================================================
# Règlement Fournisseur — List
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def reglement_fournisseur_list(request):
    """
    Payment list with search (supplier name, reference) and date filter.
    """
    qs = ReglementFournisseur.objects.select_related(
        "fournisseur", "created_by"
    ).order_by("-date_reglement", "-created_at")

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
            "title": "Règlements Fournisseurs",
        },
    )


# ===========================================================================
# Règlement Fournisseur — Create
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def reglement_fournisseur_create(request):
    """
    Record a supplier payment.

    The FIFO allocation engine (achats.utils.appliquer_reglement_fifo) runs
    automatically via the post_save signal after the record is committed.

    BR-REG-06: règlements are immutable — no edit view is provided.

    Optional GET params:
      ?fournisseur=<pk>  — pre-select supplier
      ?facture=<pk>      — pre-fill montant with facture.reste_a_payer
    """
    # Pre-select supplier via ?fournisseur=<pk>
    fournisseur_pk = request.GET.get("fournisseur", "")
    fournisseur = None
    if fournisseur_pk:
        fournisseur = get_object_or_404(Fournisseur, pk=fournisseur_pk, actif=True)

    # Optional facture context — pre-fill montant with reste_a_payer
    facture_pk = request.GET.get("facture", "")
    facture_obj = None
    facture_reste = None
    if facture_pk:
        try:
            facture_obj = FactureFournisseur.objects.get(pk=facture_pk)
            facture_reste = facture_obj.reste_a_payer
            # Auto-resolve fournisseur from facture if not already set
            if not fournisseur and facture_obj.fournisseur.actif:
                fournisseur = facture_obj.fournisseur
        except FactureFournisseur.DoesNotExist:
            pass

    if request.method == "POST":
        form = ReglementFournisseurForm(request.POST, fournisseur=fournisseur)
        if form.is_valid():
            try:
                reglement = form.save(commit=False)
                reglement.created_by = request.user
                reglement.save()  # triggers post_save → FIFO allocation

                # Refresh from DB so the template can show updated allocation info
                reglement.refresh_from_db()

                messages.success(
                    request,
                    f"Règlement de {reglement.montant} DZD enregistré pour "
                    f"{reglement.fournisseur.nom}. Allocations FIFO appliquées.",
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
                messages.error(request, f"Erreur lors de l'enregistrement : {exc}")

        else:
            messages.error(request, "Veuillez corriger les erreurs.")

    else:
        # Show the supplier's current debt for reference
        form = ReglementFournisseurForm(fournisseur=fournisseur)

    # Debt summary for the sidebar
    solde = None
    if fournisseur:
        try:
            from achats.utils import get_fournisseur_solde

            solde = get_fournisseur_solde(fournisseur)
        except Exception:
            pass

    return render(
        request,
        "achats/reglement_fournisseur_form.html",
        {
            "form": form,
            "fournisseur": fournisseur,
            "solde": solde,
            "facture_obj": facture_obj,
            "facture_reste": facture_reste,
            "title": "Nouveau règlement fournisseur",
            "action_label": "Enregistrer le règlement",
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
    reglement = get_object_or_404(
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

    return render(
        request,
        "achats/reglement_fournisseur_detail.html",
        {
            "reglement": reglement,
            "allocations": allocations,
            "acompte": acompte,
            "title": f"Règlement — {reglement.fournisseur.nom} "
            f"({reglement.date_reglement})",
        },
    )


# ===========================================================================
# Acompte Fournisseur — List
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def acompte_fournisseur_list(request):
    """
    List of overpayment credits created by the FIFO engine.
    Filterable by supplier and utilise status.
    """
    qs = AcompteFournisseur.objects.select_related("fournisseur", "reglement").order_by(
        "-date"
    )

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
            "title": "Acomptes Fournisseurs",
        },
    )


# ===========================================================================
# Acompte Fournisseur — Detail
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def acompte_fournisseur_detail(request, pk):
    acompte = get_object_or_404(
        AcompteFournisseur.objects.select_related("fournisseur", "reglement"),
        pk=pk,
    )
    return render(
        request,
        "achats/acompte_fournisseur_detail.html",
        {
            "acompte": acompte,
            "title": f"Acompte — {acompte.fournisseur.nom}",
        },
    )


# ===========================================================================
# Tableau de bord fournisseur (financial snapshot)
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def fournisseur_tableau_de_bord(request, pk):
    """
    Financial dashboard for one supplier:
      - global debt, available credit, overdue invoices
      - aged-debt breakdown (buckets)
      - recent payments
    """
    from achats.utils import get_fournisseur_solde, get_supplier_aging_buckets

    fournisseur = get_object_or_404(Fournisseur, pk=pk)
    solde = get_fournisseur_solde(fournisseur)
    aging = get_supplier_aging_buckets(fournisseur=fournisseur)

    reglements_recents = ReglementFournisseur.objects.filter(
        fournisseur=fournisseur
    ).order_by("-date_reglement")[:10]
    bls_ouverts = BLFournisseur.objects.filter(
        fournisseur=fournisseur,
        statut=BLFournisseur.STATUT_RECU,
    ).order_by("-date_bl")

    return render(
        request,
        "achats/fournisseur_tableau_de_bord.html",
        {
            "fournisseur": fournisseur,
            "solde": solde,
            "aging": aging[0] if aging else None,
            "reglements_recents": reglements_recents,
            "bls_ouverts": bls_ouverts,
            "title": f"Tableau de bord — {fournisseur.nom}",
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
    """
    pks_raw = request.GET.get("bls", "")
    if not pks_raw:
        return JsonResponse({"montant_total": "0.00", "lignes": []})

    try:
        pks = [int(p) for p in pks_raw.split(",") if p.strip().isdigit()]
    except ValueError:
        return JsonResponse({"error": "Paramètre 'bls' invalide."}, status=400)

    bls = BLFournisseur.objects.filter(pk__in=pks).prefetch_related("lignes__intrant")

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

    Returns:
        {"dette_globale": "...", "acompte_disponible": "...",
         "nb_factures_ouvertes": N}
    """
    fournisseur = get_object_or_404(Fournisseur, pk=pk)
    try:
        from achats.utils import get_fournisseur_solde

        solde = get_fournisseur_solde(fournisseur)
        data = {
            "dette_globale": str(solde["dette_globale"]),
            "acompte_disponible": str(solde["acompte_disponible"]),
            "nb_factures_ouvertes": solde["factures_ouvertes"].count(),
            "nb_factures_retard": solde["nb_factures_retard"],
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
