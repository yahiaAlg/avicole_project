"""
clients/views.py

Function-based views for the full client AR (accounts-receivable) cycle:

  Client         : list, create, detail, edit, toggle active
  BLClient       : list, create, detail, edit, validate (BROUILLON → LIVRE),
                   change statut, delete (BROUILLON only), print
  FactureClient  : list, create, detail, print
  PaiementClient : list, create (with ?facture= pre-population), detail, print
  Dashboard      : clients overview with receivable aging
  AJAX           : client financial snapshot endpoint

Business rules enforced here (complementing model.clean(), forms, and signals):
  BR-BLC-01  Stock decreases only on BROUILLON → LIVRE transition (signal).
  BR-BLC-02  BL cannot be validated if any line qty > available stock.
  BR-BLC-03  Facturé BLs are locked — no edit or delete.
  BR-FAC-01  Invoice HT total auto-computed from BL lines (m2m_changed signal).
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
    AbonnementClientForm,
    VoyageLivraisonForm,
    LivraisonPartielleForm,
    PrixMarcheForm,
)
from clients.models import (
    BLClient,
    BLClientLigne,
    Client,
    FactureClient,
    PaiementClient,
    PaiementClientAllocation,
    AbonnementClient,
    VoyageLivraison,
    LivraisonPartielle,
    PrixMarche,
)
from clients.utils import (
    appliquer_paiement_client,
    appliquer_paiement_client_fifo,
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
            f"BR-BLC-03: وصل التسليم « {bl.reference} » في حالة مفوترة ولا يمكن تعديله.",
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

    return render(
        request,
        "clients/client_list.html",
        {
            "page": page,
            "type_choices": Client.TYPE_CHOICES,
            "actif_param": actif_param,
            "q": q,
            "type_client_filter": type_client,
            "title": "العملاء",
        },
    )


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
                messages.success(request, f"تم إنشاء العميل « {client.nom} » بنجاح.")
                logger.info(
                    "Client pk=%s ('%s') created by '%s'.",
                    client.pk,
                    client.nom,
                    request.user,
                )
                return redirect("clients:client_detail", pk=client.pk)
            except Exception as exc:
                logger.exception("Error creating Client: %s", exc)
                messages.error(request, f"خطأ أثناء الإنشاء: {exc}")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        form = ClientForm()

    return render(
        request,
        "clients/client_form.html",
        {
            "form": form,
            "title": "عميل جديد",
            "action_label": "إنشاء العميل",
        },
    )


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

    bls_recents = BLClient.objects.filter(client=client).order_by(
        "-date_bl", "-created_at"
    )[:10]
    factures_ouvertes = solde["factures_ouvertes"].select_related()[:20]
    paiements_recents = PaiementClient.objects.filter(client=client).order_by(
        "-date_paiement", "-created_at"
    )[:10]

    return render(
        request,
        "clients/client_detail.html",
        {
            "client": client,
            "solde": solde,
            "bls_recents": bls_recents,
            "factures_ouvertes": factures_ouvertes,
            "paiements_recents": paiements_recents,
            "title": f"العميل — {client.nom}",
        },
    )


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
                messages.success(request, f"تم تحديث العميل « {client.nom} ».")
                logger.info("Client pk=%s updated by '%s'.", client.pk, request.user)
                return redirect("clients:client_detail", pk=client.pk)
            except Exception as exc:
                logger.exception("Error updating Client pk=%s: %s", pk, exc)
                messages.error(request, f"خطأ أثناء التحديث: {exc}")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        form = ClientForm(instance=client)

    return render(
        request,
        "clients/client_form.html",
        {
            "form": form,
            "client": client,
            "title": f"تعديل — {client.nom}",
            "action_label": "حفظ التعديلات",
        },
    )


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
    state = "مفعَّل" if client.actif else "معطَّل"
    messages.success(request, f"العميل « {client.nom} » {state}.")
    logger.info(
        "Client pk=%s toggled actif=%s by '%s'.",
        client.pk,
        client.actif,
        request.user,
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

    qs = BLClient.objects.select_related("client", "created_by").order_by(
        "-date_bl", "-created_at"
    )

    statut = request.GET.get("statut", "")
    if statut:
        qs = qs.filter(statut=statut)

    client_pk = request.GET.get("client", "")
    if client_pk:
        qs = qs.filter(client_id=client_pk)

    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(reference__icontains=q) | Q(client__nom__icontains=q))

    date_debut, date_fin = date_range_from_params(
        request.GET.get("date_debut"), request.GET.get("date_fin")
    )
    if date_debut:
        qs = qs.filter(date_bl__gte=date_debut)
    if date_fin:
        qs = qs.filter(date_bl__lte=date_fin)

    page = _paginate(qs, request.GET.get("page"))
    clients = Client.objects.filter(actif=True).order_by("nom")

    return render(
        request,
        "clients/bl_client_list.html",
        {
            "page": page,
            "statut_choices": BLClient.STATUT_CHOICES,
            "clients": clients,
            "statut_filter": statut,
            "client_filter": client_pk,
            "q": q,
            "date_debut": date_debut,
            "date_fin": date_fin,
            "title": "وصولات تسليم العملاء",
        },
    )


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
                    # Honour the chosen statut but guard LIVRE with a stock check.
                    # Previously this was hardcoded to BROUILLON, which silently
                    # ignored the user's LIVRE selection and never deducted stock.
                    wanted_statut = bl.statut  # value from form
                    bl.statut = BLClient.STATUT_BROUILLON
                    bl.save()

                    formset.instance = bl
                    formset.save()  # lines must exist before stock check

                    if wanted_statut == BLClient.STATUT_LIVRE:
                        lignes = bl.lignes.select_related("produit_fini__stock").all()
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
                            raise ValueError(
                                "BR-BLC-02 : stock insuffisant — "
                                + " | ".join(insuffisant)
                            )
                        # Transition triggers the post_save signal → stock deducted.
                        bl.statut = BLClient.STATUT_LIVRE
                        bl.save(update_fields=["statut", "updated_at"])

                success_msg = (
                    f"تم إنشاء وصل تسليم العميل « {bl.reference} » وتسليمه. تم تحديث مخزون المنتجات التامة."
                    if bl.statut == BLClient.STATUT_LIVRE
                    else f"تم إنشاء وصل تسليم العميل « {bl.reference} » (مسودة). يرجى التحقق منه لخصم المخزون."
                )
                messages.success(request, success_msg)
                logger.info(
                    "BLClient pk=%s ('%s') created (%s) by '%s' (client pk=%s).",
                    bl.pk,
                    bl.reference,
                    bl.statut,
                    request.user,
                    bl.client_id,
                )
                return redirect("clients:bl_client_detail", pk=bl.pk)

            except Exception as exc:
                logger.exception("Error creating BLClient: %s", exc)
                messages.error(request, f"خطأ أثناء الإنشاء: {exc}")
        else:
            messages.error(
                request,
                "يرجى تصحيح الأخطاء في رأس النموذج و/أو السطور.",
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

    return render(
        request,
        "clients/bl_client_form.html",
        {
            "form": form,
            "formset": formset,
            "client": client,
            "title": "وصل تسليم جديد",
            "action_label": "حفظ (مسودة)",
        },
    )


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

    # Build statut transition info for the sidebar
    is_admin = request.user.is_superuser or (
        hasattr(request.user, "userprofile")
        and request.user.userprofile.role == "admin"
    )

    # Allowed manual transitions (FACTURE is system-only; BROUILLON→LIVRE uses dedicated view)
    ALLOWED_TARGETS = {
        BLClient.STATUT_BROUILLON: [(BLClient.STATUT_LITIGE, "Signaler en litige")],
        BLClient.STATUT_LIVRE: [(BLClient.STATUT_LITIGE, "Signaler en litige")],
        BLClient.STATUT_LITIGE: [(BLClient.STATUT_BROUILLON, "Remettre en brouillon")],
    }
    statut_transitions = ALLOWED_TARGETS.get(bl.statut, [])

    # For non-admins: single next-step button (first allowed target)
    next_statut_value = statut_transitions[0][0] if statut_transitions else None
    next_statut_label = statut_transitions[0][1] if statut_transitions else None

    return render(
        request,
        "clients/bl_client_detail.html",
        {
            "bl": bl,
            "lignes": lignes,
            "factures": factures,
            "montant_total": bl.montant_total,
            "is_admin": is_admin,
            "statut_transitions": statut_transitions,
            "next_statut_value": next_statut_value,
            "next_statut_label": next_statut_label,
            "title": f"وصل تسليم العميل — {bl.reference}",
        },
    )


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
                    new_statut = form.cleaned_data.get("statut")
                    transitioning_to_livre = (
                        new_statut == BLClient.STATUT_LIVRE
                        and bl.statut != BLClient.STATUT_LIVRE
                    )
                    # Save lines FIRST so the post_save signal (fired by
                    # form.save below) reads the updated quantities, not
                    # the stale pre-edit DB rows.
                    formset.save()
                    if transitioning_to_livre:
                        lignes = bl.lignes.select_related("produit_fini__stock").all()
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
                            raise ValueError(
                                "BR-BLC-02 : stock insuffisant — "
                                + " | ".join(insuffisant)
                            )
                    form.save()  # signal fires here; lines already up-to-date

                messages.success(
                    request, f"تم تحديث وصل تسليم العميل « {bl.reference} »."
                )
                logger.info("BLClient pk=%s updated by '%s'.", bl.pk, request.user)
                return redirect("clients:bl_client_detail", pk=bl.pk)

            except Exception as exc:
                logger.exception("Error updating BLClient pk=%s: %s", pk, exc)
                messages.error(request, f"خطأ أثناء التحديث: {exc}")
        else:
            messages.error(
                request,
                "يرجى تصحيح الأخطاء في رأس النموذج و/أو السطور.",
            )
    else:
        form = BLClientForm(instance=bl, client=bl.client)
        formset = BLClientLigneFormSet(instance=bl)

    return render(
        request,
        "clients/bl_client_form.html",
        {
            "form": form,
            "formset": formset,
            "bl": bl,
            "client": bl.client,
            "title": f"تعديل وصل الاستلام — {bl.reference}",
            "action_label": "حفظ التعديلات",
        },
    )


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
            f"وصل التسليم « {bl.reference} » في حالة « {bl.get_statut_display()} » ولا يمكن إعادة التحقق منه.",
        )
        return redirect("clients:bl_client_detail", pk=bl.pk)

    lignes = bl.lignes.select_related("produit_fini__stock").all()
    if not lignes.exists():
        messages.error(
            request,
            f"تعذّر التحقق من وصل التسليم « {bl.reference} »: لا توجد سطور منتجات مسجّلة.",
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
                "BR-BLC-02: مخزون غير كافٍ للمنتجات التالية — "
                + " | ".join(insuffisant),
            )
            return redirect("clients:bl_client_detail", pk=bl.pk)

        try:
            # The post_save signal handles stock decrease on this save.
            bl.statut = BLClient.STATUT_LIVRE
            bl.save(update_fields=["statut", "updated_at"])

            messages.success(
                request,
                f"تم التحقق من وصل التسليم « {bl.reference} » (مُسلَّم). تم تحديث مخزون المنتجات التامة.",
            )
            logger.info(
                "BLClient pk=%s ('%s') validated to LIVRE by '%s'.",
                bl.pk,
                bl.reference,
                request.user,
            )

        except Exception as exc:
            logger.exception("Error validating BLClient pk=%s: %s", pk, exc)
            messages.error(request, f"خطأ أثناء التحقق: {exc}")

    return redirect("clients:bl_client_detail", pk=bl.pk)


# ===========================================================================
# BLClient — Change statut  POST-only
# ===========================================================================


@login_required(login_url=LOGIN_URL)
@require_POST
def bl_client_change_statut(request, pk):
    """
    Apply a manual statut transition on a BL Client.

    Allowed transitions (FACTURE is system-only; BROUILLON→LIVRE uses the
    dedicated bl_client_valider view which performs the stock check):
      brouillon → litige
      livre     → litige
      litige    → brouillon

    STATUT_FACTURE cannot be set manually — it is controlled by the
    FactureClient creation signal (BR-BLC-03).
    """
    ALLOWED_TARGETS = {
        BLClient.STATUT_BROUILLON: {BLClient.STATUT_LITIGE},
        BLClient.STATUT_LIVRE: {BLClient.STATUT_LITIGE},
        BLClient.STATUT_LITIGE: {BLClient.STATUT_BROUILLON},
    }

    bl = get_object_or_404(BLClient, pk=pk)
    new_statut = request.POST.get("statut", "").strip()

    if bl.statut == BLClient.STATUT_FACTURE:
        messages.error(
            request,
            f"BR-BLC-03: وصل التسليم « {bl.reference} » مقفل (مفوتر) ولا يمكن تعديله يدويًا.",
        )
        return redirect("clients:bl_client_detail", pk=bl.pk)

    allowed = ALLOWED_TARGETS.get(bl.statut, set())
    if new_statut not in allowed:
        messages.error(
            request,
            f"تحويل الحالة غير مسموح به: {bl.get_statut_display()} ← {new_statut}. لتأكيد وصل التسليم، استخدم زر « تأكيد ».",
        )
        return redirect("clients:bl_client_detail", pk=bl.pk)

    try:
        old_label = bl.get_statut_display()
        bl.statut = new_statut
        bl.save(update_fields=["statut", "updated_at"])
        new_label = bl.get_statut_display()
        messages.success(
            request,
            f"وصل التسليم « {bl.reference} »: تم تحديث الحالة ({old_label} ← {new_label}).",
        )
        logger.info(
            "BLClient pk=%s statut changed '%s' → '%s' by '%s'.",
            bl.pk,
            old_label,
            new_statut,
            request.user,
        )
    except Exception as exc:
        logger.exception("Error changing BLClient pk=%s statut: %s", pk, exc)
        messages.error(request, f"Erreur lors du changement de statut : {exc}")

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
            f"يمكن حذف وصولات التسليم في حالة المسودة فقط. وصل التسليم « {bl.reference} » في حالة « {bl.get_statut_display()} ».",
        )
        return redirect("clients:bl_client_detail", pk=bl.pk)

    reference = bl.reference
    client_pk = bl.client_id
    try:
        bl.delete()
        messages.success(request, f"تم حذف وصل تسليم العميل « {reference} ».")
        logger.info("BLClient '%s' deleted by '%s'.", reference, request.user)
    except Exception as exc:
        logger.exception("Error deleting BLClient pk=%s: %s", pk, exc)
        messages.error(request, f"خطأ أثناء الحذف: {exc}")
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
            "لا يمكن طباعة وصل تسليم في حالة المسودة. يرجى التحقق منه أولًا.",
        )
        return redirect("clients:bl_client_detail", pk=bl.pk)

    lignes = bl.lignes.select_related("produit_fini").all()

    from core.models import CompanyInfo

    company = CompanyInfo.get_instance()

    return render(
        request,
        "clients/bl_client_print.html",
        {
            "bl": bl,
            "lignes": lignes,
            "montant_total": bl.montant_total,
            "company": company,
        },
    )


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

    qs = FactureClient.objects.select_related("client", "created_by").order_by(
        "-date_facture", "-created_at"
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

    return render(
        request,
        "clients/facture_client_list.html",
        {
            "page": page,
            "statut_choices": FactureClient.STATUT_CHOICES,
            "clients": clients,
            "statut_filter": statut,
            "client_filter": client_pk,
            "date_debut": date_debut,
            "date_fin": date_fin,
            "totals": totals,
            "title": "فواتير العملاء",
        },
    )


# ===========================================================================
# FactureClient — Create
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def facture_client_create(request, client_pk=None):
    """
    Create a client invoice by selecting Livré BL Clients.

    BR-FAC-01: montant_ht/tva/ttc are computed from BL lines in the
               m2m_changed signal (fires after save_m2m() links the BLs).
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
                    # the m2m_changed signal recalculates and persists them
                    # after form.save_m2m() links the BLs.
                    facture.save()
                    # M2M must be saved after the instance has a PK —
                    # this triggers the m2m_changed signal.
                    form.save_m2m()

                # Refresh from DB to get signal-computed totals for the message.
                facture.refresh_from_db()

                messages.success(
                    request,
                    f"تم إنشاء فاتورة العميل « {facture.reference} ». إجمالي المبلغ شامل الضريبة: {facture.montant_ttc} دج.",
                )
                logger.info(
                    "FactureClient pk=%s ('%s') created by '%s' (client pk=%s).",
                    facture.pk,
                    facture.reference,
                    request.user,
                    facture.client_id,
                )
                return redirect("clients:facture_client_detail", pk=facture.pk)

            except Exception as exc:
                logger.exception("Error creating FactureClient: %s", exc)
                messages.error(request, f"خطأ أثناء الإنشاء: {exc}")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        initial = {}
        if not client_pk:
            initial["reference"] = generer_reference_facture_client()
        form = FactureClientForm(client=client, initial=initial)

    return render(
        request,
        "clients/facture_client_form.html",
        {
            "form": form,
            "client": client,
            "title": "فاتورة عميل جديدة",
            "action_label": "إنشاء الفاتورة",
        },
    )


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
    allocations = facture.allocations.select_related("paiement").order_by(
        "-paiement__date_paiement"
    )

    return render(
        request,
        "clients/facture_client_detail.html",
        {
            "facture": facture,
            "bls": bls,
            "allocations": allocations,
            "title": f"فاتورة — {facture.reference}",
        },
    )


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

    return render(
        request,
        "clients/facture_client_print.html",
        {
            "facture": facture,
            "bls": bls,
            "company": company,
        },
    )


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

    qs = PaiementClient.objects.select_related("client", "created_by").order_by(
        "-date_paiement", "-created_at"
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

    return render(
        request,
        "clients/paiement_client_list.html",
        {
            "page": page,
            "clients": clients,
            "mode_choices": PaiementClient.MODE_CHOICES,
            "client_filter": client_pk,
            "mode_filter": mode,
            "date_debut": date_debut,
            "date_fin": date_fin,
            "total": total,
            "title": "مدفوعات العملاء",
        },
    )


# ===========================================================================
# PaiementClient — Create
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def paiement_client_create(request, client_pk=None):
    """
    Record a client payment with automatic FIFO allocation.

    The FIFO engine (clients.utils.appliquer_paiement_client_fifo) runs
    immediately after the record is saved, applying the payment to the
    oldest open invoices first.

    Optional GET params:
      ?facture=<pk> — pre-fill montant with that invoice's reste_a_payer
    """
    client = None
    if client_pk:
        client = get_object_or_404(Client, pk=client_pk)

    # ── Resolve facture pre-population from ?facture=<pk> ────────────────
    facture_obj = None
    facture_reste = None
    facture_pk_param = request.GET.get("facture") or request.POST.get("_facture_pk")
    if facture_pk_param and client:
        try:
            fo = FactureClient.objects.get(
                pk=facture_pk_param,
                client=client,
                statut__in=[
                    FactureClient.STATUT_NON_PAYEE,
                    FactureClient.STATUT_PARTIELLEMENT_PAYEE,
                ],
            )
            facture_obj = fo
            facture_reste = fo.reste_a_payer
        except FactureClient.DoesNotExist:
            pass

    if request.method == "POST":
        if not client:
            client_id = request.POST.get("client")
            if client_id:
                client = get_object_or_404(Client, pk=client_id)

        form = PaiementClientForm(request.POST, client=client)

        if form.is_valid():
            paiement_client = (
                form.client
                if hasattr(form, "client")
                else form.cleaned_data.get("client")
            )
            alloc_forms = get_allocation_forms(paiement_client, data=request.POST)

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

                        if allocations:
                            # BR-FAC-03: user made explicit choices — honour them.
                            result = appliquer_paiement_client(paiement, allocations)
                            mode_label = f"{result['allocations_creees']} facture(s) mise(s) à jour."
                        else:
                            # No manual allocations supplied — fall back to FIFO.
                            result = appliquer_paiement_client_fifo(paiement)
                            mode_label = (
                                f"Allocations FIFO appliquées "
                                f"({result['allocations_creees']} facture(s))."
                            )

                    messages.success(
                        request,
                        f"تم تسجيل دفعة بقيمة {paiement.montant} دج لـ « {paiement.client.nom} ». {mode_label}",
                    )
                    logger.info(
                        "PaiementClient pk=%s created by '%s' "
                        "(client=%s, montant=%s DZD, %d allocations).",
                        paiement.pk,
                        request.user,
                        paiement.client.nom,
                        paiement.montant,
                        result["allocations_creees"],
                    )
                    return redirect("clients:paiement_client_detail", pk=paiement.pk)

                except ValueError as exc:
                    messages.error(request, f"Erreur d'allocation : {exc}")
                except Exception as exc:
                    logger.exception("Error creating PaiementClient: %s", exc)
                    messages.error(request, f"خطأ أثناء التسجيل: {exc}")
            else:
                messages.error(
                    request,
                    "يرجى تصحيح الأخطاء في التوزيعات.",
                )
        else:
            # Rebuild allocation forms with POST data for re-display
            alloc_forms = []
            if client:
                alloc_forms = get_allocation_forms(client, data=request.POST)
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")

    else:
        form = PaiementClientForm(client=client)
        alloc_forms = get_allocation_forms(client) if client else []

    solde = get_client_solde(client) if client else None

    return render(
        request,
        "clients/paiement_client_form.html",
        {
            "form": form,
            "alloc_forms": alloc_forms,
            "client": client,
            "facture_obj": facture_obj,
            "facture_reste": facture_reste,
            "solde": solde,
            "title": "تسجيل دفعة عميل",
            "action_label": "حفظ الدفعة",
        },
    )


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

    return render(
        request,
        "clients/paiement_client_detail.html",
        {
            "paiement": paiement,
            "allocations": allocations,
            "title": f"دفعة — {paiement.client.nom} — {paiement.date_paiement}",
        },
    )


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

    return render(
        request,
        "clients/paiement_client_print.html",
        {
            "paiement": paiement,
            "allocations": allocations,
            "company": company,
        },
    )


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
    nb_factures_retard = factures_ouvertes_qs.filter(date_echeance__lt=today).count()

    # Clients exceeding credit ceiling
    clients_hors_plafond = [
        c
        for c in Client.objects.filter(actif=True, plafond_credit__gt=0)
        if c.depasse_plafond
    ]

    # Uninvoiced Livré BLs (eligible for invoicing — alert)
    bls_non_factures = (
        BLClient.objects.filter(statut=BLClient.STATUT_LIVRE)
        .select_related("client")
        .order_by("-date_bl")[:20]
    )

    # Recent BLs
    bls_recents = BLClient.objects.select_related("client").order_by(
        "-date_bl", "-created_at"
    )[:10]

    # Aged receivables (all clients)
    aging_buckets = get_client_aging_buckets()

    # Recent payments
    paiements_recents = PaiementClient.objects.select_related("client").order_by(
        "-date_paiement", "-created_at"
    )[:10]

    nb_clients_actifs = Client.objects.filter(actif=True).count()

    return render(
        request,
        "clients/dashboard.html",
        {
            "total_creances": total_creances,
            "nb_factures_retard": nb_factures_retard,
            "clients_hors_plafond": clients_hors_plafond,
            "bls_non_factures": bls_non_factures,
            "bls_recents": bls_recents,
            "paiements_recents": paiements_recents,
            "aging_buckets": aging_buckets,
            "nb_clients_actifs": nb_clients_actifs,
            "title": "لوحة تحكم — العملاء",
        },
    )


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


# ===========================================================================
# AbonnementClient — List / Create / Detail / Edit / Toggle
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def abonnement_list(request):
    """
    Subscription list.

    Filters: ?client=<pk>, ?statut=actif|termine|suspendu
    """
    qs = AbonnementClient.objects.select_related("client", "produit_fini").order_by(
        "-date_debut"
    )

    client_pk = request.GET.get("client", "")
    if client_pk:
        qs = qs.filter(client_id=client_pk)

    statut = request.GET.get("statut", "")
    if statut:
        qs = qs.filter(statut=statut)

    page = _paginate(qs, request.GET.get("page"))
    clients = Client.objects.filter(actif=True).order_by("nom")

    return render(
        request,
        "clients/abonnement_list.html",
        {
            "page": page,
            "clients": clients,
            "client_pk": client_pk,
            "statut": statut,
            "statut_choices": AbonnementClient.STATUT_CHOICES,
            "title": "اشتراكات العملاء",
        },
    )


@login_required(login_url=LOGIN_URL)
def abonnement_create(request, client_pk=None):
    """Create a new client subscription."""
    client = None
    if client_pk:
        client = get_object_or_404(Client, pk=client_pk)

    if request.method == "POST":
        form = AbonnementClientForm(request.POST)
        if form.is_valid():
            try:
                abo = form.save(commit=False)
                if client:
                    abo.client = client
                abo.save()
                messages.success(
                    request,
                    f"تم إنشاء الاشتراك لـ {abo.client.nom} ({abo.produit_fini.designation}).",
                )
                logger.info(
                    "AbonnementClient pk=%s created by '%s'.", abo.pk, request.user
                )
                return redirect("clients:abonnement_detail", pk=abo.pk)
            except Exception as exc:
                logger.exception("Error creating AbonnementClient: %s", exc)
                messages.error(request, f"خطأ أثناء الإنشاء: {exc}")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        initial = {}
        if client:
            initial["client"] = client
        form = AbonnementClientForm(initial=initial)

    return render(
        request,
        "clients/abonnement_form.html",
        {
            "form": form,
            "client": client,
            "title": "اشتراك جديد",
            "action_label": "إنشاء",
        },
    )


@login_required(login_url=LOGIN_URL)
def abonnement_detail(request, pk):
    """Detail view for one subscription, with its partial deliveries."""
    abo = get_object_or_404(
        AbonnementClient.objects.select_related("client", "produit_fini"),
        pk=pk,
    )
    livraisons = abo.livraisons.select_related("voyage").order_by("-date")

    return render(
        request,
        "clients/abonnement_detail.html",
        {
            "abo": abo,
            "livraisons": livraisons,
            "title": f"الاشتراك — {abo.client.nom}",
        },
    )


@login_required(login_url=LOGIN_URL)
def abonnement_edit(request, pk):
    """Edit a subscription (status, dates, quantity)."""
    abo = get_object_or_404(AbonnementClient, pk=pk)

    if request.method == "POST":
        form = AbonnementClientForm(request.POST, instance=abo)
        if form.is_valid():
            try:
                form.save()
                messages.success(request, "تم تحديث الاشتراك.")
                return redirect("clients:abonnement_detail", pk=abo.pk)
            except Exception as exc:
                logger.exception("Error updating AbonnementClient pk=%s: %s", pk, exc)
                messages.error(request, f"خطأ أثناء التحديث: {exc}")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        form = AbonnementClientForm(instance=abo)

    return render(
        request,
        "clients/abonnement_form.html",
        {
            "form": form,
            "abo": abo,
            "title": f"تعديل الاشتراك — {abo.client.nom}",
            "action_label": "حفظ التعديلات",
        },
    )


@login_required(login_url=LOGIN_URL)
@require_POST
def abonnement_toggle_statut(request, pk):
    """Cycle subscription statut: ACTIF → SUSPENDU → TERMINE (POST-only)."""
    abo = get_object_or_404(AbonnementClient, pk=pk)
    transitions = {
        AbonnementClient.STATUT_ACTIF: AbonnementClient.STATUT_SUSPENDU,
        AbonnementClient.STATUT_SUSPENDU: AbonnementClient.STATUT_ACTIF,
        AbonnementClient.STATUT_TERMINE: AbonnementClient.STATUT_TERMINE,
    }
    nouveau = transitions.get(abo.statut, abo.statut)
    abo.statut = nouveau
    abo.save(update_fields=["statut"])
    messages.success(request, f"الاشتراك: {abo.get_statut_display()}.")
    return redirect("clients:abonnement_detail", pk=pk)


# ===========================================================================
# VoyageLivraison — List / Create / Detail / Edit
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def voyage_list(request):
    """Truck-trip list, most recent first."""
    qs = VoyageLivraison.objects.order_by("-date_voyage")

    date_debut = request.GET.get("date_debut", "")
    date_fin = request.GET.get("date_fin", "")
    if date_debut:
        qs = qs.filter(date_voyage__gte=date_debut)
    if date_fin:
        qs = qs.filter(date_voyage__lte=date_fin)

    page = _paginate(qs, request.GET.get("page"))
    return render(
        request,
        "clients/voyage_list.html",
        {
            "page": page,
            "date_debut": date_debut,
            "date_fin": date_fin,
            "title": "رحلات التوصيل",
        },
    )


@login_required(login_url=LOGIN_URL)
def voyage_create(request):
    """Create a new truck-trip record."""
    if request.method == "POST":
        form = VoyageLivraisonForm(request.POST)
        if form.is_valid():
            try:
                voyage = form.save()
                messages.success(
                    request,
                    f"تم إنشاء رحلة التوصيل بتاريخ {voyage.date_voyage}.",
                )
                logger.info(
                    "VoyageLivraison pk=%s created by '%s'.", voyage.pk, request.user
                )
                return redirect("clients:voyage_detail", pk=voyage.pk)
            except Exception as exc:
                logger.exception("Error creating VoyageLivraison: %s", exc)
                messages.error(request, f"خطأ أثناء الإنشاء: {exc}")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        import datetime

        form = VoyageLivraisonForm(initial={"date_voyage": datetime.date.today()})

    return render(
        request,
        "clients/voyage_form.html",
        {"form": form, "title": "رحلة توصيل جديدة", "action_label": "إنشاء"},
    )


@login_required(login_url=LOGIN_URL)
def voyage_detail(request, pk):
    """Detail: truck trip + all its partial deliveries."""
    voyage = get_object_or_404(VoyageLivraison, pk=pk)
    livraisons = voyage.livraisons.select_related(
        "abonnement__client", "abonnement__produit_fini"
    ).order_by("abonnement__client__nom")

    return render(
        request,
        "clients/voyage_detail.html",
        {
            "voyage": voyage,
            "livraisons": livraisons,
            "title": f"رحلة — {voyage.date_voyage}",
        },
    )


@login_required(login_url=LOGIN_URL)
def voyage_edit(request, pk):
    voyage = get_object_or_404(VoyageLivraison, pk=pk)

    if request.method == "POST":
        form = VoyageLivraisonForm(request.POST, instance=voyage)
        if form.is_valid():
            try:
                form.save()
                messages.success(request, "تم تحديث رحلة التوصيل.")
                return redirect("clients:voyage_detail", pk=pk)
            except Exception as exc:
                logger.exception("Error updating VoyageLivraison pk=%s: %s", pk, exc)
                messages.error(request, f"خطأ أثناء التحديث: {exc}")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        form = VoyageLivraisonForm(instance=voyage)

    return render(
        request,
        "clients/voyage_form.html",
        {
            "form": form,
            "voyage": voyage,
            "title": f"تعديل — رحلة {voyage.date_voyage}",
            "action_label": "حفظ التعديلات",
        },
    )


# ===========================================================================
# LivraisonPartielle — Create / Delete  (sub-resource of AbonnementClient)
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def livraison_partielle_create(request, abonnement_pk):
    """
    Record one partial delivery against a subscription.

    On save the post_save signal decrements StockProduitFini and creates a
    StockMouvement (SORTIE / LIVRAISON_ABONNEMENT).
    """
    abo = get_object_or_404(AbonnementClient, pk=abonnement_pk)

    if abo.statut != AbonnementClient.STATUT_ACTIF:
        messages.error(
            request,
            "لا يمكن تسجيل تسليم على اشتراك غير نشط.",
        )
        return redirect("clients:abonnement_detail", pk=abo.pk)

    if request.method == "POST":
        form = LivraisonPartielleForm(request.POST, abonnement=abo)
        if form.is_valid():
            try:
                with transaction.atomic():
                    livraison = form.save(commit=False)
                    livraison.abonnement = abo
                    livraison.save()  # signal → stock sortie

                messages.success(
                    request,
                    f"تم تسجيل تسليم {livraison.quantite_livree} {abo.produit_fini.unite_mesure} بتاريخ {livraison.date}.",
                )
                logger.info(
                    "LivraisonPartielle pk=%s created (abo pk=%s) by '%s'.",
                    livraison.pk,
                    abo.pk,
                    request.user,
                )
                return redirect("clients:abonnement_detail", pk=abo.pk)
            except Exception as exc:
                logger.exception(
                    "Error creating LivraisonPartielle for abo pk=%s: %s", abo.pk, exc
                )
                messages.error(request, f"خطأ أثناء التسجيل: {exc}")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        import datetime

        form = LivraisonPartielleForm(
            abonnement=abo, initial={"date": datetime.date.today()}
        )

    return render(
        request,
        "clients/livraison_partielle_form.html",
        {
            "form": form,
            "abo": abo,
            "title": f"تسليم جزئي — {abo.client.nom}",
            "action_label": "حفظ",
        },
    )


@login_required(login_url=LOGIN_URL)
@require_POST
def livraison_partielle_delete(request, pk):
    """
    Delete a partial delivery (POST-only).

    The pre_delete signal restores the StockProduitFini balance.
    """
    livraison = get_object_or_404(
        LivraisonPartielle.objects.select_related("abonnement"), pk=pk
    )
    abo = livraison.abonnement

    try:
        date_ref = livraison.date
        qte_ref = livraison.quantite_livree
        livraison.delete()  # signal → stock restored
        messages.success(
            request,
            f"تم حذف التسليم بتاريخ {date_ref} ({qte_ref}). تم تصحيح المخزون.",
        )
        logger.info("LivraisonPartielle pk=%s deleted by '%s'.", pk, request.user)
    except Exception as exc:
        logger.exception("Error deleting LivraisonPartielle pk=%s: %s", pk, exc)
        messages.error(request, f"خطأ أثناء الحذف: {exc}")

    return redirect("clients:abonnement_detail", pk=abo.pk)


# ===========================================================================
# Fiche des dettes client  — purchase history vs. market price
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def fiche_dettes_client(request, pk):
    """
    Debt sheet for a specific client: all egg BL lines in a date range,
    with the prevailing market price on each delivery date, the unit margin,
    and a running cumulative balance.

    Query params:
      ?date_debut=YYYY-MM-DD
      ?date_fin=YYYY-MM-DD
      ?produit_fini=<pk>   — filter to one product
    """
    import datetime as dt
    from decimal import Decimal

    client = get_object_or_404(Client, pk=pk)

    # ── filters ──────────────────────────────────────────────────────────
    date_debut_str = request.GET.get("date_debut", "")
    date_fin_str = request.GET.get("date_fin", "")
    produit_fini_pk = request.GET.get("produit_fini", "")

    date_debut = None
    date_fin = None
    try:
        if date_debut_str:
            date_debut = dt.date.fromisoformat(date_debut_str)
        if date_fin_str:
            date_fin = dt.date.fromisoformat(date_fin_str)
    except ValueError:
        pass

    # ── BL lines for this client ─────────────────────────────────────────
    # Only include validated (Livré / Facturé) BLs — exclude drafts and disputed.
    qs = (
        BLClientLigne.objects.filter(
            bl__client=client,
            bl__statut__in=[BLClient.STATUT_LIVRE, BLClient.STATUT_FACTURE],
        )
        .select_related("bl", "produit_fini")
        .order_by("bl__date_bl", "bl__pk", "pk")
    )

    if date_debut:
        qs = qs.filter(bl__date_bl__gte=date_debut)
    if date_fin:
        qs = qs.filter(bl__date_bl__lte=date_fin)
    if produit_fini_pk:
        qs = qs.filter(produit_fini__pk=produit_fini_pk)

    # ── Enrich each line with market price & margin ───────────────────────
    lignes_enrichies = []
    solde_cumul = Decimal("0")

    for ligne in qs:
        prix_marche = PrixMarche.get_price_on(ligne.produit_fini, ligne.bl.date_bl)
        montant = ligne.montant_total
        solde_cumul += montant

        marge_unitaire = None
        marge_pct = None
        if prix_marche is not None:
            marge_unitaire = ligne.prix_unitaire - prix_marche
            if prix_marche > 0:
                marge_pct = (marge_unitaire / prix_marche) * 100

        lignes_enrichies.append(
            {
                "ligne": ligne,
                "bl": ligne.bl,
                "date": ligne.bl.date_bl,
                "reference": ligne.bl.reference,
                "produit": ligne.produit_fini,
                "quantite": ligne.quantite,
                "prix_unitaire": ligne.prix_unitaire,
                "montant": montant,
                "prix_marche": prix_marche,
                "marge_unitaire": marge_unitaire,
                "marge_pct": marge_pct,
                "solde_cumul": solde_cumul,
            }
        )

    # ── Totals ────────────────────────────────────────────────────────────
    total_montant = sum(l["montant"] for l in lignes_enrichies)

    # distinct product list for the filter dropdown
    from production.models import ProduitFini

    produits_disponibles = (
        ProduitFini.objects.filter(
            lignes_bl_client__bl__client=client,
            type_produit=ProduitFini.TYPE_OEUFS,
        )
        .distinct()
        .order_by("designation")
        if hasattr(ProduitFini, "TYPE_OEUFS")
        else ProduitFini.objects.filter(lignes_bl_client__bl__client=client)
        .distinct()
        .order_by("designation")
    )

    return render(
        request,
        "clients/fiche_dettes_client.html",
        {
            "client": client,
            "lignes": lignes_enrichies,
            "total_montant": total_montant,
            "date_debut": date_debut,
            "date_fin": date_fin,
            "produit_fini_pk": produit_fini_pk,
            "produits_disponibles": produits_disponibles,
            "title": f"فيشة الديون — {client.nom}",
        },
    )


# ===========================================================================
# PrixMarche — Market price list / create / edit / delete
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def prix_marche_list(request):
    """List market prices with optional filters."""
    from production.models import ProduitFini

    qs = PrixMarche.objects.select_related("produit_fini").order_by(
        "-date", "produit_fini__designation"
    )

    produit_pk = request.GET.get("produit", "")
    if produit_pk:
        qs = qs.filter(produit_fini__pk=produit_pk)

    date_debut_str = request.GET.get("date_debut", "")
    date_fin_str = request.GET.get("date_fin", "")
    if date_debut_str:
        qs = qs.filter(date__gte=date_debut_str)
    if date_fin_str:
        qs = qs.filter(date__lte=date_fin_str)

    # Product list for filter — egg products only where possible
    try:
        produits = ProduitFini.objects.filter(
            type_produit=ProduitFini.TYPE_OEUFS
        ).order_by("designation")
    except Exception:
        produits = ProduitFini.objects.all().order_by("designation")

    page = _paginate(qs, request.GET.get("page"))

    return render(
        request,
        "clients/prix_marche_list.html",
        {
            "page": page,
            "produits": produits,
            "produit_pk": produit_pk,
            "date_debut": date_debut_str,
            "date_fin": date_fin_str,
            "title": "أسعار السوق",
        },
    )


@login_required(login_url=LOGIN_URL)
def prix_marche_create(request):
    """Create a new market-price record."""
    import datetime as dt

    if request.method == "POST":
        form = PrixMarcheForm(request.POST)
        if form.is_valid():
            try:
                prix = form.save(commit=False)
                prix.created_by = request.user
                prix.save()
                messages.success(
                    request,
                    f"تم تسجيل سعر السوق: {prix.produit_fini.designation} — {prix.date} : {prix.prix_marche} د.ج",
                )
                logger.info("PrixMarche pk=%s created by '%s'.", prix.pk, request.user)
                return redirect("clients:prix_marche_list")
            except Exception as exc:
                logger.exception("Error creating PrixMarche: %s", exc)
                messages.error(request, f"خطأ أثناء الحفظ: {exc}")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        form = PrixMarcheForm(initial={"date": dt.date.today()})

    return render(
        request,
        "clients/prix_marche_form.html",
        {"form": form, "title": "سعر سوق جديد", "action_label": "حفظ"},
    )


@login_required(login_url=LOGIN_URL)
def prix_marche_edit(request, pk):
    """Edit an existing market-price record."""
    prix = get_object_or_404(PrixMarche, pk=pk)

    if request.method == "POST":
        form = PrixMarcheForm(request.POST, instance=prix)
        if form.is_valid():
            try:
                form.save()
                messages.success(request, "تم تحديث سعر السوق.")
                logger.info("PrixMarche pk=%s updated by '%s'.", prix.pk, request.user)
                return redirect("clients:prix_marche_list")
            except Exception as exc:
                logger.exception("Error updating PrixMarche pk=%s: %s", prix.pk, exc)
                messages.error(request, f"خطأ أثناء التحديث: {exc}")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        form = PrixMarcheForm(instance=prix)

    return render(
        request,
        "clients/prix_marche_form.html",
        {
            "form": form,
            "prix": prix,
            "title": f"تعديل سعر — {prix.produit_fini.designation} ({prix.date})",
            "action_label": "حفظ التعديلات",
        },
    )


@login_required(login_url=LOGIN_URL)
@require_POST
def prix_marche_delete(request, pk):
    """Delete a market-price record (POST-only)."""
    prix = get_object_or_404(PrixMarche, pk=pk)
    ref = str(prix)
    try:
        prix.delete()
        messages.success(request, f"تم حذف سعر السوق: {ref}")
        logger.info("PrixMarche pk=%s deleted by '%s'.", pk, request.user)
    except Exception as exc:
        logger.exception("Error deleting PrixMarche pk=%s: %s", pk, exc)
        messages.error(request, f"خطأ أثناء الحذف: {exc}")
    return redirect("clients:prix_marche_list")
