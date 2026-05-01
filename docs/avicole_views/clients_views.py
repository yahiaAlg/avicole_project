"""
clients/views.py

Function-based views for the full client AR (accounts-receivable) cycle:

  Client         : list, create, detail, edit, toggle active
  BLClient       : list, create, detail, edit, validate (BROUILLON → LIVRE),
                   delete (BROUILLON only), print
  FactureClient  : list, create, detail, print
  PaiementClient : list, create, detail, print
  Dashboard      : clients overview with receivable aging
  AJAX           : client financial snapshot endpoint

Business rules enforced here (complementing model.clean(), forms, and signals):
  BR-BLC-01  Stock decreases only on BROUILLON → LIVRE transition (signal).
  BR-BLC-02  BL cannot be validated if any line qty > available stock.
  BR-BLC-03  Facturé BLs are locked — no edit or delete.
  BR-FAC-01  Invoice HT total auto-computed from BL lines (signal on creation).
  BR-FAC-02  Only Livré BLs from the selected client may be invoiced.
  BR-FAC-03  Client manually selects which invoice(s) a payment applies to.

All write operations use Post-Redirect-Get.
State changes (validate BL, toggle active) are POST-only.
Print views render a dedicated template — no redirect.
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

from clients.forms import (
    BLClientForm,
    BLClientLigneFormSet,
    ClientForm,
    FactureClientForm,
    PaiementClientForm,
    get_allocation_forms,
)
from clients.models import (
    BLClient,
    BLClientLigne,
    Client,
    FactureClient,
    PaiementClient,
    PaiementClientAllocation,
)
from clients.utils import (
    appliquer_paiement_client,
    generer_reference_bl_client,
    generer_reference_facture_client,
    get_client_aging_buckets,
    get_client_solde,
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


def _assert_bl_editable(bl, request):
    """
    Return True if the BL is still editable (not locked).
    Add an error message and return False for Facturé BLs (BR-BLC-03).
    """
    if bl.est_verrouille:
        messages.error(
            request,
            f"BR-BLC-03 : le BL « {bl.reference} » est au statut Facturé "
            "et ne peut plus être modifié.",
        )
        return False
    return True


# ===========================================================================
# Client — List
# ===========================================================================

@login_required(login_url=LOGIN_URL)
def client_list(request):
    """
    List all clients.

    Filters:
      ?actif=0          — include inactive clients (default: active only)
      ?type_client=<v>  — filter by client type
      ?q=<search>       — search by nom, telephone, or wilaya
    """
    qs = Client.objects.order_by("nom")

    actif_param = request.GET.get("actif", "1")
    if actif_param != "0":
        qs = qs.filter(actif=True)

    type_client = request.GET.get("type_client", "")
    if type_client:
        qs = qs.filter(type_client=type_client)

    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(
            Q(nom__icontains=q)
            | Q(telephone__icontains=q)
            | Q(wilaya__icontains=q)
            | Q(contact_nom__icontains=q)
        )

    page = _paginate(qs, request.GET.get("page"))

    return render(request, "clients/client_list.html", {
        "page": page,
        "type_choices": Client.TYPE_CHOICES,
        "actif_param": actif_param,
        "q": q,
        "type_client_filter": type_client,
        "title": "Clients",
    })


# ===========================================================================
# Client — Create
# ===========================================================================

@login_required(login_url=LOGIN_URL)
def client_create(request):
    if request.method == "POST":
        form = ClientForm(request.POST)
        if form.is_valid():
            try:
                client = form.save()
                messages.success(request, f"Client « {client.nom} » créé avec succès.")
                logger.info(
                    "Client pk=%s ('%s') created by '%s'.",
                    client.pk, client.nom, request.user,
                )
                return redirect("clients:client_detail", pk=client.pk)
            except Exception as exc:
                logger.exception("Error creating Client: %s", exc)
                messages.error(request, f"Erreur lors de la création : {exc}")
        else:
            messages.error(request, "Veuillez corriger les erreurs ci-dessous.")
    else:
        form = ClientForm()

    return render(request, "clients/client_form.html", {
        "form": form,
        "title": "Nouveau client",
        "action_label": "Créer le client",
    })


# ===========================================================================
# Client — Detail
# ===========================================================================

@login_required(login_url=LOGIN_URL)
def client_detail(request, pk):
    """
    Full client detail: contact info, financial snapshot (solde, aging),
    recent BLs, open invoices, and payment history.
    """
    client = get_object_or_404(Client, pk=pk)
    solde = get_client_solde(client)

    bls_recents = (
        BLClient.objects
        .filter(client=client)
        .order_by("-date_bl", "-created_at")[:10]
    )
    factures_ouvertes = solde["factures_ouvertes"].select_related()[:20]
    paiements_recents = (
        PaiementClient.objects
        .filter(client=client)
        .order_by("-date_paiement", "-created_at")[:10]
    )

    return render(request, "clients/client_detail.html", {
        "client": client,
        "solde": solde,
        "bls_recents": bls_recents,
        "factures_ouvertes": factures_ouvertes,
        "paiements_recents": paiements_recents,
        "title": f"Client — {client.nom}",
    })


# ===========================================================================
# Client — Edit
# ===========================================================================

@login_required(login_url=LOGIN_URL)
def client_edit(request, pk):
    client = get_object_or_404(Client, pk=pk)

    if request.method == "POST":
        form = ClientForm(request.POST, instance=client)
        if form.is_valid():
            try:
                form.save()
                messages.success(request, f"Client « {client.nom} » mis à jour.")
                logger.info("Client pk=%s updated by '%s'.", client.pk, request.user)
                return redirect("clients:client_detail", pk=client.pk)
            except Exception as exc:
                logger.exception("Error updating Client pk=%s: %s", pk, exc)
                messages.error(request, f"Erreur lors de la mise à jour : {exc}")
        else:
            messages.error(request, "Veuillez corriger les erreurs ci-dessous.")
    else:
        form = ClientForm(instance=client)

    return render(request, "clients/client_form.html", {
        "form": form,
        "client": client,
        "title": f"Modifier — {client.nom}",
        "action_label": "Enregistrer les modifications",
    })


# ===========================================================================
# Client — Toggle Active  (POST-only)
# ===========================================================================

@login_required(login_url=LOGIN_URL)
@require_POST
def client_toggle_active(request, pk):
    """
    Soft-activate or soft-deactivate a client (never hard-delete).
    """
    client = get_object_or_404(Client, pk=pk)
    client.actif = not client.actif
    client.save(update_fields=["actif", "updated_at"])
    state = "activé" if client.actif else "désactivé"
    messages.success(request, f"Client « {client.nom} » {state}.")
    logger.info(
        "Client pk=%s toggled actif=%s by '%s'.",
        client.pk, client.actif, request.user,
    )
    return redirect("clients:client_detail", pk=client.pk)


# ===========================================================================
# BLClient — List
# ===========================================================================

@login_required(login_url=LOGIN_URL)
def bl_client_list(request):
    """
    List all BL Clients.

    Filters:
      ?statut=<val>      — brouillon / livre / facture / litige
      ?client=<pk>       — filter by client
      ?q=<search>        — search by reference
      ?date_debut, ?date_fin
    """
    from core.utils import date_range_from_params

    qs = (
        BLClient.objects
        .select_related("client", "created_by")
        .order_by("-date_bl", "-created_at")
    )

    statut = request.GET.get("statut", "")
    if statut:
        qs = qs.filter(statut=statut)

    client_pk = request.GET.get("client", "")
    if client_pk:
        qs = qs.filter(client_id=client_pk)

    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(
            Q(reference__icontains=q) | Q(client__nom__icontains=q)
        )

    date_debut, date_fin = date_range_from_params(
        request.GET.get("date_debut"), request.GET.get("date_fin")
    )
    if date_debut:
        qs = qs.filter(date_bl__gte=date_debut)
    if date_fin:
        qs = qs.filter(date_bl__lte=date_fin)

    page = _paginate(qs, request.GET.get("page"))
    clients = Client.objects.filter(actif=True).order_by("nom")

    return render(request, "clients/bl_client_list.html", {
        "page": page,
        "statut_choices": BLClient.STATUT_CHOICES,
        "clients": clients,
        "statut_filter": statut,
        "client_filter": client_pk,
        "q": q,
        "date_debut": date_debut,
        "date_fin": date_fin,
        "title": "BL Clients",
    })


# ===========================================================================
# BLClient — Create
# ===========================================================================

@login_required(login_url=LOGIN_URL)
def bl_client_create(request, client_pk=None):
    """
    Create a new BL Client (BROUILLON) with its product lines.

    Accepts an optional `client_pk` URL parameter to pre-select the client.
    The inline formset manages BLClientLigne records.
    Auto-generates the BL reference if the form's reference field is empty.
    """
    client = None
    if client_pk:
        client = get_object_or_404(Client, pk=client_pk)

    if request.method == "POST":
        form = BLClientForm(request.POST, client=client)
        formset = BLClientLigneFormSet(request.POST)

        if form.is_valid() and formset.is_valid():
            try:
                with transaction.atomic():
                    bl = form.save(commit=False)
                    bl.created_by = request.user
                    # Auto-generate reference if blank
                    if not bl.reference:
                        bl.reference = generer_reference_bl_client()
                    bl.statut = BLClient.STATUT_BROUILLON
                    bl.save()

                    formset.instance = bl
                    formset.save()

                messages.success(
                    request,
                    f"BL Client « {bl.reference} » créé (brouillon). "
                    "Validez-le pour déduire le stock.",
                )
                logger.info(
                    "BLClient pk=%s ('%s') created (BROUILLON) by '%s' "
                    "(client pk=%s).",
                    bl.pk, bl.reference, request.user, bl.client_id,
                )
                return redirect("clients:bl_client_detail", pk=bl.pk)

            except Exception as exc:
                logger.exception("Error creating BLClient: %s", exc)
                messages.error(request, f"Erreur lors de la création : {exc}")
        else:
            messages.error(
                request,
                "Veuillez corriger les erreurs ci-dessous (entête et/ou lignes).",
            )
    else:
        initial = {}
        if not client_pk:
            initial["reference"] = generer_reference_bl_client()
        form = BLClientForm(client=client, initial=initial)
        tmp_bl = BLClient()
        if client:
            tmp_bl.client = client
        formset = BLClientLigneFormSet(instance=tmp_bl)

    return render(request, "clients/bl_client_form.html", {
        "form": form,
        "formset": formset,
        "client": client,
        "title": "Nouveau BL Client",
        "action_label": "Enregistrer (brouillon)",
    })


# ===========================================================================
# BLClient — Detail
# ===========================================================================

@login_required(login_url=LOGIN_URL)
def bl_client_detail(request, pk):
    bl = get_object_or_404(
        BLClient.objects.select_related("client", "created_by"),
        pk=pk,
    )
    lignes = bl.lignes.select_related("produit_fini").all()
    factures = bl.factures.order_by("-date_facture")

    return render(request, "clients/bl_client_detail.html", {
        "bl": bl,
        "lignes": lignes,
        "factures": factures,
        "montant_total": bl.montant_total,
        "title": f"BL Client — {bl.reference}",
    })


# ===========================================================================
# BLClient — Edit
# ===========================================================================

@login_required(login_url=LOGIN_URL)
def bl_client_edit(request, pk):
    """
    Edit a BL Client header and its lines.
    Only BROUILLON and LITIGE BLs are editable (BR-BLC-03).
    """
    bl = get_object_or_404(BLClient.objects.select_related("client"), pk=pk)

    if not _assert_bl_editable(bl, request):
        return redirect("clients:bl_client_detail", pk=bl.pk)

    if request.method == "POST":
        form = BLClientForm(request.POST, instance=bl, client=bl.client)
        formset = BLClientLigneFormSet(request.POST, instance=bl)

        if form.is_valid() and formset.is_valid():
            try:
                with transaction.atomic():
                    form.save()
                    formset.save()

                messages.success(
                    request, f"BL Client « {bl.reference} » mis à jour."
                )
                logger.info(
                    "BLClient pk=%s updated by '%s'.", bl.pk, request.user
                )
                return redirect("clients:bl_client_detail", pk=bl.pk)

            except Exception as exc:
                logger.exception("Error updating BLClient pk=%s: %s", pk, exc)
                messages.error(request, f"Erreur lors de la mise à jour : {exc}")
        else:
            messages.error(
                request,
                "Veuillez corriger les erreurs ci-dessous (entête et/ou lignes).",
            )
    else:
        form = BLClientForm(instance=bl, client=bl.client)
        formset = BLClientLigneFormSet(instance=bl)

    return render(request, "clients/bl_client_form.html", {
        "form": form,
        "formset": formset,
        "bl": bl,
        "client": bl.client,
        "title": f"Modifier BL — {bl.reference}",
        "action_label": "Enregistrer les modifications",
    })


# ===========================================================================
# BLClient — Validate (BROUILLON → LIVRE)  POST-only
# ===========================================================================

@login_required(login_url=LOGIN_URL)
@require_POST
def bl_client_valider(request, pk):
    """
    Transition a BL Client from BROUILLON to LIVRE.

    BR-BLC-02: each line's quantity is checked against current stock before
               committing the transition.  The check is re-validated here
               atomically to guard against race conditions even though the
               form already validated at input time.
    BR-BLC-01: the post_save signal on BLClient applies the stock decrease
               and creates StockMouvement records automatically.
    """
    bl = get_object_or_404(BLClient.objects.select_related("client"), pk=pk)

    if bl.statut != BLClient.STATUT_BROUILLON:
        messages.warning(
            request,
            f"Le BL « {bl.reference} » est au statut « {bl.get_statut_display()} » "
            "et ne peut pas être re-validé.",
        )
        return redirect("clients:bl_client_detail", pk=bl.pk)

    lignes = bl.lignes.select_related("produit_fini__stock").all()
    if not lignes.exists():
        messages.error(
            request,
            f"Impossible de valider le BL « {bl.reference} » : "
            "aucune ligne produit enregistrée.",
        )
        return redirect("clients:bl_client_detail", pk=bl.pk)

    # BR-BLC-02: atomic stock check before committing the transition.
    with transaction.atomic():
        insuffisant = []
        for ligne in lignes:
            dispo = ligne.produit_fini.quantite_en_stock
            if ligne.quantite > dispo:
                insuffisant.append(
                    f"« {ligne.produit_fini.designation} » : "
                    f"demandé {ligne.quantite}, disponible {dispo} "
                    f"{ligne.produit_fini.unite_mesure}"
                )

        if insuffisant:
            messages.error(
                request,
                "BR-BLC-02 : stock insuffisant pour les produits suivants — "
                + " | ".join(insuffisant),
            )
            return redirect("clients:bl_client_detail", pk=bl.pk)

        try:
            # The post_save signal handles stock decrease on this save.
            bl.statut = BLClient.STATUT_LIVRE
            bl.save(update_fields=["statut", "updated_at"])

            messages.success(
                request,
                f"BL Client « {bl.reference} » validé (Livré). "
                "Le stock produits finis a été mis à jour.",
            )
            logger.info(
                "BLClient pk=%s ('%s') validated to LIVRE by '%s'.",
                bl.pk, bl.reference, request.user,
            )

        except Exception as exc:
            logger.exception(
                "Error validating BLClient pk=%s: %s", pk, exc
            )
            messages.error(request, f"Erreur lors de la validation : {exc}")

    return redirect("clients:bl_client_detail", pk=bl.pk)


# ===========================================================================
# BLClient — Delete (BROUILLON only)  POST-only
# ===========================================================================

@login_required(login_url=LOGIN_URL)
@require_POST
def bl_client_delete(request, pk):
    """
    Hard-delete a BL Client that is still in BROUILLON status.
    LIVRE, FACTURE, and LITIGE BLs may not be deleted.
    """
    bl = get_object_or_404(BLClient, pk=pk)

    if bl.statut != BLClient.STATUT_BROUILLON:
        messages.error(
            request,
            f"Seuls les BLs en brouillon peuvent être supprimés. "
            f"Le BL « {bl.reference} » est au statut « {bl.get_statut_display()} ».",
        )
        return redirect("clients:bl_client_detail", pk=bl.pk)

    reference = bl.reference
    client_pk = bl.client_id
    try:
        bl.delete()
        messages.success(request, f"BL Client « {reference} » supprimé.")
        logger.info(
            "BLClient '%s' deleted by '%s'.", reference, request.user
        )
    except Exception as exc:
        logger.exception("Error deleting BLClient pk=%s: %s", pk, exc)
        messages.error(request, f"Erreur lors de la suppression : {exc}")
        return redirect("clients:bl_client_detail", pk=pk)

    return redirect("clients:client_detail", pk=client_pk)


# ===========================================================================
# BLClient — Print
# ===========================================================================

@login_required(login_url=LOGIN_URL)
def bl_client_print(request, pk):
    """
    Print-optimised view for a single BL Client.
    Renders a dedicated template with @media print CSS — no PDF library.
    Available for LIVRE and FACTURE BLs only; BROUILLON BLs are excluded.
    """
    bl = get_object_or_404(
        BLClient.objects.select_related("client", "created_by"),
        pk=pk,
    )

    if bl.statut == BLClient.STATUT_BROUILLON:
        messages.warning(
            request,
            "Un BL en brouillon ne peut pas être imprimé. "
            "Validez-le d'abord.",
        )
        return redirect("clients:bl_client_detail", pk=bl.pk)

    lignes = bl.lignes.select_related("produit_fini").all()

    from core.models import CompanyInfo
    company = CompanyInfo.get_instance()

    return render(request, "clients/bl_client_print.html", {
        "bl": bl,
        "lignes": lignes,
        "montant_total": bl.montant_total,
        "company": company,
    })


# ===========================================================================
# FactureClient — List
# ===========================================================================

@login_required(login_url=LOGIN_URL)
def facture_client_list(request):
    """
    List all client invoices.

    Filters:
      ?statut=<val>      — non_payee / partiellement_payee / payee / en_litige
      ?client=<pk>       — filter by client
      ?retard=1          — overdue invoices only
      ?date_debut, ?date_fin
    """
    from core.utils import date_range_from_params
    import datetime

    qs = (
        FactureClient.objects
        .select_related("client", "created_by")
        .order_by("-date_facture", "-created_at")
    )

    statut = request.GET.get("statut", "")
    if statut:
        qs = qs.filter(statut=statut)

    client_pk = request.GET.get("client", "")
    if client_pk:
        qs = qs.filter(client_id=client_pk)

    if request.GET.get("retard") == "1":
        today = datetime.date.today()
        qs = qs.filter(
            date_echeance__lt=today,
        ).exclude(statut=FactureClient.STATUT_PAYEE)

    date_debut, date_fin = date_range_from_params(
        request.GET.get("date_debut"), request.GET.get("date_fin")
    )
    if date_debut:
        qs = qs.filter(date_facture__gte=date_debut)
    if date_fin:
        qs = qs.filter(date_facture__lte=date_fin)

    page = _paginate(qs, request.GET.get("page"))
    clients = Client.objects.filter(actif=True).order_by("nom")

    # Summary totals for the list header
    totals = qs.aggregate(
        total_ttc=Sum("montant_ttc"),
        total_regle=Sum("montant_regle"),
        total_rap=Sum("reste_a_payer"),
    )

    return render(request, "clients/facture_client_list.html", {
        "page": page,
        "statut_choices": FactureClient.STATUT_CHOICES,
        "clients": clients,
        "statut_filter": statut,
        "client_filter": client_pk,
        "date_debut": date_debut,
        "date_fin": date_fin,
        "totals": totals,
        "title": "Factures Clients",
    })


# ===========================================================================
# FactureClient — Create
# ===========================================================================

@login_required(login_url=LOGIN_URL)
def facture_client_create(request, client_pk=None):
    """
    Create a client invoice by selecting Livré BL Clients.

    BR-FAC-01: montant_ht/tva/ttc are computed from BL lines in the
               post_save signal — the view only needs to persist the header.
    BR-FAC-02: the form filters BLs to Livré BLs for the selected client.

    Accepts an optional `client_pk` URL parameter to pre-select the client.
    """
    client = None
    if client_pk:
        client = get_object_or_404(Client, pk=client_pk)

    if request.method == "POST":
        form = FactureClientForm(request.POST, client=client)

        if form.is_valid():
            try:
                with transaction.atomic():
                    facture = form.save(commit=False)
                    facture.created_by = request.user
                    # Auto-generate reference if blank
                    if not facture.reference:
                        facture.reference = generer_reference_facture_client()
                    # montant_ht/tva/ttc initialised to 0 here;
                    # the post_save signal recalculates and persists them.
                    facture.save()
                    # M2M must be saved after the instance has a PK
                    form.save_m2m()

                messages.success(
                    request,
                    f"Facture Client « {facture.reference} » créée. "
                    f"Montant TTC : {facture.montant_ttc} DZD.",
                )
                logger.info(
                    "FactureClient pk=%s ('%s') created by '%s' "
                    "(client pk=%s).",
                    facture.pk, facture.reference,
                    request.user, facture.client_id,
                )
                return redirect("clients:facture_client_detail", pk=facture.pk)

            except Exception as exc:
                logger.exception("Error creating FactureClient: %s", exc)
                messages.error(request, f"Erreur lors de la création : {exc}")
        else:
            messages.error(request, "Veuillez corriger les erreurs ci-dessous.")
    else:
        initial = {}
        if not client_pk:
            initial["reference"] = generer_reference_facture_client()
        form = FactureClientForm(client=client, initial=initial)

    return render(request, "clients/facture_client_form.html", {
        "form": form,
        "client": client,
        "title": "Nouvelle Facture Client",
        "action_label": "Créer la facture",
    })


# ===========================================================================
# FactureClient — Detail
# ===========================================================================

@login_required(login_url=LOGIN_URL)
def facture_client_detail(request, pk):
    facture = get_object_or_404(
        FactureClient.objects.select_related("client", "created_by"),
        pk=pk,
    )
    bls = facture.bls.prefetch_related("lignes__produit_fini").order_by("date_bl")
    allocations = facture.allocations.select_related(
        "paiement"
    ).order_by("-paiement__date_paiement")

    return render(request, "clients/facture_client_detail.html", {
        "facture": facture,
        "bls": bls,
        "allocations": allocations,
        "title": f"Facture — {facture.reference}",
    })


# ===========================================================================
# FactureClient — Print
# ===========================================================================

@login_required(login_url=LOGIN_URL)
def facture_client_print(request, pk):
    """
    Print-optimised invoice view.
    Renders a dedicated template with @media print CSS.
    """
    facture = get_object_or_404(
        FactureClient.objects.select_related("client", "created_by"),
        pk=pk,
    )
    bls = facture.bls.prefetch_related("lignes__produit_fini").order_by("date_bl")

    from core.models import CompanyInfo
    company = CompanyInfo.get_instance()

    return render(request, "clients/facture_client_print.html", {
        "facture": facture,
        "bls": bls,
        "company": company,
    })


# ===========================================================================
# PaiementClient — List
# ===========================================================================

@login_required(login_url=LOGIN_URL)
def paiement_client_list(request):
    """
    List all client payments.

    Filters:
      ?client=<pk>
      ?mode_paiement=<val>
      ?date_debut, ?date_fin
    """
    from core.utils import date_range_from_params

    qs = (
        PaiementClient.objects
        .select_related("client", "created_by")
        .order_by("-date_paiement", "-created_at")
    )

    client_pk = request.GET.get("client", "")
    if client_pk:
        qs = qs.filter(client_id=client_pk)

    mode = request.GET.get("mode_paiement", "")
    if mode:
        qs = qs.filter(mode_paiement=mode)

    date_debut, date_fin = date_range_from_params(
        request.GET.get("date_debut"), request.GET.get("date_fin")
    )
    if date_debut:
        qs = qs.filter(date_paiement__gte=date_debut)
    if date_fin:
        qs = qs.filter(date_paiement__lte=date_fin)

    page = _paginate(qs, request.GET.get("page"))
    clients = Client.objects.filter(actif=True).order_by("nom")

    total = qs.aggregate(total=Sum("montant"))["total"] or 0

    return render(request, "clients/paiement_client_list.html", {
        "page": page,
        "clients": clients,
        "mode_choices": PaiementClient.MODE_CHOICES,
        "client_filter": client_pk,
        "mode_filter": mode,
        "date_debut": date_debut,
        "date_fin": date_fin,
        "total": total,
        "title": "Paiements Clients",
    })


# ===========================================================================
# PaiementClient — Create
# ===========================================================================

@login_required(login_url=LOGIN_URL)
def paiement_client_create(request, client_pk=None):
    """
    Record a client payment and allocate it to open invoices.

    BR-FAC-03: the user explicitly selects which invoice(s) the payment
    applies to via inline allocation forms (one per open invoice).

    Workflow:
      GET  — render PaiementClientForm + one PaiementClientAllocationForm
             per open invoice for the selected client.
      POST — validate both, create the PaiementClient, then call
             appliquer_paiement_client() with the user's allocation choices.
    """
    client = None
    if client_pk:
        client = get_object_or_404(Client, pk=client_pk)

    if request.method == "POST":
        # Determine the client from POST if not in URL
        if not client:
            from clients.models import Client as ClientModel
            client_id = request.POST.get("client")
            if client_id:
                client = get_object_or_404(ClientModel, pk=client_id)

        form = PaiementClientForm(request.POST, client=client)

        if form.is_valid():
            paiement_client = form.client if hasattr(form, 'client') else \
                form.cleaned_data.get("client")
            alloc_forms = get_allocation_forms(
                paiement_client, data=request.POST
            )

            # Validate all allocation forms
            alloc_valid = all(f.is_valid() for f in alloc_forms)

            if alloc_valid:
                try:
                    with transaction.atomic():
                        paiement = form.save(commit=False)
                        paiement.created_by = request.user
                        paiement.save()

                        # Build allocation list from submitted forms
                        allocations = [
                            {
                                "facture": f.cleaned_data["facture"],
                                "montant_alloue": f.cleaned_data["montant_alloue"],
                            }
                            for f in alloc_forms
                            if f.cleaned_data.get("montant_alloue", 0) > 0
                        ]

                        result = appliquer_paiement_client(paiement, allocations)

                    messages.success(
                        request,
                        f"Paiement de {paiement.montant} DZD enregistré pour "
                        f"« {paiement.client.nom} ». "
                        f"{result['allocations_creees']} facture(s) mise(s) à jour.",
                    )
                    logger.info(
                        "PaiementClient pk=%s created by '%s' "
                        "(client=%s, montant=%s DZD, %d allocations).",
                        paiement.pk, request.user,
                        paiement.client.nom, paiement.montant,
                        result["allocations_creees"],
                    )
                    return redirect(
                        "clients:paiement_client_detail", pk=paiement.pk
                    )

                except ValueError as exc:
                    messages.error(request, f"Erreur d'allocation : {exc}")
                except Exception as exc:
                    logger.exception("Error creating PaiementClient: %s", exc)
                    messages.error(
                        request, f"Erreur lors de l'enregistrement : {exc}"
                    )
            else:
                messages.error(
                    request,
                    "Veuillez corriger les erreurs dans les allocations.",
                )
        else:
            # Rebuild allocation forms with POST data for re-display
            alloc_forms = []
            if client:
                alloc_forms = get_allocation_forms(client, data=request.POST)
            messages.error(request, "Veuillez corriger les erreurs ci-dessous.")

    else:
        form = PaiementClientForm(client=client)
        alloc_forms = get_allocation_forms(client) if client else []

    return render(request, "clients/paiement_client_form.html", {
        "form": form,
        "alloc_forms": alloc_forms,
        "client": client,
        "title": "Enregistrer un paiement client",
        "action_label": "Enregistrer le paiement",
    })


# ===========================================================================
# PaiementClient — Detail
# ===========================================================================

@login_required(login_url=LOGIN_URL)
def paiement_client_detail(request, pk):
    paiement = get_object_or_404(
        PaiementClient.objects.select_related("client", "created_by"),
        pk=pk,
    )
    allocations = paiement.allocations.select_related("facture").order_by(
        "facture__reference"
    )

    return render(request, "clients/paiement_client_detail.html", {
        "paiement": paiement,
        "allocations": allocations,
        "title": f"Paiement — {paiement.client.nom} — {paiement.date_paiement}",
    })


# ===========================================================================
# PaiementClient — Print
# ===========================================================================

@login_required(login_url=LOGIN_URL)
def paiement_client_print(request, pk):
    """
    Print-optimised payment receipt.
    """
    paiement = get_object_or_404(
        PaiementClient.objects.select_related("client", "created_by"),
        pk=pk,
    )
    allocations = paiement.allocations.select_related("facture").order_by(
        "facture__reference"
    )

    from core.models import CompanyInfo
    company = CompanyInfo.get_instance()

    return render(request, "clients/paiement_client_print.html", {
        "paiement": paiement,
        "allocations": allocations,
        "company": company,
    })


# ===========================================================================
# Clients Dashboard
# ===========================================================================

@login_required(login_url=LOGIN_URL)
def clients_dashboard(request):
    """
    Clients module dashboard:
      - Total receivables (créances) and overdue count
      - Clients exceeding their credit ceiling
      - Recent BL Clients (last 10)
      - Uninvoiced Livré BLs (eligible for invoicing)
      - Aging summary across all clients
    """
    import datetime

    today = datetime.date.today()

    # Top-level receivable metrics
    factures_ouvertes_qs = FactureClient.objects.filter(
        statut__in=[
            FactureClient.STATUT_NON_PAYEE,
            FactureClient.STATUT_PARTIELLEMENT_PAYEE,
        ]
    )
    total_creances = (
        factures_ouvertes_qs.aggregate(total=Sum("reste_a_payer"))["total"] or 0
    )
    nb_factures_retard = factures_ouvertes_qs.filter(
        date_echeance__lt=today
    ).count()

    # Clients exceeding credit ceiling
    clients_hors_plafond = [
        c for c in Client.objects.filter(actif=True, plafond_credit__gt=0)
        if c.depasse_plafond
    ]

    # Uninvoiced Livré BLs (eligible for invoicing — alert)
    bls_non_factures = (
        BLClient.objects
        .filter(statut=BLClient.STATUT_LIVRE)
        .select_related("client")
        .order_by("-date_bl")[:20]
    )

    # Recent BLs
    bls_recents = (
        BLClient.objects
        .select_related("client")
        .order_by("-date_bl", "-created_at")[:10]
    )

    # Aged receivables (all clients)
    aging_buckets = get_client_aging_buckets()

    # Recent payments
    paiements_recents = (
        PaiementClient.objects
        .select_related("client")
        .order_by("-date_paiement", "-created_at")[:10]
    )

    nb_clients_actifs = Client.objects.filter(actif=True).count()

    return render(request, "clients/dashboard.html", {
        "total_creances": total_creances,
        "nb_factures_retard": nb_factures_retard,
        "clients_hors_plafond": clients_hors_plafond,
        "bls_non_factures": bls_non_factures,
        "bls_recents": bls_recents,
        "paiements_recents": paiements_recents,
        "aging_buckets": aging_buckets,
        "nb_clients_actifs": nb_clients_actifs,
        "title": "Tableau de bord — Clients",
    })


# ===========================================================================
# AJAX helpers
# ===========================================================================

@login_required(login_url=LOGIN_URL)
def client_solde_json(request, pk):
    """
    Return the financial snapshot for one client as JSON.
    Used by the payment creation page to show live receivable balance.

    Returns:
        {
          "creance_globale": float,
          "nb_factures_ouvertes": int,
          "nb_factures_retard": int,
          "depasse_plafond": bool,
          "plafond_credit": float,
        }
    """
    client = get_object_or_404(Client, pk=pk)
    solde = get_client_solde(client)

    data = {
        "creance_globale": float(solde["creance_globale"]),
        "nb_factures_ouvertes": solde["factures_ouvertes"].count(),
        "nb_factures_retard": solde["nb_factures_retard"],
        "depasse_plafond": solde["depasse_plafond"],
        "plafond_credit": float(client.plafond_credit),
    }
    return JsonResponse(data)
