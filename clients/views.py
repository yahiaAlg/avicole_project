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

v1.4 (§3.5, BR-BRA-01/02/04/07): Client stays global (BR-BRA-06), but
BLClient, FactureClient, PaiementClient, and AbonnementClient all carry a
required `branche` FK. Vue par Branche scopes every list/detail to the
request's active branche; Vue Globale shows every branche combined.
Creation views require a concrete active branche (@require_branche_context)
and lock the form's branche field to it. VoyageLivraison and PrixMarche stay
intentionally global (§3.5.3); LivraisonPartielle has no stored branche of
its own — it inherits its parent abonnement's.
"""

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db import transaction
from django.db.models import Q, Sum
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from clients.forms import (
    BLClientForm,
    BLClientLigneFormSet,
    BLClientPieceJointeFormSet,
    ClientForm,
    FactureClientForm,
    FactureClientPieceJointeFormSet,
    PaiementClientForm,
    PaiementClientPieceJointeFormSet,
    AcompteClientPieceJointeFormSet,
    get_allocation_forms,
    AbonnementClientForm,
    GenererEcheanceAbonnementForm,
    VoyageLivraisonForm,
    LivraisonPartielleForm,
    PrixMarcheForm,
)
from clients.models import (
    TypeClient,
    BLClient,
    BLClientLigne,
    Client,
    FactureClient,
    PaiementClient,
    PaiementClientAllocation,
    AcompteClient,
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
    generer_facture_abonnement,
    generer_echeances_abonnements_forfait,
    get_client_aging_buckets,
    get_client_solde,
)
from core.views import (
    branche_object_or_404,
    branche_matches,
    build_piece_jointe_formset,
    get_active_branche,
    require_branche_context,
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
        qs = qs.filter(type_client__code=type_client)

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
            "type_choices": TypeClient.objects.filter(actif=True).order_by(
                "ordre", "libelle"
            ),
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

    v1.4 (§3.5.3 ¶4): Client stays global, but BLClient/FactureClient/
    PaiementClient are branch-scoped. Vue par Branche shows this branch's
    figures only (mirrors intrants.fournisseur_detail); Vue Globale sums
    across every branch this client has ever been served by.
    """
    client = get_object_or_404(Client, pk=pk)
    branche = get_active_branche(request)
    solde = get_client_solde(client, branche=branche)

    bls_recents_qs = BLClient.objects.filter(client=client)
    paiements_recents_qs = PaiementClient.objects.filter(client=client)
    if branche is not None:
        bls_recents_qs = bls_recents_qs.filter(branche=branche)
        paiements_recents_qs = paiements_recents_qs.filter(branche=branche)

    bls_recents = bls_recents_qs.order_by("-date_bl", "-created_at")[:10]
    factures_ouvertes = solde["factures_ouvertes"].select_related()[:20]
    paiements_recents = paiements_recents_qs.order_by("-date_paiement", "-created_at")[
        :10
    ]

    abonnements_qs = AbonnementClient.objects.filter(client=client)
    if branche is not None:
        abonnements_qs = abonnements_qs.filter(branche=branche)
    abonnements = abonnements_qs.select_related("produit_fini").order_by(
        "-statut", "-date_debut"
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
            "abonnements": abonnements,
            "active_branche": branche,
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

    branche = get_active_branche(request)
    qs = BLClient.objects.select_related("client", "branche", "created_by").order_by(
        "-date_bl", "-created_at"
    )
    if branche is not None:
        qs = qs.filter(branche=branche)

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
            "active_branche": branche,
            "title": "وصولات تسليم العملاء",
        },
    )


# ===========================================================================
# BLClient — Create
# ===========================================================================


@login_required(login_url=LOGIN_URL)
@require_branche_context
def bl_client_create(request, client_pk=None):
    """
    Create a new BL Client (BROUILLON) with its product lines.

    Accepts an optional `client_pk` URL parameter to pre-select the client.
    The inline formset manages BLClientLigne records.
    Auto-generates the BL reference if the form's reference field is empty.

    BR-BRA-01/04: the BL comes out of the request's active branche's
    StockProduitFini — locked on the form; Vue Globale cannot reach this
    view (@require_branche_context).
    """
    branche = get_active_branche(request)
    client = None
    if client_pk:
        client = get_object_or_404(Client, pk=client_pk)

    if request.method == "POST":
        form = BLClientForm(request.POST, request.FILES, client=client, branche=branche)
        formset = BLClientLigneFormSet(request.POST, form_kwargs={"branche": branche})
        pj_formset = build_piece_jointe_formset(
            BLClientPieceJointeFormSet, request, prefix="pj"
        )

        if form.is_valid() and formset.is_valid() and pj_formset.is_valid():
            try:
                with transaction.atomic():
                    bl = form.save(commit=False)
                    bl.created_by = request.user
                    # Auto-generate reference if blank
                    if not bl.reference:
                        bl.reference = generer_reference_bl_client(branche)
                    # Honour the chosen statut but guard LIVRE with a stock check.
                    # Previously this was hardcoded to BROUILLON, which silently
                    # ignored the user's LIVRE selection and never deducted stock.
                    wanted_statut = bl.statut  # value from form
                    bl.statut = BLClient.STATUT_BROUILLON
                    bl.save()

                    formset.instance = bl
                    formset.save()  # lines must exist before stock check
                    pj_formset.instance = bl
                    pj_formset.save()

                    if wanted_statut == BLClient.STATUT_LIVRE:
                        lignes = bl.lignes.select_related("produit_fini").all()
                        insuffisant = []
                        for ligne in lignes:
                            dispo = ligne.produit_fini.quantite_en_stock_branche(
                                bl.branche
                            )
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
            initial["reference"] = generer_reference_bl_client(branche)
        form = BLClientForm(client=client, branche=branche, initial=initial)
        tmp_bl = BLClient()
        if client:
            tmp_bl.client = client
        formset = BLClientLigneFormSet(
            instance=tmp_bl, form_kwargs={"branche": branche}
        )
        pj_formset = build_piece_jointe_formset(
            BLClientPieceJointeFormSet, request, prefix="pj"
        )

    return render(
        request,
        "clients/bl_client_form.html",
        {
            "form": form,
            "formset": formset,
            "pj_formset": pj_formset,
            "client": client,
            "active_branche": branche,
            "title": "وصل تسليم جديد",
            "action_label": "حفظ (مسودة)",
        },
    )


# ===========================================================================
# BLClient — Detail
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def bl_client_detail(request, pk):
    bl = branche_object_or_404(
        request,
        BLClient.objects.select_related("client", "branche", "created_by"),
        pk=pk,
    )
    lignes = bl.lignes.select_related("produit_fini").all()
    factures = bl.factures.order_by("-date_facture")
    pieces_jointes = bl.pieces_jointes.select_related("uploaded_by").order_by(
        "-created_at"
    )

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
            "pieces_jointes": pieces_jointes,
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
    BR-BRA-02: the BL must belong to the request's active branche.
    """
    bl = branche_object_or_404(
        request, BLClient.objects.select_related("client", "branche"), pk=pk
    )

    if not _assert_bl_editable(bl, request):
        return redirect("clients:bl_client_detail", pk=bl.pk)

    if request.method == "POST":
        form = BLClientForm(
            request.POST,
            request.FILES,
            instance=bl,
            client=bl.client,
            branche=bl.branche,
        )
        formset = BLClientLigneFormSet(
            request.POST, instance=bl, form_kwargs={"branche": bl.branche}
        )
        pj_formset = build_piece_jointe_formset(
            BLClientPieceJointeFormSet, request, instance=bl, prefix="pj"
        )

        if form.is_valid() and formset.is_valid() and pj_formset.is_valid():
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
                    pj_formset.save()
                    if transitioning_to_livre:
                        lignes = bl.lignes.select_related("produit_fini").all()
                        insuffisant = []
                        for ligne in lignes:
                            dispo = ligne.produit_fini.quantite_en_stock_branche(
                                bl.branche
                            )
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
        form = BLClientForm(instance=bl, client=bl.client, branche=bl.branche)
        formset = BLClientLigneFormSet(instance=bl, form_kwargs={"branche": bl.branche})
        pj_formset = build_piece_jointe_formset(
            BLClientPieceJointeFormSet, request, instance=bl, prefix="pj"
        )

    return render(
        request,
        "clients/bl_client_form.html",
        {
            "form": form,
            "formset": formset,
            "pj_formset": pj_formset,
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
    BR-BRA-02: the BL must belong to the request's active branche.
    """
    bl = branche_object_or_404(
        request, BLClient.objects.select_related("client", "branche"), pk=pk
    )

    if bl.statut != BLClient.STATUT_BROUILLON:
        messages.warning(
            request,
            f"وصل التسليم « {bl.reference} » في حالة « {bl.get_statut_display()} » ولا يمكن إعادة التحقق منه.",
        )
        return redirect("clients:bl_client_detail", pk=bl.pk)

    lignes = bl.lignes.select_related("produit_fini").all()
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
            dispo = ligne.produit_fini.quantite_en_stock_branche(bl.branche)
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

    bl = branche_object_or_404(request, BLClient, pk=pk)
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
    bl = branche_object_or_404(request, BLClient, pk=pk)

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
    bl = branche_object_or_404(
        request,
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

    branche = get_active_branche(request)
    qs = FactureClient.objects.select_related(
        "client", "branche", "created_by"
    ).order_by("-date_facture", "-created_at")
    if branche is not None:
        qs = qs.filter(branche=branche)

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
            "active_branche": branche,
            "title": "فواتير العملاء",
        },
    )


# ===========================================================================
# FactureClient — Create
# ===========================================================================


@login_required(login_url=LOGIN_URL)
@require_branche_context
def facture_client_create(request, client_pk=None):
    """
    Create a client invoice by selecting Livré BL Clients.

    BR-FAC-01: montant_ht/tva/ttc are computed from BL lines in the
               m2m_changed signal (fires after save_m2m() links the BLs).
    BR-FAC-02: the form filters BLs to Livré BLs for the selected client.
    BR-BRA-01/04: the invoice belongs to the request's active branche —
               only that branche's Livré BLs can be selected; Vue Globale
               cannot reach this view (@require_branche_context).

    Accepts an optional `client_pk` URL parameter to pre-select the client.
    """
    branche = get_active_branche(request)
    client = None
    if client_pk:
        client = get_object_or_404(Client, pk=client_pk)

    if request.method == "POST":
        form = FactureClientForm(request.POST, client=client, branche=branche)
        pj_formset = build_piece_jointe_formset(
            FactureClientPieceJointeFormSet, request, prefix="pj"
        )

        if form.is_valid() and pj_formset.is_valid():
            try:
                with transaction.atomic():
                    facture = form.save(commit=False)
                    facture.created_by = request.user
                    # Auto-generate reference if blank
                    if not facture.reference:
                        facture.reference = generer_reference_facture_client(branche)
                    # montant_ht/tva/ttc initialised to 0 here;
                    # the m2m_changed signal recalculates and persists them
                    # after form.save_m2m() links the BLs.
                    facture.save()
                    # M2M must be saved after the instance has a PK —
                    # this triggers the m2m_changed signal.
                    form.save_m2m()
                    pj_formset.instance = facture
                    pj_formset.save()

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
            initial["reference"] = generer_reference_facture_client(branche)
        form = FactureClientForm(client=client, branche=branche, initial=initial)
        pj_formset = build_piece_jointe_formset(
            FactureClientPieceJointeFormSet, request, prefix="pj"
        )

    return render(
        request,
        "clients/facture_client_form.html",
        {
            "form": form,
            "pj_formset": pj_formset,
            "client": client,
            "active_branche": branche,
            "title": "فاتورة عميل جديدة",
            "action_label": "إنشاء الفاتورة",
        },
    )


# ===========================================================================
# FactureClient — Detail
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def facture_client_detail(request, pk):
    facture = branche_object_or_404(
        request,
        FactureClient.objects.select_related("client", "created_by"),
        pk=pk,
    )
    bls = facture.bls.prefetch_related("lignes__produit_fini").order_by("date_bl")
    allocations = facture.allocations.select_related("paiement").order_by(
        "-paiement__date_paiement"
    )
    pieces_jointes = facture.pieces_jointes.select_related("uploaded_by").order_by(
        "-created_at"
    )
    pj_formset = build_piece_jointe_formset(
        FactureClientPieceJointeFormSet, request, instance=facture, prefix="pj"
    )

    # Same admin pattern used elsewhere in this file (bl_client_detail) —
    # controls visibility of the cascade-delete button (facture + its BLs +
    # its paiements).
    is_admin = request.user.is_superuser or (
        hasattr(request.user, "userprofile")
        and request.user.userprofile.role == "admin"
    )

    return render(
        request,
        "clients/facture_client_detail.html",
        {
            "facture": facture,
            "bls": bls,
            "allocations": allocations,
            "pieces_jointes": pieces_jointes,
            "pj_formset": pj_formset,
            "is_admin": is_admin,
            "title": f"فاتورة — {facture.reference}",
        },
    )


# ===========================================================================
# FactureClient — Ajouter des pièces jointes (pas d'édition possible)
# ===========================================================================


@login_required(login_url=LOGIN_URL)
@require_POST
def facture_client_ajouter_piece_jointe(request, pk):
    """
    FactureClient has no edit view (locked once its BLs are linked), so
    proof documents added after the fact go through this dedicated
    POST-only action.
    """
    facture = branche_object_or_404(request, FactureClient, pk=pk)
    pj_formset = build_piece_jointe_formset(
        FactureClientPieceJointeFormSet, request, instance=facture, prefix="pj"
    )
    if pj_formset.is_valid():
        pj_formset.save()
        messages.success(request, "تم إضافة المرفقات.")
    else:
        messages.error(request, "يرجى تصحيح الأخطاء في المرفقات.")
    return redirect("clients:facture_client_detail", pk=pk)


# ===========================================================================
# FactureClient — Print
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def facture_client_print(request, pk):
    """
    Print-optimised invoice view.
    Renders a dedicated template with @media print CSS.
    """
    facture = branche_object_or_404(
        request,
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
# FactureClient — Delete (cascade, admin-only)
# ===========================================================================


@login_required(login_url=LOGIN_URL)
@require_POST
def facture_client_delete(request, pk):
    """
    ADMIN-ONLY hard delete: remove a FactureClient together with every BL it
    includes and every PaiementClient that paid it.

    This intentionally bypasses BR-BLC-03 (BL lock) and the normal
    immutability of PaiementClient/PaiementClientAllocation — restricted to
    admins (is_superuser or userprofile.role=="admin") and POST-only. See
    clients.utils.supprimer_facture_client_cascade for the full cascade and
    its side effects, including on OTHER invoices a shared paiement also
    paid.
    """
    is_admin = request.user.is_superuser or (
        hasattr(request.user, "userprofile")
        and request.user.userprofile.role == "admin"
    )

    if not is_admin:
        messages.error(request, "غير مسموح: هذا الإجراء متاح للمدراء فقط.")
        return redirect("clients:facture_client_detail", pk=pk)

    facture = branche_object_or_404(request, FactureClient, pk=pk)

    from clients.utils import supprimer_facture_client_cascade

    try:
        summary = supprimer_facture_client_cascade(facture)
    except Exception as exc:
        logger.exception("Error deleting FactureClient pk=%s: %s", pk, exc)
        messages.error(request, f"خطأ أثناء الحذف: {exc}")
        return redirect("clients:facture_client_detail", pk=pk)

    msg = (
        f"تم حذف الفاتورة {summary['facture_reference']} نهائيًا مع "
        f"{len(summary['bls_references'])} وصل تسليم و "
        f"{len(summary['paiements_references'])} دفعة مرتبطة."
    )
    if summary["factures_tierces_impactees"]:
        autres = ", ".join(sorted(set(summary["factures_tierces_impactees"])))
        msg += (
            f" تنبيه: تأثرت فواتير أخرى ({autres}) لأنها شاركت في نفس "
            "الدفعات المحذوفة — يرجى مراجعة أرصدتها."
        )
    messages.success(request, msg)
    logger.info(
        "FactureClient %s deleted (admin cascade) by '%s'. Summary: %s",
        summary["facture_reference"],
        request.user,
        summary,
    )
    return redirect("clients:facture_client_list")


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

    branche = get_active_branche(request)
    qs = PaiementClient.objects.select_related(
        "client", "branche", "created_by"
    ).order_by("-date_paiement", "-created_at")
    if branche is not None:
        qs = qs.filter(branche=branche)

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
            "active_branche": branche,
            "title": "مدفوعات العملاء",
        },
    )


# ===========================================================================
# PaiementClient — Create
# ===========================================================================


@login_required(login_url=LOGIN_URL)
@require_branche_context
def paiement_client_create(request, client_pk=None):
    """
    Record a client payment with automatic FIFO allocation.

    The FIFO engine (clients.utils.appliquer_paiement_client_fifo) runs
    immediately after the record is saved, scoped to this payment's own
    branche (BR-BRA-01) — it only settles open invoices in the same branche.

    BR-BRA-04: Vue Globale cannot reach this view (@require_branche_context);
               the active branche is pre-selected and locked on the form.

    Optional GET params:
      ?facture=<pk> — pre-fill montant with that invoice's reste_a_payer
    """
    branche = get_active_branche(request)
    client = None
    if client_pk:
        client = get_object_or_404(Client, pk=client_pk)

    # ── Resolve facture pre-population from ?facture=<pk> ────────────────
    # BR-BRA-01: only a facture in the active branche can be pre-filled.
    facture_obj = None
    facture_reste = None
    facture_pk_param = request.GET.get("facture") or request.POST.get("_facture_pk")
    if facture_pk_param and client:
        try:
            fo = FactureClient.objects.get(
                pk=facture_pk_param,
                client=client,
                branche=branche,
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

        form = PaiementClientForm(request.POST, client=client, branche=branche)
        pj_formset = build_piece_jointe_formset(
            PaiementClientPieceJointeFormSet, request, prefix="pj"
        )

        if form.is_valid() and pj_formset.is_valid():
            paiement_client = (
                form.client
                if hasattr(form, "client")
                else form.cleaned_data.get("client")
            )
            alloc_forms = get_allocation_forms(
                paiement_client, branche=branche, data=request.POST
            )

            # Validate all allocation forms
            alloc_valid = all(f.is_valid() for f in alloc_forms)

            if alloc_valid:
                try:
                    with transaction.atomic():
                        paiement = form.save(commit=False)
                        paiement.created_by = request.user
                        paiement.save()
                        pj_formset.instance = paiement
                        pj_formset.save()

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

                    if result.get("surplus"):
                        mode_label += (
                            f" تم تحويل {result['surplus']} دج غير مخصصة إلى دفعة "
                            "مسبقة، ستُخصم تلقائياً من الفواتير القادمة."
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
                alloc_forms = get_allocation_forms(
                    client, branche=branche, data=request.POST
                )
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")

    else:
        form = PaiementClientForm(client=client, branche=branche)
        alloc_forms = get_allocation_forms(client, branche=branche) if client else []
        pj_formset = build_piece_jointe_formset(
            PaiementClientPieceJointeFormSet, request, prefix="pj"
        )

    solde = get_client_solde(client, branche=branche) if client else None

    return render(
        request,
        "clients/paiement_client_form.html",
        {
            "form": form,
            "pj_formset": pj_formset,
            "alloc_forms": alloc_forms,
            "client": client,
            "facture_obj": facture_obj,
            "facture_reste": facture_reste,
            "solde": solde,
            "active_branche": branche,
            "title": "تسجيل دفعة عميل",
            "action_label": "حفظ الدفعة",
        },
    )


# ===========================================================================
# PaiementClient — Detail
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def paiement_client_detail(request, pk):
    paiement = branche_object_or_404(
        request,
        PaiementClient.objects.select_related("client", "created_by"),
        pk=pk,
    )
    allocations = paiement.allocations.select_related("facture").order_by(
        "facture__reference"
    )
    try:
        acompte = paiement.acompte
    except AcompteClient.DoesNotExist:
        acompte = None
    pieces_jointes = paiement.pieces_jointes.select_related("uploaded_by").order_by(
        "-created_at"
    )
    pj_formset = build_piece_jointe_formset(
        PaiementClientPieceJointeFormSet, request, instance=paiement, prefix="pj"
    )

    # Admin status controls visibility of the cascade-delete button — same
    # pattern as facture_client_detail.
    is_admin = request.user.is_superuser or (
        hasattr(request.user, "userprofile")
        and request.user.userprofile.role == "admin"
    )

    return render(
        request,
        "clients/paiement_client_detail.html",
        {
            "paiement": paiement,
            "allocations": allocations,
            "acompte": acompte,
            "pieces_jointes": pieces_jointes,
            "pj_formset": pj_formset,
            "is_admin": is_admin,
            "title": f"دفعة — {paiement.client.nom} — {paiement.date_paiement}",
        },
    )


# ===========================================================================
# PaiementClient — Delete (cascade, admin-only)
# ===========================================================================


@login_required(login_url=LOGIN_URL)
@require_POST
def paiement_client_delete(request, pk):
    """
    ADMIN-ONLY hard delete: remove a PaiementClient and reverse every side
    effect it produced (facture allocations, and anything its AcompteClient
    went on to fund).

    This intentionally bypasses the normal immutability of
    PaiementClient/PaiementClientAllocation — restricted to admins
    (is_superuser or userprofile.role=="admin") and POST-only. See
    clients.utils.supprimer_paiement_client_cascade for the full cascade.
    """
    is_admin = request.user.is_superuser or (
        hasattr(request.user, "userprofile")
        and request.user.userprofile.role == "admin"
    )

    if not is_admin:
        messages.error(request, "غير مسموح: هذا الإجراء متاح للمدراء فقط.")
        return redirect("clients:paiement_client_detail", pk=pk)

    paiement = branche_object_or_404(request, PaiementClient, pk=pk)

    from clients.utils import supprimer_paiement_client_cascade

    try:
        summary = supprimer_paiement_client_cascade(paiement)
    except Exception as exc:
        logger.exception("Error deleting PaiementClient pk=%s: %s", pk, exc)
        messages.error(request, f"خطأ أثناء الحذف: {exc}")
        return redirect("clients:paiement_client_detail", pk=pk)

    msg = (
        f"تم حذف الدفعة ({summary['paiement_montant']} دج — "
        f"{summary['client_nom']}) نهائيًا."
    )
    if summary["factures_impactees"]:
        autres = ", ".join(sorted(set(summary["factures_impactees"])))
        msg += f" تنبيه: تم تحديث أرصدة الفواتير التالية: {autres}."
    if summary["acompte_supprime"]:
        msg += " كما تم حذف الدفعة المقدمة الناتجة عن هذه الدفعة."
    messages.success(request, msg)
    logger.info(
        "PaiementClient (%s DZD, %s) deleted (admin cascade) by '%s'. Summary: %s",
        summary["paiement_montant"],
        summary["client_nom"],
        request.user,
        summary,
    )
    return redirect("clients:paiement_client_list")


# ===========================================================================
# PaiementClient — Ajouter des pièces jointes
# ===========================================================================


@login_required(login_url=LOGIN_URL)
@require_POST
def paiement_client_ajouter_piece_jointe(request, pk):
    """
    PaiementClient has no edit view, so proof documents added after the
    fact go through this dedicated POST-only action.
    """
    paiement = branche_object_or_404(request, PaiementClient, pk=pk)
    pj_formset = build_piece_jointe_formset(
        PaiementClientPieceJointeFormSet, request, instance=paiement, prefix="pj"
    )
    if pj_formset.is_valid():
        pj_formset.save()
        messages.success(request, "تم إضافة المرفقات.")
    else:
        messages.error(request, "يرجى تصحيح الأخطاء في المرفقات.")
    return redirect("clients:paiement_client_detail", pk=pk)


# ===========================================================================
# PaiementClient — Print
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def paiement_client_print(request, pk):
    """
    Print-optimised payment receipt.
    """
    paiement = branche_object_or_404(
        request,
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
# Acompte Client — List (created automatically from payment surplus)
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def acompte_client_list(request):
    """
    List prepayments (AcompteClient), created automatically whenever a
    PaiementClient leaves an unallocated balance.
    """
    branche = get_active_branche(request)
    qs = AcompteClient.objects.select_related("client", "branche", "paiement").order_by(
        "-date", "-created_at"
    )
    if branche is not None:
        qs = qs.filter(branche=branche)

    client_pk = request.GET.get("client", "")
    if client_pk:
        qs = qs.filter(client_id=client_pk)

    disponible_seulement = request.GET.get("disponible", "") == "1"
    if disponible_seulement:
        qs = qs.filter(montant_restant__gt=0)

    page = _paginate(qs, request.GET.get("page"))
    clients = Client.objects.filter(actif=True).order_by("nom")

    return render(
        request,
        "clients/acompte_client_list.html",
        {
            "page": page,
            "clients": clients,
            "client_filter": client_pk,
            "disponible_seulement": disponible_seulement,
            "active_branche": branche,
            "title": "الدفعات المسبقة للعملاء",
        },
    )


# ===========================================================================
# Acompte Client — Detail
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def acompte_client_detail(request, pk):
    acompte = branche_object_or_404(
        request,
        AcompteClient.objects.select_related("client", "branche", "paiement"),
        pk=pk,
    )
    pieces_jointes = acompte.pieces_jointes.select_related("uploaded_by").order_by(
        "-created_at"
    )
    pj_formset = build_piece_jointe_formset(
        AcompteClientPieceJointeFormSet, request, instance=acompte, prefix="pj"
    )
    # Every facture this advance has (fully or partially) paid for.
    allocations = acompte.allocations.select_related("facture").order_by(
        "facture__date_facture"
    )
    return render(
        request,
        "clients/acompte_client_detail.html",
        {
            "acompte": acompte,
            "allocations": allocations,
            "montant_consomme": acompte.montant - acompte.montant_restant,
            "pieces_jointes": pieces_jointes,
            "pj_formset": pj_formset,
            "active_branche": get_active_branche(request),
            "title": f"دفعة مسبقة — {acompte.client.nom}",
        },
    )


# ===========================================================================
# Acompte Client — Ajouter des pièces jointes
#
# AcompteClient rows are created automatically (no create/edit view of
# their own), so proof documents are attached after the fact here.
# ===========================================================================


@login_required(login_url=LOGIN_URL)
@require_POST
def acompte_client_ajouter_piece_jointe(request, pk):
    acompte = branche_object_or_404(request, AcompteClient, pk=pk)
    pj_formset = build_piece_jointe_formset(
        AcompteClientPieceJointeFormSet, request, instance=acompte, prefix="pj"
    )
    if pj_formset.is_valid():
        pj_formset.save()
        messages.success(request, "تم إضافة المرفقات.")
    else:
        messages.error(request, "يرجى تصحيح الأخطاء في المرفقات.")
    return redirect("clients:acompte_client_detail", pk=pk)


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

    v1.4 (§3.5.5): Vue par Branche shows only the active branche's figures;
    Vue Globale aggregates across every branche.
    """
    import datetime

    today = datetime.date.today()
    branche = get_active_branche(request)

    # Top-level receivable metrics
    factures_ouvertes_qs = FactureClient.objects.filter(
        statut__in=[
            FactureClient.STATUT_NON_PAYEE,
            FactureClient.STATUT_PARTIELLEMENT_PAYEE,
        ]
    )
    bls_non_factures_qs = BLClient.objects.filter(statut=BLClient.STATUT_LIVRE)
    bls_recents_qs = BLClient.objects.all()
    paiements_recents_qs = PaiementClient.objects.all()
    if branche is not None:
        factures_ouvertes_qs = factures_ouvertes_qs.filter(branche=branche)
        bls_non_factures_qs = bls_non_factures_qs.filter(branche=branche)
        bls_recents_qs = bls_recents_qs.filter(branche=branche)
        paiements_recents_qs = paiements_recents_qs.filter(branche=branche)

    total_creances = (
        factures_ouvertes_qs.aggregate(total=Sum("reste_a_payer"))["total"] or 0
    )
    nb_factures_retard = factures_ouvertes_qs.filter(date_echeance__lt=today).count()

    # Clients exceeding credit ceiling — scoped to the active branche's
    # créance when one is selected (BR-BRA-01), Vue Globale otherwise.
    clients_hors_plafond = [
        c
        for c in Client.objects.filter(actif=True, plafond_credit__gt=0)
        if c.creance_globale(branche) > c.plafond_credit
    ]

    # Uninvoiced Livré BLs (eligible for invoicing — alert)
    bls_non_factures = bls_non_factures_qs.select_related("client").order_by(
        "-date_bl"
    )[:20]

    # Recent BLs
    bls_recents = bls_recents_qs.select_related("client").order_by(
        "-date_bl", "-created_at"
    )[:10]

    # Aged receivables (scoped to the active branche, Vue Globale sums all)
    aging_buckets = get_client_aging_buckets(branche=branche)

    # Recent payments
    paiements_recents = paiements_recents_qs.select_related("client").order_by(
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
            "active_branche": branche,
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
    branche = get_active_branche(request)
    solde = get_client_solde(client, branche=branche)

    data = {
        "creance_globale": float(solde["creance_globale"]),
        "acompte_disponible": float(solde["acompte_disponible"]),
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

    v1.4 (BR-BRA-01/02): Vue par Branche shows only the active branche's
    subscriptions; Vue Globale shows every branche's combined.
    """
    branche = get_active_branche(request)
    qs = AbonnementClient.objects.select_related(
        "client", "produit_fini", "branche"
    ).order_by("-date_debut")
    if branche is not None:
        qs = qs.filter(branche=branche)

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
            "active_branche": branche,
            "title": "اشتراكات العملاء",
        },
    )


@login_required(login_url=LOGIN_URL)
@require_branche_context
def abonnement_create(request, client_pk=None):
    """
    Create a new client subscription.

    BR-BRA-01/04: the agreement is fulfilled out of the request's active
    branche's stock — locked on the form; Vue Globale cannot reach this
    view (@require_branche_context).
    """
    branche = get_active_branche(request)
    client = None
    if client_pk:
        client = get_object_or_404(Client, pk=client_pk)

    if request.method == "POST":
        form = AbonnementClientForm(request.POST, client=client, branche=branche)
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
        form = AbonnementClientForm(initial=initial, client=client, branche=branche)

    return render(
        request,
        "clients/abonnement_form.html",
        {
            "form": form,
            "client": client,
            "active_branche": branche,
            "title": "اشتراك جديد",
            "action_label": "إنشاء",
        },
    )


@login_required(login_url=LOGIN_URL)
def abonnement_detail(request, pk):
    """
    Detail view for one subscription, with its partial deliveries.

    BR-ABO-03: when mode_facturation=forfait, also shows the billing
    history (FactureClient rows generated from this abonnement) and
    whether the current calendar-month period has already been billed —
    LivraisonPartielle stays visible/optional but doesn't drive what's due.
    """
    abo = branche_object_or_404(
        request,
        AbonnementClient.objects.select_related("client", "produit_fini", "branche"),
        pk=pk,
    )
    livraisons = abo.livraisons.select_related("voyage").order_by("-date")

    factures_abonnement = None
    periode_courante = None
    periode_deja_facturee = False
    if abo.est_forfait:
        factures_abonnement = abo.factures_abonnement.order_by("-periode_debut")
        periode_courante = abo.periode_courante()
        periode_deja_facturee = abo.echeance_deja_facturee(*periode_courante)

    is_admin = request.user.is_superuser or (
        hasattr(request.user, "userprofile")
        and request.user.userprofile.role == "admin"
    )

    return render(
        request,
        "clients/abonnement_detail.html",
        {
            "abo": abo,
            "livraisons": livraisons,
            "factures_abonnement": factures_abonnement,
            "periode_courante": periode_courante,
            "periode_deja_facturee": periode_deja_facturee,
            "is_admin": is_admin,
            "title": f"الاشتراك — {abo.client.nom}",
        },
    )


@login_required(login_url=LOGIN_URL)
def abonnement_edit(request, pk):
    """Edit a subscription (status, dates, quantity). BR-BRA-02 scoped."""
    abo = branche_object_or_404(request, AbonnementClient, pk=pk)

    if request.method == "POST":
        form = AbonnementClientForm(
            request.POST, instance=abo, client=abo.client, branche=abo.branche
        )
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
        form = AbonnementClientForm(
            instance=abo, client=abo.client, branche=abo.branche
        )

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
    abo = branche_object_or_404(request, AbonnementClient, pk=pk)
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
# AbonnementClient — Delete (admin-only, POST-only)
# ===========================================================================


@login_required(login_url=LOGIN_URL)
@require_POST
def abonnement_delete(request, pk):
    """
    ADMIN-ONLY hard delete of a subscription. Blocked if it already has
    LivraisonPartielle or FactureClient rows against it (both are
    on_delete=PROTECT) — those must be dealt with first.
    """
    is_admin = request.user.is_superuser or (
        hasattr(request.user, "userprofile")
        and request.user.userprofile.role == "admin"
    )
    if not is_admin:
        messages.error(request, "غير مسموح: هذا الإجراء متاح للمدراء فقط.")
        return redirect("clients:abonnement_detail", pk=pk)

    abo = branche_object_or_404(request, AbonnementClient, pk=pk)

    if abo.livraisons.exists() or abo.factures_abonnement.exists():
        messages.error(
            request,
            "لا يمكن حذف هذا الاشتراك لوجود تسليمات أو فواتير مرتبطة به.",
        )
        return redirect("clients:abonnement_detail", pk=pk)

    client_pk = abo.client_id
    label = str(abo)
    try:
        abo.delete()
        messages.success(request, f"تم حذف الاشتراك « {label} ».")
        logger.info(
            "AbonnementClient pk=%s (%s) deleted by '%s'.", pk, label, request.user
        )
    except Exception as exc:
        logger.exception("Error deleting AbonnementClient pk=%s: %s", pk, exc)
        messages.error(request, f"خطأ أثناء الحذف: {exc}")
        return redirect("clients:abonnement_detail", pk=pk)

    return redirect("clients:client_detail", pk=client_pk)


@login_required(login_url=LOGIN_URL)
def generer_echeance_abonnement(request, pk):
    """
    Bill the current period of one forfait AbonnementClient (BR-ABO-03).

    GET shows a small confirmation form (optional date_facture/date_echeance
    override); POST creates the FactureClient via
    clients.utils.generer_facture_abonnement — which also auto-settles it
    from any AcompteClient the client already has (mode_paiement=prepaye).
    """
    abo = branche_object_or_404(request, AbonnementClient, pk=pk)

    if not abo.est_forfait:
        messages.error(
            request, "هذا الاشتراك ليس جزافياً — لا حاجة لتوليد فاتورة يدوياً."
        )
        return redirect("clients:abonnement_detail", pk=abo.pk)

    if request.method == "POST":
        form = GenererEcheanceAbonnementForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    facture = generer_facture_abonnement(
                        abo,
                        periode_debut=form.cleaned_data.get("periode_debut"),
                        periode_fin=form.cleaned_data.get("periode_fin"),
                        date_facture=form.cleaned_data.get("date_facture"),
                        date_echeance=form.cleaned_data.get("date_echeance"),
                        created_by=request.user,
                    )
                messages.success(
                    request,
                    f"تم توليد الفاتورة {facture.reference} بمبلغ "
                    f"{facture.montant_ttc} د.ج.",
                )
                logger.info(
                    "FactureClient pk=%s generated from abonnement pk=%s by '%s'.",
                    facture.pk,
                    abo.pk,
                    request.user,
                )
                return redirect("clients:facture_client_detail", pk=facture.pk)
            except ValueError as exc:
                messages.error(request, str(exc))
            except Exception as exc:
                logger.exception(
                    "Error generating facture for abonnement pk=%s: %s", abo.pk, exc
                )
                messages.error(request, f"خطأ أثناء التوليد: {exc}")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        import datetime

        periode_debut, periode_fin = abo.periode_courante()
        form = GenererEcheanceAbonnementForm(
            initial={
                "periode_debut": periode_debut,
                "periode_fin": periode_fin,
                "date_facture": datetime.date.today(),
            }
        )

    return render(
        request,
        "clients/generer_echeance_form.html",
        {
            "form": form,
            "abo": abo,
            "title": f"توليد فاتورة — {abo.client.nom}",
        },
    )


@login_required(login_url=LOGIN_URL)
@require_branche_context
@require_POST
def abonnements_generer_echeances(request):
    """
    Bulk-bill the current period for every active forfait AbonnementClient
    in the request's active branche (POST-only, idempotent — already-billed
    subscriptions are silently skipped). Vue Globale cannot reach this
    (@require_branche_context) since a chef-de-branche action shouldn't
    silently touch every branch's books at once.
    """
    branche = get_active_branche(request)
    resultat = generer_echeances_abonnements_forfait(
        branche=branche, created_by=request.user
    )

    if resultat["crees"]:
        messages.success(
            request,
            f"تم توليد {len(resultat['crees'])} فاتورة اشتراك "
            f"({sum(f.montant_ttc for f in resultat['crees'])} د.ج إجمالاً).",
        )
    if resultat["erreurs"]:
        for abo, err in resultat["erreurs"]:
            messages.error(
                request, f"{abo.client.nom} — {abo.produit_fini.designation}: {err}"
            )
    if not resultat["crees"] and not resultat["erreurs"]:
        messages.info(
            request, "لا توجد اشتراكات جزافية بحاجة إلى فاتورة جديدة هذا الشهر."
        )

    logger.info(
        "abonnements_generer_echeances: branche=%s — %d créée(s), %d ignorée(s), "
        "%d erreur(s), by '%s'.",
        branche.code if branche else "?",
        len(resultat["crees"]),
        len(resultat["ignores"]),
        len(resultat["erreurs"]),
        request.user,
    )
    return redirect("clients:abonnement_list")


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

    is_admin = request.user.is_superuser or (
        hasattr(request.user, "userprofile")
        and request.user.userprofile.role == "admin"
    )

    return render(
        request,
        "clients/voyage_list.html",
        {
            "page": page,
            "date_debut": date_debut,
            "date_fin": date_fin,
            "is_admin": is_admin,
            "title": "رحلات التوصيل",
        },
    )


@login_required(login_url=LOGIN_URL)
def voyage_create(request):
    """
    Create a new truck-trip record.

    v1.7: on success, redirects straight to the dépense form (pre-linked to
    this trip via ?voyage=<pk>) so the transport cost (fuel, driver fee...)
    gets logged right away instead of being forgotten.
    """
    if request.method == "POST":
        form = VoyageLivraisonForm(request.POST)
        if form.is_valid():
            try:
                voyage = form.save()
                messages.success(
                    request,
                    f"تم إنشاء رحلة التوصيل بتاريخ {voyage.date_voyage}. "
                    "أضف الآن تكلفة النقل الخاصة بها.",
                )
                logger.info(
                    "VoyageLivraison pk=%s created by '%s'.", voyage.pk, request.user
                )
                from django.urls import reverse

                return redirect(
                    reverse("depenses:depense_create") + f"?voyage={voyage.pk}"
                )
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
    """Detail: truck trip + all its partial deliveries + linked expenses."""
    voyage = get_object_or_404(VoyageLivraison, pk=pk)
    livraisons = voyage.livraisons.select_related(
        "abonnement__client", "abonnement__produit_fini"
    ).order_by("abonnement__client__nom")
    depenses_transport = voyage.depenses_transport.select_related("categorie").order_by(
        "-date"
    )

    is_admin = request.user.is_superuser or (
        hasattr(request.user, "userprofile")
        and request.user.userprofile.role == "admin"
    )

    return render(
        request,
        "clients/voyage_detail.html",
        {
            "voyage": voyage,
            "livraisons": livraisons,
            "depenses_transport": depenses_transport,
            "is_admin": is_admin,
            "title": f"رحلة — {voyage.date_voyage}",
        },
    )


# ===========================================================================
# VoyageLivraison — Delete (admin-only, POST-only)
# ===========================================================================


@login_required(login_url=LOGIN_URL)
@require_POST
def voyage_delete(request, pk):
    """
    ADMIN-ONLY hard delete of a truck trip. Any LivraisonPartielle records
    that pointed to it simply fall back to voyage=None (SET_NULL) — their
    stock effect is untouched. Blocked if it still has linked transport
    dépenses, so those aren't silently orphaned.
    """
    is_admin = request.user.is_superuser or (
        hasattr(request.user, "userprofile")
        and request.user.userprofile.role == "admin"
    )
    if not is_admin:
        messages.error(request, "غير مسموح: هذا الإجراء متاح للمدراء فقط.")
        return redirect("clients:voyage_detail", pk=pk)

    voyage = get_object_or_404(VoyageLivraison, pk=pk)

    if voyage.depenses_transport.exists():
        messages.error(
            request,
            "لا يمكن حذف هذه الرحلة لوجود مصاريف نقل مرتبطة بها. "
            "احذف المصاريف أولاً.",
        )
        return redirect("clients:voyage_detail", pk=pk)

    date_voyage = voyage.date_voyage
    try:
        voyage.delete()
        messages.success(request, f"تم حذف رحلة التوصيل بتاريخ {date_voyage}.")
        logger.info(
            "VoyageLivraison pk=%s (%s) deleted by '%s'.", pk, date_voyage, request.user
        )
    except Exception as exc:
        logger.exception("Error deleting VoyageLivraison pk=%s: %s", pk, exc)
        messages.error(request, f"خطأ أثناء الحذف: {exc}")
        return redirect("clients:voyage_detail", pk=pk)

    return redirect("clients:voyage_list")


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
    StockMouvement (SORTIE / LIVRAISON_ABONNEMENT). BR-BRA-02: the
    subscription must belong to the request's active branche.
    """
    abo = branche_object_or_404(request, AbonnementClient, pk=abonnement_pk)

    if abo.statut != AbonnementClient.STATUT_ACTIF:
        messages.error(
            request,
            "لا يمكن تسجيل تسليم على اشتراك غير نشط.",
        )
        return redirect("clients:abonnement_detail", pk=abo.pk)

    if request.method == "POST":
        form = LivraisonPartielleForm(request.POST, abonnement=abo, branche=abo.branche)
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
            abonnement=abo, branche=abo.branche, initial={"date": datetime.date.today()}
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
    BR-BRA-02: LivraisonPartielle.branche is derived from its parent
    abonnement (no stored FK), so the guard reads `.branche` via
    `branche_matches` instead of `branche_object_or_404`.
    """
    livraison = get_object_or_404(
        LivraisonPartielle.objects.select_related("abonnement"), pk=pk
    )
    abo = livraison.abonnement
    if not branche_matches(request, livraison):
        raise Http404("Cette livraison appartient à une autre branche.")

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

    v1.4 (§3.5.5): reports default to the request's active branche; Vue
    Globale shows this client's deliveries across every branche combined.

    Query params:
      ?date_debut=YYYY-MM-DD
      ?date_fin=YYYY-MM-DD
      ?produit_fini=<pk>   — filter to one product
    """
    import datetime as dt
    from decimal import Decimal

    client = get_object_or_404(Client, pk=pk)
    branche = get_active_branche(request)

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
    if branche is not None:
        qs = qs.filter(bl__branche=branche)

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
            type_produit__code="OEUFS",
        )
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
            "active_branche": branche,
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
    produits = ProduitFini.objects.filter(type_produit__code="OEUFS").order_by(
        "designation"
    )

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
