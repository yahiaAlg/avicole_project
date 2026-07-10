"""
elevage/views.py

Function-based views for the poultry batch (lot d'élevage) domain:

  LotElevage   : list, create, detail, edit, close (fermer)
  Mortalite    : create, edit, delete  (on open lots only — BR-LOT-03)
  Consommation : create, edit, delete  (on open lots only — BR-LOT-03)

Business rules enforced here (complementing model.clean() and signals):
  BR-LOT-01  Lot opening requires initial chick count and building.
  BR-LOT-02  Effectif vivant is computed — never edited directly.
  BR-LOT-03  Consommation and mortalité are only permitted on open lots.
  BR-LOT-04  Closing a lot requires at least one validated production record.
  BR-LOT-05  A closed lot is fully locked — no further entries of any type.
  BR-INT-03  Consommation quantity cannot exceed available stock.

All write operations use Post-Redirect-Get.
State changes (close lot, delete mortality/consumption) are POST-only.
"""

import datetime
import json
import logging
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db import transaction
from django.db.models import Q, Sum
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from core.views import (
    branche_matches,
    branche_object_or_404,
    get_active_branche,
    require_branche_context,
)
from elevage.forms import (
    ConsommationForm,
    ConsommationMedicamentForm,
    ConsommationMedicamentPaiementForm,
    FormuleAlimentForm,
    FormuleAlimentLigneFormSet,
    LotElevageForm,
    LotFermetureForm,
    MortaliteForm,
    PeseeEchantillonForm,
    ProductionAlimentForm,
    ProductionAlimentPaiementForm,
    RecolteOeufsForm,
    RetraitOeufsForm,
    TransfertLotForm,
)
from elevage.models import (
    Consommation,
    FormuleAliment,
    FormuleAlimentLigne,
    LotElevage,
    Mortalite,
    PeseeEchantillon,
    ProductionAliment,
    RecolteOeufs,
    RetraitOeufs,
    TransfertLot,
)
from elevage.utils import (
    get_lot_summary,
    get_lot_suivi_journalier,
    get_oeufs_fifo_allocation,
    get_oeufs_stock_lot,
    lots_a_transferer,
    verifier_mortalite_anormale,
)

logger = logging.getLogger(__name__)

LOGIN_URL = "core:login"
PER_PAGE = 25
SUIVI_PER_PAGE = 31  # ≈ un mois par page pour le tableau de suivi journalier


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_poussins_catalogue(branche):
    """
    JSON-serializable catalogue of chick (poussin) Intrant items for the
    lot_form's JS pre-population (désignation / souche / fournisseur /
    nombre_poussins_initial helpers).

    One entry per active Intrant in the POUSSIN category, with:
      - id, designation
      - stock: current StockIntrant balance for `branche` (0 if none/omitted)
      - fournisseurs: PKs of suppliers linked to this Intrant (M2M) — used
        to filter the fournisseur_poussins <select> client-side.
    """
    from intrants.models import Intrant

    qs = Intrant.objects.filter(categorie__code="POUSSIN", actif=True).prefetch_related(
        "fournisseurs"
    )

    return [
        {
            "id": intrant.pk,
            "designation": intrant.designation,
            "stock": float(intrant.quantite_en_stock(branche)),
            "fournisseurs": list(intrant.fournisseurs.values_list("pk", flat=True)),
        }
        for intrant in qs
    ]


def _build_batiments_meta(batiment_qs):
    """
    JSON-serializable {pk: {nom, capacite}} map for the lot_form's JS —
    used to build the désignation ("Lot <Mois> <Année> — <Bâtiment>") and to
    cap the suggested nombre_poussins_initial at the building's capacite.
    """
    return {str(b.pk): {"nom": b.nom, "capacite": b.capacite} for b in batiment_qs}


def _paginate(qs, page_number, per_page=PER_PAGE):
    paginator = Paginator(qs, per_page)
    try:
        return paginator.page(page_number)
    except PageNotAnInteger:
        return paginator.page(1)
    except EmptyPage:
        return paginator.page(paginator.num_pages)


def _assert_lot_ouvert(lot, request):
    """
    Return True if the lot is open; add an error message and return False
    otherwise.  Used as a guard before all write operations on a lot's
    sub-records.
    """
    if lot.statut == LotElevage.STATUT_FERME:
        messages.error(
            request,
            f"BR-LOT-05: الدفعة « {lot.designation} » مغلقة. لا يمكن إجراء أي تعديل.",
        )
        return False
    return True


def _auto_creer_depense_production_aliment(production, user):
    """
    Auto-create a Depense for a direct feed replenishment (no `formule`)
    entered with a known unit price — the operator is recording an actual
    cash/bank outlay ("we just bought/refilled X kg at Y DZD/kg"), so it
    should also land in the dépenses ledger without a second manual entry.

    Skipped when a `formule` was used (cost is only implied from ingredient
    PMP, not an actual outlay — see elevage.signals.production_aliment_post_save)
    or when prix_unitaire is 0 (no known cost to record).
    """
    from depenses.models import CategorieDepense, Depense

    if (
        production.formule_id
        or not production.prix_unitaire
        or production.prix_unitaire <= 0
    ):
        return None

    categorie, _ = CategorieDepense.objects.get_or_create(
        code="ACHAT_ALIMENT",
        defaults={
            "libelle": "شراء الأعلاف",
            "description": "مصاريف تزويد/شراء الأعلاف الجاهزة مباشرة (دون تركيبة).",
            "actif": True,
        },
    )

    return Depense.objects.create(
        date=production.date,
        branche=production.branche,
        categorie=categorie,
        description=(
            f"تزويد علف — {production.intrant_produit.designation} "
            f"({production.quantite_produite_kg} كغ)"
        ),
        montant=production.montant_total,
        mode_paiement=Depense.MODE_ESPECES,
        notes=production.notes,
        enregistre_par=user,
    )


def _recognize_facon_cost_for_batch(production, prix_facon_unitaire):
    """
    Batch costing (BR-request): called once a ProductionAliment batch's
    façon (mill labor) fee becomes known — i.e. from
    production_aliment_paiement_create — to stamp prix_facon_unitaire on
    the batch and retroactively recognize the cost for whatever portion of
    it was ALREADY consumed before the payment was recorded.

    Before this runs, every ConsommationAlimentAllocation row for this
    batch was created with cout_facon_alloue=0 (the fee wasn't known yet
    at consumption time — see elevage.signals._allouer_consommation_aliment).
    This re-prices those existing rows and rolls the total into
    production.cout_facon_impute, so LotElevage.cout_aliments reflects it
    immediately for every affected lot — not just future consumption.
    Consumption happening AFTER this call is priced directly at allocation
    time, since prix_facon_unitaire is now set on the batch.
    """
    from elevage.models import ConsommationAlimentAllocation

    production.prix_facon_unitaire = prix_facon_unitaire

    allocations = list(production.allocations_consommees.all())
    cout_deja_consomme = Decimal("0")
    for alloc in allocations:
        alloc.cout_facon_alloue = (alloc.quantite_kg * prix_facon_unitaire).quantize(
            Decimal("0.01")
        )
        cout_deja_consomme += alloc.cout_facon_alloue
    if allocations:
        ConsommationAlimentAllocation.objects.bulk_update(
            allocations, ["cout_facon_alloue"]
        )

    production.cout_facon_impute = cout_deja_consomme
    production.save(update_fields=["prix_facon_unitaire", "cout_facon_impute"])


def _auto_creer_depense_consommation_medicament(conso, user):
    """
    Auto-create a Depense for a médicament/vaccin Consommation entered with
    a known unit price — the operator is recording an actual cash/bank
    outlay ("we just used X units at Y DZD/unit"), so it should also land
    in the dépenses ledger without a second manual entry.

    Skipped when prix_unitaire is 0 (no known cost yet — the record stays
    `necessite_paiement` and awaits a later batched team/vet payment via
    consommation_medicament_paiement_create), or for feed (ALIMENT)
    consumption which never carries a cost. Mirrors
    _auto_creer_depense_production_aliment exactly.
    """
    from depenses.models import CategorieDepense, Depense

    if not conso.est_medicament or not conso.prix_unitaire or conso.prix_unitaire <= 0:
        return None

    categorie, _ = CategorieDepense.objects.get_or_create(
        code="ACHAT_MEDICAMENT",
        defaults={
            "libelle": "شراء أدوية/لقاحات",
            "description": "مصاريف استهلاك أدوية/لقاحات مُسعّرة مباشرة عند التسجيل.",
            "actif": True,
        },
    )

    return Depense.objects.create(
        date=conso.date,
        branche=conso.branche,
        lot=conso.lot,
        categorie=categorie,
        description=(
            f"استهلاك دواء — {conso.intrant.designation} "
            f"({conso.quantite} {conso.intrant.unite_mesure}) — دفعة "
            f"{conso.lot.designation}"
        ),
        montant=conso.montant_total,
        mode_paiement=Depense.MODE_ESPECES,
        notes=conso.notes,
        enregistre_par=user,
    )


def _ensure_branche_access(request, lot):
    """
    404 when *lot* (or a sub-record's parent lot) belongs to a branche other
    than the request's active one (BR-BRA-02) — a chef de branche/opérateur
    must never reach another branch's lot, even via a sub-record's pk.
    Vue Globale always passes.
    """
    if not branche_matches(request, lot):
        raise Http404("Ce lot appartient à une autre branche.")


def _scope_lots_pour_operateur_terrain(request, qs):
    """
    BR-RH-06 — an opérateur account auto-provisioned from an RH Employe
    record is restricted further than the usual branche scoping: it may
    only see currently OPEN lots in its own assigned bâtiment. Every other
    account (admin, manager, chef_branche, comptable, or a manually-created
    opérateur with no linked Employe) passes through unchanged.

    Returns an empty queryset (not a 404) when the operator has no bâtiment
    assigned yet — nothing to show rather than an error.
    """
    profile = getattr(request.user, "profile", None)
    if not getattr(profile, "est_operateur_terrain", False):
        return qs
    batiment_id = profile.employe.batiment_id if profile.employe_id else None
    if not batiment_id:
        return qs.none()
    return qs.filter(batiment_id=batiment_id, statut=LotElevage.STATUT_OUVERT)


def _ensure_lot_visible_pour_operateur_terrain(request, lot):
    """
    404 when an opérateur-terrain account (BR-RH-06) reaches a lot outside
    its own bâtiment, or a lot that's no longer open — mirrors
    _scope_lots_pour_operateur_terrain but for a single already-fetched
    lot (detail view and any sub-record view keyed off a lot pk).
    """
    profile = getattr(request.user, "profile", None)
    if not getattr(profile, "est_operateur_terrain", False):
        return
    batiment_id = profile.employe.batiment_id if profile.employe_id else None
    if (
        not batiment_id
        or lot.batiment_id != batiment_id
        or lot.statut != LotElevage.STATUT_OUVERT
    ):
        raise Http404("Ce lot n'est pas accessible à ce compte.")


# ===========================================================================
# LotElevage — List
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def lot_list(request):
    """
    List all lots d'élevage.

    Vue par Branche (BR-BRA-01/02): only the active branche's lots — exactly
    what a chef de branche sees. Vue Globale: every lot across all branches.

    Filters:
      ?statut=ouvert|ferme  — filter by lot status
      ?batiment=<pk>        — filter by building
      ?q=<search>           — search by designation or souche
    """
    from intrants.models import Batiment

    branche = get_active_branche(request)

    qs = LotElevage.objects.select_related(
        "batiment", "fournisseur_poussins", "created_by", "branche"
    ).order_by("-date_ouverture")
    if branche is not None:
        qs = qs.filter(branche=branche)
    qs = _scope_lots_pour_operateur_terrain(request, qs)

    statut = request.GET.get("statut", "")
    if statut in (LotElevage.STATUT_OUVERT, LotElevage.STATUT_FERME):
        qs = qs.filter(statut=statut)

    batiment_pk = request.GET.get("batiment", "")
    if batiment_pk:
        qs = qs.filter(batiment_id=batiment_pk)

    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(designation__icontains=q) | Q(souche__icontains=q))

    page = _paginate(qs, request.GET.get("page"))
    batiments = Batiment.objects.filter(actif=True)
    if branche is not None:
        batiments = batiments.filter(branche=branche)
    batiments = batiments.order_by("nom")

    # Count summaries for dashboard context (scoped the same way as the list)
    counts_qs = LotElevage.objects.all()
    if branche is not None:
        counts_qs = counts_qs.filter(branche=branche)
    counts_qs = _scope_lots_pour_operateur_terrain(request, counts_qs)
    nb_ouverts = counts_qs.filter(statut=LotElevage.STATUT_OUVERT).count()
    nb_fermes = counts_qs.filter(statut=LotElevage.STATUT_FERME).count()

    return render(
        request,
        "elevage/lot_list.html",
        {
            "page": page,
            "q": q,
            "statut": statut,
            "batiment_pk": batiment_pk,
            "batiments": batiments,
            "nb_ouverts": nb_ouverts,
            "nb_fermes": nb_fermes,
            "statut_choices": LotElevage.STATUT_CHOICES,
            "active_branche": branche,
            "title": "دفعات التربية",
        },
    )


# ===========================================================================
# LotElevage — Create
# ===========================================================================


@login_required(login_url=LOGIN_URL)
@require_branche_context
def lot_create(request):
    """
    Open a new lot d'élevage.

    BR-LOT-01: designation, date_ouverture, nombre_poussins_initial,
    fournisseur_poussins, and batiment are required.
    The BL fournisseur (poussins) is optional but recommended.

    BR-BRA-01: the lot's branche is derived from its `batiment` (denormalized
    in LotElevage.save()); the `batiment`/`bl_fournisseur_poussins` choices
    are scoped to the active branche so the derived branche is correct.
    """
    branche = get_active_branche(request)

    if request.method == "POST":
        form = LotElevageForm(request.POST, branche=branche)
        if form.is_valid():
            try:
                with transaction.atomic():
                    lot = form.save(commit=False)
                    lot.created_by = request.user
                    lot.save()

                messages.success(
                    request,
                    f"تم فتح الدفعة « {lot.designation} » بنجاح ({lot.nombre_poussins_initial} كتكوت).",
                )
                logger.info(
                    "LotElevage pk=%s ('%s') created by '%s'. "
                    "Poussins: %s, bâtiment: %s.",
                    lot.pk,
                    lot.designation,
                    request.user,
                    lot.nombre_poussins_initial,
                    lot.batiment,
                )
                return redirect("elevage:lot_detail", pk=lot.pk)

            except Exception as exc:
                logger.exception("Error creating LotElevage: %s", exc)
                messages.error(request, f"خطأ أثناء فتح الدفعة: {exc}")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        form = LotElevageForm(branche=branche)

    return render(
        request,
        "elevage/lot_form.html",
        {
            "form": form,
            "title": "فتح دفعة جديدة",
            "action_label": "فتح الدفعة",
            "poussins_catalogue": _build_poussins_catalogue(branche),
            "batiments_meta": _build_batiments_meta(form.fields["batiment"].queryset),
        },
    )


# ===========================================================================
# LotElevage — Detail
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def lot_detail(request, pk):
    """
    Full detail view for one lot d'élevage.

    Displays:
      - Key computed indicators (KPIs) via get_lot_summary()
      - Recent mortality records
      - Recent consumption records
      - Production records linked to this lot
      - Abnormal mortality warning flag
    """
    lot = branche_object_or_404(
        request,
        LotElevage.objects.select_related(
            "batiment", "fournisseur_poussins", "bl_fournisseur_poussins", "created_by"
        ),
        pk=pk,
    )
    _ensure_lot_visible_pour_operateur_terrain(request, lot)

    summary = get_lot_summary(lot)
    mortalite_anormale = verifier_mortalite_anormale(lot)

    # Paginate sub-lists for large lots
    mortalites_page = _paginate(
        summary["mortalites"], request.GET.get("page_mort"), per_page=10
    )
    # Split feed vs médicament consumption into two sections (separation of
    # concerns: distinct table, form and template per catégorie).
    consommations_page = _paginate(
        summary["consommations"].filter(intrant__categorie__code="ALIMENT"),
        request.GET.get("page_conso"),
        per_page=10,
    )
    consommations_medicament_page = _paginate(
        summary["consommations"].exclude(intrant__categorie__code="ALIMENT"),
        request.GET.get("page_medic"),
        per_page=10,
    )

    # Pick up the zero-effectif closure suggestion set by production_record_valider.
    session_key = f"suggest_fermeture_lot_{lot.pk}"
    suggest_fermeture = request.session.pop(session_key, False)

    transferts = lot.transferts.select_related(
        "batiment_origine", "batiment_destination"
    ).order_by("-date_transfert")
    pesees_page = _paginate(
        lot.pesees.order_by("-date"), request.GET.get("page_pesee"), per_page=10
    )
    recoltes_oeufs_page = _paginate(
        lot.recoltes_oeufs.select_related("pesee").order_by("-date"),
        request.GET.get("page_oeufs"),
        per_page=10,
    )
    retraits_oeufs_page = _paginate(
        summary["retraits_oeufs"],
        request.GET.get("page_retraits"),
        per_page=10,
    )

    return render(
        request,
        "elevage/lot_detail.html",
        {
            "lot": lot,
            "summary": summary,
            "mortalite_anormale": mortalite_anormale,
            "mortalites_page": mortalites_page,
            "consommations_page": consommations_page,
            "consommations_medicament_page": consommations_medicament_page,
            "productions": summary["productions"],
            "depenses": summary["depenses"],
            "suggest_fermeture": suggest_fermeture,
            "transferts": transferts,
            "pesees_page": pesees_page,
            "recoltes_oeufs_page": recoltes_oeufs_page,
            "retraits_oeufs_page": retraits_oeufs_page,
            "doit_etre_transfere": lot.doit_etre_transfere,
            "est_mature_pour_vente": lot.est_mature_pour_vente,
            "title": f"الدفعة — {lot.designation}",
        },
    )


# ===========================================================================
# LotElevage — Edit
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def lot_edit(request, pk):
    """
    Edit a lot's header information.

    BR-LOT-05: closed lots cannot be edited (guard applied here and in the
    template to disable the form).  Core operational data (effectif vivant,
    total mortalité) is computed — not editable through this view.
    """
    lot = branche_object_or_404(request, LotElevage, pk=pk)
    _ensure_lot_visible_pour_operateur_terrain(request, lot)

    if lot.statut == LotElevage.STATUT_FERME:
        messages.error(
            request,
            f"BR-LOT-05: الدفعة « {lot.designation} » مغلقة ولا يمكن تعديلها.",
        )
        return redirect("elevage:lot_detail", pk=lot.pk)

    if request.method == "POST":
        form = LotElevageForm(request.POST, instance=lot)
        if form.is_valid():
            try:
                form.save()
                messages.success(request, f"تم تحديث الدفعة « {lot.designation} ».")
                logger.info("LotElevage pk=%s updated by '%s'.", lot.pk, request.user)
                return redirect("elevage:lot_detail", pk=lot.pk)
            except Exception as exc:
                logger.exception("Error updating LotElevage pk=%s: %s", pk, exc)
                messages.error(request, f"خطأ أثناء التحديث: {exc}")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        form = LotElevageForm(instance=lot)

    return render(
        request,
        "elevage/lot_form.html",
        {
            "form": form,
            "lot": lot,
            "title": f"تعديل — {lot.designation}",
            "action_label": "حفظ التعديلات",
            "poussins_catalogue": _build_poussins_catalogue(lot.branche),
            "batiments_meta": _build_batiments_meta(form.fields["batiment"].queryset),
        },
    )


# ===========================================================================
# LotElevage — Close (Fermer)
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def lot_fermer(request, pk):
    """
    Close an open lot d'élevage.

    BR-LOT-04: at least one validated ProductionRecord must exist before
               closure is allowed — except for lots in a poussinière-type
               building, where this requirement is skipped entirely (the
               expected path there is transfer, not on-site production).
    BR-LOT-05: once closed, no further entries are accepted.

    GET  — renders the closure confirmation form.
    POST — validates, then calls lot.fermer(date_fermeture).
    """
    lot = branche_object_or_404(request, LotElevage, pk=pk)

    if lot.statut == LotElevage.STATUT_FERME:
        messages.warning(request, f"الدفعة « {lot.designation} » مغلقة مسبقًا.")
        return redirect("elevage:lot_detail", pk=lot.pk)

    # BR-LOT-04: at least one validated production record required.
    # Exception: lots housed in a poussinière-type building are exempt — the
    # expected path for such lots is transfer (TransfertLot) to a grow-out
    # building, not an on-site production record, so the requirement is
    # skipped entirely when the current batiment is a poussinière.
    from production.models import ProductionRecord
    from intrants.models import Batiment

    est_poussiniere = (
        lot.batiment_id and lot.batiment.type_batiment == Batiment.TYPE_POUSSINIERE
    )

    if not est_poussiniere:
        has_production = ProductionRecord.objects.filter(
            lot=lot,
            statut=ProductionRecord.STATUT_VALIDE,
        ).exists()

        if not has_production:
            messages.error(
                request,
                "BR-LOT-04: لا يمكن إغلاق الدفعة دون وجود سجل إنتاج محقق. يرجى تسجيل الإنتاج والتحقق منه أولًا.",
            )
            return redirect("elevage:lot_detail", pk=lot.pk)

    if request.method == "POST":
        form = LotFermetureForm(request.POST)
        if form.is_valid():
            try:
                date_fermeture = form.cleaned_data["date_fermeture"]
                lot.fermer(date_fermeture=date_fermeture)
                messages.success(
                    request,
                    f"تم إغلاق الدفعة « {lot.designation} » بتاريخ {date_fermeture}. التعداد النهائي: {lot.effectif_vivant} طير.",
                )
                logger.info(
                    "LotElevage pk=%s ('%s') closed by '%s' on %s.",
                    lot.pk,
                    lot.designation,
                    request.user,
                    date_fermeture,
                )
                return redirect("elevage:lot_detail", pk=lot.pk)
            except Exception as exc:
                logger.exception("Error closing LotElevage pk=%s: %s", pk, exc)
                messages.error(request, f"خطأ أثناء الإغلاق: {exc}")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        form = LotFermetureForm()

    # Summary stats for the confirmation page
    from elevage.utils import calculer_ic
    from decimal import Decimal

    total_mortalite = lot.total_mortalite
    effectif_final = lot.effectif_vivant
    taux_mortalite = lot.taux_mortalite
    conso_aliment = lot.consommation_totale_aliment

    productions = ProductionRecord.objects.filter(
        lot=lot, statut=ProductionRecord.STATUT_VALIDE
    )
    poids_total = productions.aggregate(total=Sum("poids_total_kg"))[
        "total"
    ] or Decimal("0")
    ic = calculer_ic(conso_aliment, poids_total)

    return render(
        request,
        "elevage/lot_fermer.html",
        {
            "form": form,
            "lot": lot,
            "total_mortalite": total_mortalite,
            "effectif_final": effectif_final,
            "taux_mortalite": taux_mortalite,
            "conso_aliment": conso_aliment,
            "poids_total": poids_total,
            "ic": ic,
            "title": f"إغلاق الدفعة — {lot.designation}",
        },
    )


# ===========================================================================
# Mortalite — Create
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def mortalite_create(request, lot_pk):
    """
    Record daily bird deaths on a lot.

    BR-LOT-03: only open lots accept new mortality records.
    The cumulative mortality cannot exceed the initial bird count (enforced
    in MortaliteForm.clean()).
    """
    lot = branche_object_or_404(request, LotElevage, pk=lot_pk)

    if not _assert_lot_ouvert(lot, request):
        return redirect("elevage:lot_detail", pk=lot.pk)

    if request.method == "POST":
        form = MortaliteForm(request.POST, lot=lot)
        if form.is_valid():
            try:
                mortalite = form.save(commit=False)
                mortalite.lot = lot
                mortalite.save()

                messages.success(
                    request,
                    f"تم تسجيل {mortalite.nombre} نفوق بتاريخ {mortalite.date}. التعداد الحي: {lot.effectif_vivant}.",
                )
                logger.info(
                    "Mortalite pk=%s created (lot pk=%s, nombre=%s, date=%s) by '%s'.",
                    mortalite.pk,
                    lot.pk,
                    mortalite.nombre,
                    mortalite.date,
                    request.user,
                )

                # Alert if daily mortality is abnormal.
                if verifier_mortalite_anormale(lot):
                    messages.warning(
                        request,
                        "⚠ تنبيه: النفوق اليومي تجاوز الحد المعتاد (≥ 5%). يرجى مراجعة حالة الدفعة.",
                    )

                return redirect("elevage:lot_detail", pk=lot.pk)

            except Exception as exc:
                logger.exception(
                    "Error creating Mortalite for lot pk=%s: %s", lot.pk, exc
                )
                messages.error(request, f"خطأ أثناء التسجيل: {exc}")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        import datetime

        form = MortaliteForm(lot=lot, initial={"date": datetime.date.today()})

    return render(
        request,
        "elevage/mortalite_form.html",
        {
            "form": form,
            "lot": lot,
            "title": f"تسجيل نفوق — {lot.designation}",
            "action_label": "حفظ",
        },
    )


# ===========================================================================
# Mortalite — Edit
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def mortalite_edit(request, pk):
    """
    Edit an existing mortality record.

    BR-LOT-05: editing is blocked on closed lots.
    Cumulative-mortality guard is re-applied in MortaliteForm.clean().
    """
    mortalite = get_object_or_404(Mortalite.objects.select_related("lot"), pk=pk)
    lot = mortalite.lot
    _ensure_branche_access(request, lot)

    if not _assert_lot_ouvert(lot, request):
        return redirect("elevage:lot_detail", pk=lot.pk)

    if request.method == "POST":
        form = MortaliteForm(request.POST, instance=mortalite, lot=lot)
        if form.is_valid():
            try:
                form.save()
                messages.success(
                    request,
                    f"تم تحديث النفوق بتاريخ {mortalite.date}. التعداد الحي: {lot.effectif_vivant}.",
                )
                logger.info(
                    "Mortalite pk=%s updated by '%s'.", mortalite.pk, request.user
                )
                return redirect("elevage:lot_detail", pk=lot.pk)
            except Exception as exc:
                logger.exception("Error updating Mortalite pk=%s: %s", pk, exc)
                messages.error(request, f"خطأ أثناء التحديث: {exc}")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        form = MortaliteForm(instance=mortalite, lot=lot)

    return render(
        request,
        "elevage/mortalite_form.html",
        {
            "form": form,
            "lot": lot,
            "mortalite": mortalite,
            "title": f"تعديل النفوق بتاريخ {mortalite.date}",
            "action_label": "حفظ التعديلات",
        },
    )


# ===========================================================================
# Mortalite — Delete
# ===========================================================================


@login_required(login_url=LOGIN_URL)
@require_POST
def mortalite_delete(request, pk):
    """
    Delete a mortality record (POST-only).

    BR-LOT-05: deletion blocked on closed lots.
    No stock reversal needed — mortality does not affect intrant stock.
    """
    mortalite = get_object_or_404(Mortalite.objects.select_related("lot"), pk=pk)
    lot = mortalite.lot
    _ensure_branche_access(request, lot)

    if not _assert_lot_ouvert(lot, request):
        return redirect("elevage:lot_detail", pk=lot.pk)

    try:
        date_ref = mortalite.date
        nombre_ref = mortalite.nombre
        mortalite.delete()
        messages.success(
            request,
            f"تم حذف سجل النفوق بتاريخ {date_ref} ({nombre_ref} نفوق).",
        )
        logger.info(
            "Mortalite pk=%s deleted by '%s' (lot pk=%s).",
            pk,
            request.user,
            lot.pk,
        )
    except Exception as exc:
        logger.exception("Error deleting Mortalite pk=%s: %s", pk, exc)
        messages.error(request, f"خطأ أثناء الحذف: {exc}")

    return redirect("elevage:lot_detail", pk=lot.pk)


# ===========================================================================
# Consommation — Create (shared helper: feed vs médicament)
# ===========================================================================


def _consommation_create(request, lot_pk, form_class, template, kind_label):
    """
    Shared create logic for both feed (ConsommationForm) and médicament
    (ConsommationMedicamentForm) consumption records — same underlying
    Consommation model, only the form's intrant scope and the rendering
    template differ (separation of concerns per catégorie of consumption).

    BR-LOT-03: only permitted on open lots.
    BR-INT-03: quantity cannot exceed available stock — enforced in the
               form's clean() and double-checked here atomically.

    BR-BRA-07: StockIntrant is now one row per (branche, intrant), so the
    in-view availability check below is scoped to `lot.branche`.
    """
    lot = branche_object_or_404(request, LotElevage, pk=lot_pk)

    if not _assert_lot_ouvert(lot, request):
        return redirect("elevage:lot_detail", pk=lot.pk)

    if request.method == "POST":
        form = form_class(request.POST, lot=lot)
        if form.is_valid():
            try:
                with transaction.atomic():
                    # Double-check stock availability atomically before commit
                    # to guard against race conditions (form check is pre-lock).
                    intrant = form.cleaned_data["intrant"]
                    quantite = form.cleaned_data["quantite"]

                    from stock.models import StockIntrant

                    try:
                        stock = StockIntrant.objects.select_for_update().get(
                            branche=lot.branche, intrant=intrant
                        )
                        if quantite > stock.quantite:
                            messages.error(
                                request,
                                f"BR-INT-03 : stock insuffisant pour "
                                f"« {intrant.designation} ». "
                                f"Disponible : {stock.quantite} {intrant.unite_mesure} — "
                                f"Demandé : {quantite} {intrant.unite_mesure}.",
                            )
                            return render(
                                request,
                                template,
                                {
                                    "form": form,
                                    "lot": lot,
                                    "title": f"تسجيل {kind_label} — {lot.designation}",
                                    "action_label": "حفظ",
                                },
                            )
                    except StockIntrant.DoesNotExist:
                        messages.error(
                            request,
                            f"Aucun stock disponible pour « {intrant.designation} ». "
                            "Vérifiez les réceptions (BL fournisseur).",
                        )
                        return render(
                            request,
                            template,
                            {
                                "form": form,
                                "lot": lot,
                                "title": f"تسجيل {kind_label} — {lot.designation}",
                                "action_label": "حفظ",
                            },
                        )

                    conso = form.save(commit=False)
                    conso.lot = lot
                    conso.created_by = request.user
                    conso.save()  # triggers signal → stock decrease + mouvement

                    # BR-request: médicament/vaccin consumptions priced
                    # directly at entry get their cost auto-expensed right
                    # away; left at 0, they stay `necessite_paiement` and
                    # wait for a later batched team/vet payment instead
                    # (see consommation_medicament_list/paiement_create).
                    depense = None
                    if conso.est_medicament:
                        depense = _auto_creer_depense_consommation_medicament(
                            conso, request.user
                        )

                if depense is not None:
                    messages.success(
                        request,
                        f"تم تسجيل الاستهلاك: {conso.quantite} {intrant.unite_mesure} "
                        f"من « {intrant.designation} » بتاريخ {conso.date} وإنشاء "
                        f"مصروف تلقائي بمبلغ {depense.montant} د.ج.",
                    )
                elif conso.est_medicament:
                    messages.success(
                        request,
                        f"تم تسجيل الاستهلاك: {conso.quantite} {intrant.unite_mesure} "
                        f"من « {intrant.designation} » بتاريخ {conso.date}. لا تنسَ "
                        f"دفع أجرة الطبيب/الفريق من «استهلاكات الأدوية» عند الحاجة.",
                    )
                else:
                    messages.success(
                        request,
                        f"تم تسجيل الاستهلاك: {conso.quantite} {intrant.unite_mesure} من « {intrant.designation} » بتاريخ {conso.date}.",
                    )
                logger.info(
                    "Consommation pk=%s created (lot pk=%s, intrant pk=%s, "
                    "quantite=%s, date=%s) by '%s' — depense_auto=%s.",
                    conso.pk,
                    lot.pk,
                    intrant.pk,
                    conso.quantite,
                    conso.date,
                    request.user,
                    depense.pk if depense is not None else None,
                )
                return redirect("elevage:lot_detail", pk=lot.pk)

            except Exception as exc:
                logger.exception(
                    "Error creating Consommation for lot pk=%s: %s", lot.pk, exc
                )
                messages.error(request, f"خطأ أثناء التسجيل: {exc}")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        import datetime

        form = form_class(lot=lot, initial={"date": datetime.date.today()})

    return render(
        request,
        template,
        {
            "form": form,
            "lot": lot,
            "title": f"تسجيل {kind_label} — {lot.designation}",
            "action_label": "حفظ",
        },
    )


@login_required(login_url=LOGIN_URL)
def consommation_create(request, lot_pk):
    """Record feed (aliment) consumption attributed to a lot."""
    return _consommation_create(
        request,
        lot_pk,
        ConsommationForm,
        "elevage/consommation_form.html",
        "استهلاك",
    )


@login_required(login_url=LOGIN_URL)
def consommation_medicament_create(request, lot_pk):
    """Record médicament/vaccin/vitamine/antibiotique/désinfectant consumption."""
    return _consommation_create(
        request,
        lot_pk,
        ConsommationMedicamentForm,
        "elevage/consommation_medicament_form.html",
        "دواء",
    )


# ===========================================================================
# Consommation — Edit
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def consommation_edit(request, pk):
    """
    Edit an existing consumption record.

    BR-LOT-05: editing blocked on closed lots.
    The signal handles the stock correction automatically:
      - On intrant change: reverses old intrant stock, applies full new quantity.
      - On quantity change: applies the net delta to the same intrant.
    """
    conso = get_object_or_404(
        Consommation.objects.select_related("lot", "intrant", "intrant__categorie"),
        pk=pk,
    )
    lot = conso.lot
    _ensure_branche_access(request, lot)

    if not _assert_lot_ouvert(lot, request):
        return redirect("elevage:lot_detail", pk=lot.pk)

    # Dispatch to the right form/template based on the record's own
    # catégorie (ALIMENT vs médicament) — same separation of concerns as
    # the create views, without needing two separate edit URLs.
    is_medicament = conso.intrant.categorie.code != "ALIMENT"
    form_class = ConsommationMedicamentForm if is_medicament else ConsommationForm
    template = (
        "elevage/consommation_medicament_form.html"
        if is_medicament
        else "elevage/consommation_form.html"
    )

    if request.method == "POST":
        form = form_class(request.POST, instance=conso, lot=lot)
        if form.is_valid():
            try:
                with transaction.atomic():
                    intrant = form.cleaned_data["intrant"]
                    quantite = form.cleaned_data["quantite"]

                    # Net delta check for same-intrant updates
                    from stock.models import StockIntrant

                    intrant_changed = intrant.pk != conso.intrant_id
                    if intrant_changed:
                        # Full new quantity required from new intrant stock.
                        stock_check_qty = quantite
                        stock_check_intrant = intrant
                    else:
                        # Only net additional quantity needed.
                        stock_check_qty = quantite - conso.quantite
                        stock_check_intrant = intrant

                    if stock_check_qty > 0:
                        try:
                            stock = StockIntrant.objects.select_for_update().get(
                                branche=lot.branche, intrant=stock_check_intrant
                            )
                            if stock_check_qty > stock.quantite:
                                messages.error(
                                    request,
                                    f"BR-INT-03 : stock insuffisant pour "
                                    f"« {intrant.designation} ». "
                                    f"Disponible : {stock.quantite} {intrant.unite_mesure} — "
                                    f"Variation demandée : +{stock_check_qty} "
                                    f"{intrant.unite_mesure}.",
                                )
                                return render(
                                    request,
                                    template,
                                    {
                                        "form": form,
                                        "lot": lot,
                                        "conso": conso,
                                        "title": "تعديل الاستهلاك",
                                        "action_label": "حفظ التعديلات",
                                    },
                                )
                        except StockIntrant.DoesNotExist:
                            messages.error(
                                request,
                                f"لا يوجد مخزون لـ « {intrant.designation} ».",
                            )
                            return render(
                                request,
                                template,
                                {
                                    "form": form,
                                    "lot": lot,
                                    "conso": conso,
                                    "title": "Modifier la consommation",
                                    "action_label": "حفظ التعديلات",
                                },
                            )

                    form.save()  # triggers pre_save + post_save signals

                messages.success(
                    request,
                    f"تم تحديث الاستهلاك بتاريخ {conso.date}.",
                )
                logger.info(
                    "Consommation pk=%s updated by '%s'.", conso.pk, request.user
                )
                return redirect("elevage:lot_detail", pk=lot.pk)

            except Exception as exc:
                logger.exception("Error updating Consommation pk=%s: %s", pk, exc)
                messages.error(request, f"خطأ أثناء التحديث: {exc}")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        form = form_class(instance=conso, lot=lot)

    return render(
        request,
        template,
        {
            "form": form,
            "lot": lot,
            "conso": conso,
            "title": f"تعديل الاستهلاك بتاريخ {conso.date}",
            "action_label": "حفظ التعديلات",
        },
    )


# ===========================================================================
# Consommation — Delete
# ===========================================================================


@login_required(login_url=LOGIN_URL)
@require_POST
def consommation_delete(request, pk):
    """
    Delete a consumption record (POST-only).

    BR-LOT-05: deletion blocked on closed lots.

    The pre_delete signal (elevage/signals.py) automatically restores the
    stock balance before the record is removed:
      - Increases StockIntrant.quantite by the consumed quantity.
      - Creates a corrective StockMouvement (entree).
    """
    conso = get_object_or_404(
        Consommation.objects.select_related("lot", "intrant"), pk=pk
    )
    lot = conso.lot
    _ensure_branche_access(request, lot)

    if not _assert_lot_ouvert(lot, request):
        return redirect("elevage:lot_detail", pk=lot.pk)

    try:
        date_ref = conso.date
        intrant_ref = conso.intrant.designation
        quantite_ref = conso.quantite
        unite_ref = conso.intrant.unite_mesure

        conso.delete()  # triggers pre_delete signal → stock restored

        messages.success(
            request,
            f"تم حذف الاستهلاك بتاريخ {date_ref} ({quantite_ref} {unite_ref} من « {intrant_ref} »). تم استعادة المخزون.",
        )
        logger.info(
            "Consommation pk=%s deleted by '%s' (lot pk=%s). "
            "Stock for intrant '%s' restored.",
            pk,
            request.user,
            lot.pk,
            intrant_ref,
        )
    except Exception as exc:
        logger.exception("Error deleting Consommation pk=%s: %s", pk, exc)
        messages.error(request, f"خطأ أثناء الحذف: {exc}")

    return redirect("elevage:lot_detail", pk=lot.pk)


# ===========================================================================
# Consommation — List (standalone, cross-lot)
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def consommation_list(request):
    """
    Cross-lot consumption list — useful for reporting and auditing feed usage.

    Filters:
      ?lot=<pk>           — filter by lot
      ?intrant=<pk>       — filter by intrant
      ?date_debut, ?date_fin
      ?q=<search>         — intrant designation or lot name
    """
    from intrants.models import Intrant

    branche = get_active_branche(request)

    qs = Consommation.objects.select_related(
        "lot", "intrant__categorie", "created_by"
    ).order_by("-date", "-created_at")
    if branche is not None:
        qs = qs.filter(lot__branche=branche)

    lot_pk = request.GET.get("lot", "")
    if lot_pk:
        qs = qs.filter(lot_id=lot_pk)

    intrant_pk = request.GET.get("intrant", "")
    if intrant_pk:
        qs = qs.filter(intrant_id=intrant_pk)

    date_debut = request.GET.get("date_debut", "")
    date_fin = request.GET.get("date_fin", "")
    if date_debut:
        qs = qs.filter(date__gte=date_debut)
    if date_fin:
        qs = qs.filter(date__lte=date_fin)

    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(
            Q(intrant__designation__icontains=q) | Q(lot__designation__icontains=q)
        )

    # Aggregate total consumed per intrant over the filtered period
    total_par_intrant = (
        qs.values("intrant__designation", "intrant__unite_mesure")
        .annotate(total=Sum("quantite"))
        .order_by("-total")[:10]
    )

    page = _paginate(qs, request.GET.get("page"))
    lots = LotElevage.objects.all()
    if branche is not None:
        lots = lots.filter(branche=branche)
    lots = lots.order_by("-date_ouverture")
    intrants = Intrant.objects.filter(
        categorie__consommable_en_lot=True, actif=True
    ).select_related("categorie")
    # Same rule as ConsommationForm: within catégorie ALIMENT, only the
    # finished feeds (Aliment Démarrage Poussin / Aliment Ponte Poule) are
    # ever actually consumed by a lot — raw ingredients only exist to be
    # milled into one via FormuleAliment, so they're excluded from this
    # filter too (they'd otherwise list options that never occur in the
    # Consommation table, since the model itself won't let a lot consume
    # them, or that would drop this filter out of sync with what the
    # create form even offers).
    raw_ingredient_ids = FormuleAlimentLigne.objects.values_list(
        "intrant_id", flat=True
    ).distinct()
    intrants = intrants.exclude(
        categorie__code="ALIMENT", pk__in=raw_ingredient_ids
    ).order_by("designation")

    return render(
        request,
        "elevage/consommation_list.html",
        {
            "page": page,
            "q": q,
            "lot_pk": lot_pk,
            "intrant_pk": intrant_pk,
            "date_debut": date_debut,
            "date_fin": date_fin,
            "lots": lots,
            "intrants": intrants,
            "total_par_intrant": total_par_intrant,
            "active_branche": branche,
            "title": "الاستهلاكات",
        },
    )


# ===========================================================================
# Consommation (médicament) — List + batched team/vet payment
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def consommation_medicament_list(request):
    """
    Cross-lot listing of médicament/vaccin Consommation records — its own
    separate view from the generic consommation_list (BR-request), mirroring
    production_aliment_list exactly. This is where consumptions left unpriced
    at entry (necessite_paiement) get selected and batched into ONE
    team/vet Depense; see consommation_medicament_paiement_create.

    Filters:
      ?intrant=<pk>       — médicament/vaccin
      ?lot=<pk>
      ?paiement=impaye|paye
      ?date_debut, ?date_fin
    """
    from intrants.models import Intrant

    branche = get_active_branche(request)

    qs = (
        Consommation.objects.select_related(
            "lot", "intrant", "intrant__categorie", "depense_paiement"
        )
        .exclude(intrant__categorie__code="ALIMENT")
        .order_by("-date", "-created_at")
    )
    if branche is not None:
        qs = qs.filter(lot__branche=branche)

    intrant_pk = request.GET.get("intrant", "")
    if intrant_pk:
        qs = qs.filter(intrant_id=intrant_pk)

    lot_pk = request.GET.get("lot", "")
    if lot_pk:
        qs = qs.filter(lot_id=lot_pk)

    date_debut = request.GET.get("date_debut", "")
    date_fin = request.GET.get("date_fin", "")
    if date_debut:
        qs = qs.filter(date__gte=date_debut)
    if date_fin:
        qs = qs.filter(date__lte=date_fin)

    statut_paiement = request.GET.get("paiement", "")
    if statut_paiement == "impaye":
        qs = qs.filter(prix_unitaire=0, depense_paiement__isnull=True)
    elif statut_paiement == "paye":
        qs = qs.filter(Q(depense_paiement__isnull=False) | Q(prix_unitaire__gt=0))

    page = _paginate(qs, request.GET.get("page"))

    en_attente_qs = Consommation.objects.exclude(
        intrant__categorie__code="ALIMENT"
    ).filter(prix_unitaire=0, depense_paiement__isnull=True)
    if branche is not None:
        en_attente_qs = en_attente_qs.filter(lot__branche=branche)
    total_qte_impaye = en_attente_qs.aggregate(total=Sum("quantite"))["total"] or 0

    intrants = (
        Intrant.objects.filter(categorie__consommable_en_lot=True, actif=True)
        .exclude(categorie__code="ALIMENT")
        .order_by("designation")
    )
    lots = LotElevage.objects.all()
    if branche is not None:
        lots = lots.filter(branche=branche)
    lots = lots.order_by("-date_ouverture")

    return render(
        request,
        "elevage/consommation_medicament_list.html",
        {
            "page": page,
            "intrant_pk": intrant_pk,
            "lot_pk": lot_pk,
            "statut_paiement": statut_paiement,
            "date_debut": date_debut,
            "date_fin": date_fin,
            "intrants": intrants,
            "lots": lots,
            "nb_impaye": en_attente_qs.count(),
            "total_qte_impaye": total_qte_impaye,
            "active_branche": branche,
            "title": "استهلاكات الأدوية واللقاحات",
        },
    )


@login_required(login_url=LOGIN_URL)
@require_branche_context
def consommation_medicament_paiement_create(request):
    """
    Consolidate one or more unpriced médicament/vaccin Consommation records
    into ONE Depense paying the veterinarian/team's fee: a single
    prix_unitaire × Σ quantite of everything selected. Exact mirror of
    production_aliment_paiement_create.

    GET  ?ids=<pk>&ids=<pk>…    — review screen (from
                                  consommation_medicament_list checkboxes),
                                  prix_unitaire not decided yet.
    POST consommation_ids=<pk>… — final submission: creates the Depense and
                                  marks every selected record paid via
                                  depense_paiement (so it drops out of the
                                  next batch — BR: never pay the same
                                  consumption twice).
    """
    from depenses.models import CategorieDepense, Depense

    branche = get_active_branche(request)

    base_qs = (
        Consommation.objects.exclude(intrant__categorie__code="ALIMENT")
        .filter(prix_unitaire=0, depense_paiement__isnull=True)
        .select_related("lot", "intrant")
    )
    if branche is not None:
        base_qs = base_qs.filter(lot__branche=branche)

    if request.method == "POST":
        ids = request.POST.getlist("consommation_ids")
        consommations = list(base_qs.filter(pk__in=ids))
        if not consommations:
            messages.error(
                request,
                "لم يتم العثور على استهلاكات صالحة للدفع ضمن الاختيار "
                "(ربما تم دفعها من قبل). يرجى إعادة الاختيار.",
            )
            return redirect("elevage:consommation_medicament_list")

        branches_selectionnees = {c.lot.branche_id for c in consommations}
        if len(branches_selectionnees) > 1:
            messages.error(
                request,
                "لا يمكن تجميع استهلاكات من فروع مختلفة ضمن مصروف دفع واحد. "
                "يرجى اختيار استهلاكات من نفس الفرع.",
            )
            return redirect("elevage:consommation_medicament_list")

        form = ConsommationMedicamentPaiementForm(request.POST)
        if form.is_valid():
            total_qte = sum((c.quantite for c in consommations), Decimal("0"))
            # BR-request fix: prix_unitaire is per-chick/bird, not per-dose
            # — multiply by the total effectif_vivant of the lot(s) covered
            # by this batch (summed once per distinct lot), not by the
            # summed dose quantite.
            lots_uniques = {c.lot_id: c.lot for c in consommations}
            total_effectif = sum(l.effectif_vivant for l in lots_uniques.values())

            # BR-request: non-homogeneous batches (several distinct
            # médicaments/vaccins in one vet visit) don't always have a
            # clean per-chick rate — the form now also accepts a single
            # direct lump-sum amount for the whole batch as an alternative
            # to prix_unitaire × total_effectif.
            mode_montant = form.cleaned_data["mode_montant"]
            prix_unitaire = form.cleaned_data.get("prix_unitaire")
            montant_direct = form.cleaned_data.get("montant_direct")
            if mode_montant == ConsommationMedicamentPaiementForm.MODE_DIRECT:
                montant = montant_direct.quantize(Decimal("0.01"))
                prix_unitaire = None
            else:
                montant = (total_effectif * prix_unitaire).quantize(Decimal("0.01"))

            categorie, _ = CategorieDepense.objects.get_or_create(
                code="MAIN_OEUVRE_MEDICAMENT",
                defaults={
                    "libelle": "أجرة الطبيب/الفريق البيطري",
                    "description": (
                        "أجرة الطبيب/الفريق البيطري عن استهلاكات أدوية/لقاحات."
                    ),
                    "actif": True,
                },
            )
            # BR-request: vaccination/médicament names travel from the
            # selected records into the Depense description, prepopulating
            # a sensible default (same as production_aliment_paiement's
            # `noms`) that stays free-text and fully editable via `notes`.
            noms = "، ".join(sorted({c.intrant.designation for c in consommations}))

            # BR-request: when every selected consumption belongs to the
            # same lot, attribute the Depense to it directly so it feeds
            # that lot's cout_total_depenses / marge_brute (lot_detail)
            # without a manual edit. A batch spanning several lots is left
            # unattributed (BR-DEP-04 is optional) — the user can still
            # assign one lot manually from the Depense edit screen.
            lots_selectionnes = set(lots_uniques.keys())
            lot_unique = consommations[0].lot if len(lots_selectionnes) == 1 else None

            try:
                with transaction.atomic():
                    depense = Depense.objects.create(
                        date=form.cleaned_data["date"],
                        branche=branche or consommations[0].lot.branche,
                        lot=lot_unique,
                        categorie=categorie,
                        description=(
                            f"دفع أجرة طبيب/فريق — {noms} "
                            f"({total_qte} عبر {len(consommations)} استهلاك)"
                        ),
                        montant=montant,
                        mode_paiement=form.cleaned_data["mode_paiement"],
                        notes=form.cleaned_data.get("notes", ""),
                        enregistre_par=request.user,
                    )
                    Consommation.objects.filter(
                        pk__in=[c.pk for c in consommations]
                    ).update(depense_paiement=depense)

                if mode_montant == ConsommationMedicamentPaiementForm.MODE_DIRECT:
                    detail_montant = f"مبلغ إجمالي مباشر لـ {total_effectif} طير"
                else:
                    detail_montant = f"{total_effectif} طير × {prix_unitaire} د.ج"
                messages.success(
                    request,
                    f"تم إنشاء مصروف بمبلغ {montant} د.ج ({detail_montant}) "
                    f"عبر {len(consommations)} استهلاك.",
                )
                logger.info(
                    "Depense pk=%s created for %s Consommation(médicament) "
                    "payment (mode_montant=%s, total_effectif=%s, total_qte=%s, "
                    "prix_unitaire=%s, montant_direct=%s) by '%s'.",
                    depense.pk,
                    len(consommations),
                    mode_montant,
                    total_effectif,
                    total_qte,
                    prix_unitaire,
                    montant_direct,
                    request.user,
                )
                # Same UX as production_aliment: land on the expense's edit
                # form directly, to attach a supporting document right away.
                return redirect("depenses:depense_edit", pk=depense.pk)
            except Exception as exc:
                logger.exception(
                    "Error creating paiement consommation medicament: %s", exc
                )
                messages.error(request, f"خطأ أثناء التسجيل: {exc}")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        ids = request.GET.getlist("ids")
        consommations = list(base_qs.filter(pk__in=ids)) if ids else []
        if not consommations:
            messages.error(request, "يرجى اختيار استهلاك واحد على الأقل قبل المتابعة.")
            return redirect("elevage:consommation_medicament_list")
        # BR-request: when the batch mixes several distinct médicaments/
        # vaccins, default the form to the direct lump-sum mode instead of
        # the per-chick unit price — a single per-bird rate rarely makes
        # sense across non-homogeneous products.
        intrants_distincts_init = sorted({c.intrant.designation for c in consommations})
        mode_initial = (
            ConsommationMedicamentPaiementForm.MODE_DIRECT
            if len(intrants_distincts_init) > 1
            else ConsommationMedicamentPaiementForm.MODE_UNITAIRE
        )
        form = ConsommationMedicamentPaiementForm(
            initial={"mode_montant": mode_initial}
        )

    total_qte = sum((c.quantite for c in consommations), Decimal("0"))
    lots_uniques_ctx = {c.lot_id: c.lot for c in consommations}
    total_effectif = sum(l.effectif_vivant for l in lots_uniques_ctx.values())
    # BR-request: surface to the template whether this batch is
    # non-homogeneous (>1 distinct médicament/vaccin), so it can nudge the
    # user toward the direct lump-sum pricing mode.
    intrants_distincts = sorted({c.intrant.designation for c in consommations})
    est_non_homogene = len(intrants_distincts) > 1

    # BR-request: "smarter" materials summary — instead of only listing every
    # selected Consommation row-by-row (noisy when the same médicament was
    # given on several dates), accumulate quantities per intrant and show
    # what that accumulated quantity is actually worth AT STOCK COST (PMP —
    # prix_moyen_pondéré), i.e. the material cost already being drawn from
    # stock by this batch. This is purely informational: it does NOT get
    # added to the Depense created below, which only ever pays the vet/team
    # labor fee — the material cost itself is already tracked via
    # StockIntrant/StockMouvement when each Consommation was first recorded
    # (see LotElevage.cout_medicaments, same PMP logic, mirrored here).
    from stock.models import StockIntrant

    groupes_par_intrant = {}
    for c in consommations:
        stock = StockIntrant.objects.filter(
            intrant_id=c.intrant_id, branche=c.lot.branche
        ).first()
        pmp = stock.prix_moyen_pondere if stock else Decimal("0")
        cout_ligne = c.quantite * pmp
        groupe = groupes_par_intrant.setdefault(
            c.intrant_id,
            {
                "designation": c.intrant.designation,
                "unite": c.intrant.unite_mesure,
                "quantite": Decimal("0"),
                "montant": Decimal("0"),
            },
        )
        groupe["quantite"] += c.quantite
        groupe["montant"] += cout_ligne

    materiel_groupes = []
    for g in sorted(groupes_par_intrant.values(), key=lambda g: g["designation"]):
        prix_moyen = (
            (g["montant"] / g["quantite"]).quantize(Decimal("0.0001"))
            if g["quantite"]
            else Decimal("0")
        )
        materiel_groupes.append(
            {
                "designation": g["designation"],
                "unite": g["unite"],
                "quantite": g["quantite"],
                "prix_moyen": prix_moyen,
                "montant": g["montant"].quantize(Decimal("0.01")),
            }
        )
    total_materiel = sum((g["montant"] for g in materiel_groupes), Decimal("0"))

    return render(
        request,
        "elevage/consommation_medicament_paiement_form.html",
        {
            "form": form,
            "consommations": consommations,
            "total_qte": total_qte,
            "total_effectif": total_effectif,
            "intrants_distincts": intrants_distincts,
            "est_non_homogene": est_non_homogene,
            "materiel_groupes": materiel_groupes,
            "total_materiel": total_materiel,
            "title": "دفع أجرة الطبيب/الفريق البيطري",
        },
    )


# ===========================================================================
# Mortalite — List (standalone, cross-lot)
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def mortalite_list(request):
    """
    Cross-lot mortality list — supports global mortality reporting.

    Filters:
      ?lot=<pk>           — filter by lot
      ?date_debut, ?date_fin
      ?q=<search>         — lot designation or cause
    """
    branche = get_active_branche(request)

    qs = Mortalite.objects.select_related("lot").order_by("-date", "-created_at")
    if branche is not None:
        qs = qs.filter(lot__branche=branche)

    lot_pk = request.GET.get("lot", "")
    if lot_pk:
        qs = qs.filter(lot_id=lot_pk)

    date_debut = request.GET.get("date_debut", "")
    date_fin = request.GET.get("date_fin", "")
    if date_debut:
        qs = qs.filter(date__gte=date_debut)
    if date_fin:
        qs = qs.filter(date__lte=date_fin)

    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(lot__designation__icontains=q) | Q(cause__icontains=q))

    # Cross-lot total for the filtered period
    total_mortalite = qs.aggregate(total=Sum("nombre"))["total"] or 0

    page = _paginate(qs, request.GET.get("page"))
    lots = LotElevage.objects.all()
    if branche is not None:
        lots = lots.filter(branche=branche)
    lots = lots.order_by("-date_ouverture")

    return render(
        request,
        "elevage/mortalite_list.html",
        {
            "page": page,
            "q": q,
            "lot_pk": lot_pk,
            "date_debut": date_debut,
            "date_fin": date_fin,
            "lots": lots,
            "total_mortalite": total_mortalite,
            "active_branche": branche,
            "title": "النفوق",
        },
    )


# ===========================================================================
# TransfertLot — Create  (Poussinière → Poulailler, immutable audit record)
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def transfert_create(request, lot_pk):
    """
    Move a lot from its current building to another, in one of three modes:

    MODE_FULL        — whole flock relocates (existing behaviour).
    MODE_SPLIT_NEW   — partial move; a child lot is created at destination;
                       source baseline decreases by effectif_transfere.
    MODE_SPLIT_MERGE — partial move; birds merge into an existing open lot at
                       destination; source baseline decreases, dest increases.

    TransfertLot records are immutable once created (no edit/delete view).
    The signal (transfert_lot_post_save) applies the chosen mode on save.
    """
    import datetime
    import json

    lot = branche_object_or_404(request, LotElevage, pk=lot_pk)

    if not _assert_lot_ouvert(lot, request):
        return redirect("elevage:lot_detail", pk=lot.pk)

    # Build {batiment_pk → [{pk, designation}]} for JS lot_destination
    # filtering. BR-BRA-01: a transfer never crosses branches, so this is
    # scoped to the source lot's own branche (mirrors TransfertLotForm).
    open_lots = (
        LotElevage.objects.filter(statut=LotElevage.STATUT_OUVERT, branche=lot.branche)
        .exclude(pk=lot.pk)
        .values("pk", "designation", "batiment_id")
    )
    lots_par_batiment: dict = {}
    for entry in open_lots:
        bid = str(entry["batiment_id"])
        lots_par_batiment.setdefault(bid, []).append(
            {"pk": entry["pk"], "designation": entry["designation"]}
        )
    lots_par_batiment_json = json.dumps(lots_par_batiment, ensure_ascii=False)

    if request.method == "POST":
        form = TransfertLotForm(request.POST, lot=lot)
        if form.is_valid():
            try:
                with transaction.atomic():
                    transfert = form.save(commit=False)
                    transfert.lot = lot
                    transfert.created_by = request.user
                    transfert.save()  # signal applies mode

                mode = transfert.mode
                mode_labels = {
                    TransfertLot.MODE_FULL: "نقل كامل",
                    TransfertLot.MODE_SPLIT_NEW: "تقسيم — دفعة فرعية جديدة",
                    TransfertLot.MODE_SPLIT_MERGE: "تقسيم — دمج في دفعة موجودة",
                }
                messages.success(
                    request,
                    f"({mode_labels.get(mode, mode)}) — تم نقل «{lot.designation}» "
                    f"إلى «{transfert.batiment_destination.nom}» "
                    f"({transfert.effectif_transfere} طير، العمر {transfert.age_jours_transfert} يوم).",
                )
                logger.info(
                    "TransfertLot pk=%s (mode=%s) created for lot pk=%s by '%s'.",
                    transfert.pk,
                    mode,
                    lot.pk,
                    request.user,
                )
                return redirect("elevage:lot_detail", pk=lot.pk)

            except Exception as exc:
                logger.exception(
                    "Error creating TransfertLot for lot pk=%s: %s", lot.pk, exc
                )
                messages.error(request, f"خطأ أثناء النقل: {exc}")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        form = TransfertLotForm(
            lot=lot,
            initial={
                "date_transfert": datetime.date.today(),
                "mode": TransfertLot.MODE_FULL,
            },
        )

    return render(
        request,
        "elevage/transfert_form.html",
        {
            "form": form,
            "lot": lot,
            "title": f"نقل الدفعة — {lot.designation}",
            "action_label": "تأكيد النقل",
            "lots_par_batiment_json": lots_par_batiment_json,
            "MODE_FULL": TransfertLot.MODE_FULL,
            "MODE_SPLIT_NEW": TransfertLot.MODE_SPLIT_NEW,
            "MODE_SPLIT_MERGE": TransfertLot.MODE_SPLIT_MERGE,
        },
    )


# ===========================================================================
# PeseeEchantillon — Create / Delete
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def pesee_create(request, lot_pk):
    """Record a sample weighing (birds or eggs) for a lot."""
    lot = branche_object_or_404(request, LotElevage, pk=lot_pk)

    if not _assert_lot_ouvert(lot, request):
        return redirect("elevage:lot_detail", pk=lot.pk)

    if request.method == "POST":
        form = PeseeEchantillonForm(request.POST, lot=lot, user=request.user)
        if form.is_valid():
            try:
                pesee = form.save(commit=False)
                pesee.lot = lot
                pesee.created_by = request.user
                pesee.save()

                qualite = pesee.qualite
                qualite_label = f" — الجودة: {qualite.libelle}" if qualite else ""
                messages.success(
                    request,
                    f"تم تسجيل وزن العينة: {pesee.poids_moyen_g} غ/وحدة "
                    f"({pesee.nombre_sujets} عينة){qualite_label}.",
                )
                logger.info(
                    "PeseeEchantillon pk=%s created (lot pk=%s) by '%s'.",
                    pesee.pk,
                    lot.pk,
                    request.user,
                )
                return redirect("elevage:lot_detail", pk=lot.pk)

            except Exception as exc:
                logger.exception(
                    "Error creating PeseeEchantillon for lot pk=%s: %s", lot.pk, exc
                )
                messages.error(request, f"خطأ أثناء التسجيل: {exc}")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        import datetime

        from elevage.signals import _get_produit_oeufs
        from clients.models import PrixMarche

        initial = {"date": datetime.date.today()}
        produit_oeufs = _get_produit_oeufs()
        prix_proche = None
        if produit_oeufs:
            # Pre-populate with the market price closest to the lot's own
            # latest date (falls back to today if the lot has no history yet).
            date_ref = (
                lot.pesees.filter(type_pesee=PeseeEchantillon.TYPE_OEUFS)
                .order_by("-date")
                .values_list("date", flat=True)
                .first()
                or datetime.date.today()
            )
            prix_proche = PrixMarche.get_closest_to(produit_oeufs, date_ref)
            if prix_proche:
                initial["prix_marche"] = prix_proche.pk

        form = PeseeEchantillonForm(lot=lot, user=request.user, initial=initial)

    from clients.models import PrixMarche

    prix_marche_data = {
        pm.pk: {
            "prix_marche": str(pm.prix_marche),
            "poids_reference_kg": str(pm.poids_reference_kg),
        }
        for pm in PrixMarche.objects.all()
    }

    return render(
        request,
        "elevage/pesee_form.html",
        {
            "form": form,
            "lot": lot,
            "title": f"وزن عينة — {lot.designation}",
            "action_label": "حفظ",
            "is_admin_user": getattr(form, "is_admin_user", False),
            "prix_marche_data_json": json.dumps(prix_marche_data),
            "prix_marche_create_url": reverse("elevage:prix_marche_quick_create_json"),
        },
    )


@login_required(login_url=LOGIN_URL)
@require_POST
def pesee_delete(request, pk):
    """
    Delete a sample weighing (POST-only).

    Any RecolteOeufs referencing this pesee falls back to qualite=None
    (on_delete=SET_NULL) rather than blocking the delete.
    """
    pesee = get_object_or_404(PeseeEchantillon.objects.select_related("lot"), pk=pk)
    lot = pesee.lot
    _ensure_branche_access(request, lot)

    if not _assert_lot_ouvert(lot, request):
        return redirect("elevage:lot_detail", pk=lot.pk)

    try:
        date_ref = pesee.date
        pesee.delete()
        messages.success(request, f"تم حذف وزن العينة بتاريخ {date_ref}.")
        logger.info("PeseeEchantillon pk=%s deleted by '%s'.", pk, request.user)
    except Exception as exc:
        logger.exception("Error deleting PeseeEchantillon pk=%s: %s", pk, exc)
        messages.error(request, f"خطأ أثناء الحذف: {exc}")

    return redirect("elevage:lot_detail", pk=lot.pk)


# ===========================================================================
# RecolteOeufs — Create / Edit / Delete / List
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def recolte_oeufs_create(request, lot_pk):
    """
    Record a daily egg-collection event for a lot in laying phase.

    On success the post_save signal (elevage/signals.py) automatically
    credits StockProduitFini for the farm's egg product and logs a
    StockMouvement (entree / ponte).

    BR-BRA-02: the lot must belong to the request's active branche.
    """
    lot = branche_object_or_404(request, LotElevage, pk=lot_pk)

    if not _assert_lot_ouvert(lot, request):
        return redirect("elevage:lot_detail", pk=lot.pk)

    if request.method == "POST":
        form = RecolteOeufsForm(request.POST, lot=lot)
        if form.is_valid():
            try:
                recolte = form.save(commit=False)
                recolte.lot = lot
                recolte.created_by = request.user
                recolte.save()  # triggers signal → stock entrée + mouvement

                messages.success(
                    request,
                    f"تم تسجيل {recolte.nombre_oeufs} بيضة "
                    f"({recolte.nombre_plateaux} صينية + {recolte.oeufs_hors_plateau} "
                    f"خارج الصينية) بتاريخ {recolte.date}.",
                )
                logger.info(
                    "RecolteOeufs pk=%s created (lot pk=%s, nombre=%s) by '%s'.",
                    recolte.pk,
                    lot.pk,
                    recolte.nombre_oeufs,
                    request.user,
                )
                return redirect("elevage:lot_detail", pk=lot.pk)

            except Exception as exc:
                logger.exception(
                    "Error creating RecolteOeufs for lot pk=%s: %s", lot.pk, exc
                )
                messages.error(request, f"خطأ أثناء التسجيل: {exc}")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        import datetime

        form = RecolteOeufsForm(lot=lot, initial={"date": datetime.date.today()})

    return render(
        request,
        "elevage/recolte_oeufs_form.html",
        {
            "form": form,
            "lot": lot,
            "title": f"جمع بيض — {lot.designation}",
            "action_label": "حفظ",
        },
    )


@login_required(login_url=LOGIN_URL)
def recolte_oeufs_edit(request, pk):
    """Edit an existing egg-collection record (blocked on closed lots)."""
    recolte = get_object_or_404(RecolteOeufs.objects.select_related("lot"), pk=pk)
    lot = recolte.lot
    _ensure_branche_access(request, lot)

    if not _assert_lot_ouvert(lot, request):
        return redirect("elevage:lot_detail", pk=lot.pk)

    if request.method == "POST":
        form = RecolteOeufsForm(request.POST, instance=recolte, lot=lot)
        if form.is_valid():
            try:
                form.save()  # signal applies the delta to stock
                messages.success(request, f"تم تحديث جمع البيض بتاريخ {recolte.date}.")
                logger.info(
                    "RecolteOeufs pk=%s updated by '%s'.", recolte.pk, request.user
                )
                return redirect("elevage:lot_detail", pk=lot.pk)
            except Exception as exc:
                logger.exception("Error updating RecolteOeufs pk=%s: %s", pk, exc)
                messages.error(request, f"خطأ أثناء التحديث: {exc}")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        form = RecolteOeufsForm(instance=recolte, lot=lot)

    return render(
        request,
        "elevage/recolte_oeufs_form.html",
        {
            "form": form,
            "lot": lot,
            "recolte": recolte,
            "title": f"تعديل جمع البيض بتاريخ {recolte.date}",
            "action_label": "حفظ التعديلات",
        },
    )


@login_required(login_url=LOGIN_URL)
@require_POST
def recolte_oeufs_delete(request, pk):
    """
    Delete an egg-collection record (POST-only).

    The pre_delete signal reverses the StockProduitFini credit before the
    record is removed.
    """
    recolte = get_object_or_404(RecolteOeufs.objects.select_related("lot"), pk=pk)
    lot = recolte.lot
    _ensure_branche_access(request, lot)

    if not _assert_lot_ouvert(lot, request):
        return redirect("elevage:lot_detail", pk=lot.pk)

    try:
        date_ref = recolte.date
        nombre_ref = recolte.nombre_oeufs
        recolte.delete()  # triggers pre_delete signal → stock reversed
        messages.success(
            request,
            f"تم حذف جمع البيض بتاريخ {date_ref} ({nombre_ref} بيضة). تم تصحيح المخزون.",
        )
        logger.info("RecolteOeufs pk=%s deleted by '%s'.", pk, request.user)
    except Exception as exc:
        logger.exception("Error deleting RecolteOeufs pk=%s: %s", pk, exc)
        messages.error(request, f"خطأ أثناء الحذف: {exc}")

    return redirect("elevage:lot_detail", pk=lot.pk)


@login_required(login_url=LOGIN_URL)
def recolte_oeufs_list(request):
    """
    Cross-lot egg-collection list — supports global ponte reporting.

    Filters:
      ?lot=<pk>           — filter by lot
      ?date_debut, ?date_fin

    Vue par Branche (BR-BRA-01): only the active branche's collections;
    Vue Globale: every branche, combined.
    """
    branche = get_active_branche(request)
    qs = RecolteOeufs.objects.select_related("lot", "pesee").order_by(
        "-date", "-created_at"
    )
    if branche is not None:
        qs = qs.filter(lot__branche=branche)

    lot_pk = request.GET.get("lot", "")
    if lot_pk:
        qs = qs.filter(lot_id=lot_pk)

    date_debut = request.GET.get("date_debut", "")
    date_fin = request.GET.get("date_fin", "")
    if date_debut:
        qs = qs.filter(date__gte=date_debut)
    if date_fin:
        qs = qs.filter(date__lte=date_fin)

    total_oeufs = qs.aggregate(total=Sum("nombre_oeufs"))["total"] or 0

    page = _paginate(qs, request.GET.get("page"))
    lots = LotElevage.objects.order_by("-date_ouverture")
    if branche is not None:
        lots = lots.filter(branche=branche)

    return render(
        request,
        "elevage/recolte_oeufs_list.html",
        {
            "page": page,
            "lot_pk": lot_pk,
            "date_debut": date_debut,
            "date_fin": date_fin,
            "lots": lots,
            "total_oeufs": total_oeufs,
            "active_branche": branche,
            "title": "جمع البيض",
        },
    )


@login_required(login_url=LOGIN_URL)
def lot_suivi_journalier(request, pk):
    """
    Render the day-by-day accumulation table for one lot (paper-ledger
    style): mortalité, aliment consommé (+ cumul), œufs récoltés/retirés
    (+ cumul et solde) — see elevage.utils.get_lot_suivi_journalier.
    """
    lot = branche_object_or_404(request, LotElevage, pk=pk)

    # get_lot_suivi_journalier returns chronological (oldest → newest) rows
    # so the running cumulative columns build up correctly. For display we
    # reverse to most-recent-first — a lot can span hundreds of days, so
    # without this the operator would have to page all the way through
    # ancient history before reaching what happened this week.
    lignes = list(reversed(get_lot_suivi_journalier(lot)))
    page = _paginate(lignes, request.GET.get("page"), per_page=SUIVI_PER_PAGE)

    return render(
        request,
        "elevage/lot_suivi_journalier.html",
        {
            "lot": lot,
            "page": page,
            "lignes": lignes,
            "title": f"جدول التتبع اليومي — {lot.designation}",
        },
    )


@login_required(login_url=LOGIN_URL)
def formule_aliment_list(request):
    """
    List feed recipes (FormuleAliment). Not branche-scoped — a recipe is a
    shared reference/catalogue entry, same as FormuleAliment itself.
    """
    formules = (
        FormuleAliment.objects.select_related("intrant_produit")
        .prefetch_related("lignes__intrant")
        .order_by("nom")
    )

    return render(
        request,
        "elevage/formule_aliment_list.html",
        {"formules": formules, "title": "تركيبات العلف"},
    )


def _safe_next_url(request, default):
    """Only follow an internal ?next= — never an open redirect."""
    next_url = request.GET.get("next") or request.POST.get("next")
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return next_url
    return reverse(default)


@login_required(login_url=LOGIN_URL)
def formule_aliment_create(request):
    """
    Create a feed recipe with its ingredient lines. Reached mainly from the
    "+" shortcut on ProductionAlimentForm's التركيبة field, since that
    dropdown is empty (and unusable) until at least one recipe exists here.
    """
    next_url = _safe_next_url(request, "elevage:formule_aliment_list")

    if request.method == "POST":
        form = FormuleAlimentForm(request.POST)
        if form.is_valid():
            formule = form.save(commit=False)
            formset = FormuleAlimentLigneFormSet(request.POST, instance=formule)
            if formset.is_valid():
                with transaction.atomic():
                    formule.save()
                    formset.instance = formule
                    formset.save()
                messages.success(
                    request,
                    f"تم إنشاء التركيبة « {formule.nom} ». يمكنك اختيارها الآن.",
                )
                logger.info(
                    "FormuleAliment pk=%s created by '%s'.", formule.pk, request.user
                )
                return redirect(next_url)
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
        else:
            formset = FormuleAlimentLigneFormSet(request.POST)
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        form = FormuleAlimentForm()
        formset = FormuleAlimentLigneFormSet()

    return render(
        request,
        "elevage/formule_aliment_form.html",
        {
            "form": form,
            "formset": formset,
            "title": "تركيبة علف جديدة",
            "action_label": "حفظ التركيبة",
            "next_url": next_url,
        },
    )


@login_required(login_url=LOGIN_URL)
def formule_aliment_edit(request, pk):
    formule = get_object_or_404(FormuleAliment, pk=pk)
    next_url = _safe_next_url(request, "elevage:formule_aliment_list")

    if request.method == "POST":
        form = FormuleAlimentForm(request.POST, instance=formule)
        formset = FormuleAlimentLigneFormSet(request.POST, instance=formule)
        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                form.save()
                formset.save()
            messages.success(request, f"تم تحديث التركيبة « {formule.nom} ».")
            logger.info(
                "FormuleAliment pk=%s updated by '%s'.", formule.pk, request.user
            )
            return redirect(next_url)
        messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        form = FormuleAlimentForm(instance=formule)
        formset = FormuleAlimentLigneFormSet(instance=formule)

    return render(
        request,
        "elevage/formule_aliment_form.html",
        {
            "form": form,
            "formset": formset,
            "formule": formule,
            "title": f"تعديل التركيبة — {formule.nom}",
            "action_label": "حفظ التعديلات",
            "next_url": next_url,
        },
    )


@login_required(login_url=LOGIN_URL)
@require_branche_context
def production_aliment_create(request):
    """
    Replenish a finished feed's stock (bare quantity, or via a
    FormuleAliment which also debits ingredient Intrants — see signals.py).
    Not tied to a single lot: feed is milled/bought for the whole branche
    and then consumed per-lot via the existing Consommation flow.
    """
    branche = get_active_branche(request)

    if request.method == "POST":
        form = ProductionAlimentForm(request.POST, branche=branche)
        if form.is_valid():
            try:
                with transaction.atomic():
                    production = form.save(commit=False)
                    if branche:
                        production.branche = branche
                    production.created_by = request.user
                    production.save()  # triggers signal → stock entrée (+ ingrédients)

                    depense = _auto_creer_depense_production_aliment(
                        production, request.user
                    )

                if depense is not None:
                    messages.success(
                        request,
                        f"تم تسجيل تزويد {production.quantite_produite_kg} كغ من "
                        f"«{production.intrant_produit.designation}» وإنشاء "
                        f"مصروف تلقائي بمبلغ {depense.montant} د.ج.",
                    )
                elif production.formule_id:
                    messages.success(
                        request,
                        f"تم تسجيل تزويد {production.quantite_produite_kg} كغ من "
                        f"«{production.intrant_produit.designation}» عبر تركيبة "
                        f"«{production.formule.nom}». لا تنسَ دفع أجرة التصنيع "
                        f"من هنا عند الحاجة.",
                    )
                else:
                    messages.success(
                        request,
                        f"تم تسجيل تزويد {production.quantite_produite_kg} كغ من "
                        f"«{production.intrant_produit.designation}».",
                    )
                logger.info(
                    "ProductionAliment pk=%s created (intrant pk=%s, qte=%s) by '%s'"
                    " — depense_auto=%s.",
                    production.pk,
                    production.intrant_produit_id,
                    production.quantite_produite_kg,
                    request.user,
                    depense.pk if depense is not None else None,
                )
                # Land on the production listing (not the dashboard) — this
                # is where the record now lives, and where a formule-based
                # entry gets its labor-cost payment batched (BR-request).
                return redirect("elevage:production_aliment_list")

            except Exception as exc:
                logger.exception("Error creating ProductionAliment: %s", exc)
                messages.error(request, f"خطأ أثناء التسجيل: {exc}")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        form = ProductionAlimentForm(branche=branche)

    return render(
        request,
        "elevage/production_aliment_form.html",
        {
            "form": form,
            "title": "تزويد/تصنيع علف",
            "action_label": "حفظ",
            "formule_intrant_map": json.dumps(
                dict(
                    FormuleAliment.objects.filter(actif=True).values_list(
                        "pk", "intrant_produit_id"
                    )
                )
            ),
        },
    )


@login_required(login_url=LOGIN_URL)
def production_aliment_detail(request, pk):
    """
    Batch costing detail (BR-request): full picture of ONE ProductionAliment
    batch — how much of it is left, its façon (mill labor) cost envelope
    and how much of that has been recognized so far, and the complete
    consumption trail (which lot drew how much, when, and at what façon
    cost) via ConsommationAlimentAllocation.
    """
    production = get_object_or_404(
        ProductionAliment.objects.select_related(
            "intrant_produit", "formule", "branche", "depense_paiement"
        ),
        pk=pk,
    )
    # ProductionAliment carries its own `branche` FK directly (it isn't
    # scoped to a single lot), so it can be passed straight to the same
    # helper used for LotElevage-rooted records — branche_matches() only
    # needs an object with a `.branche` attribute.
    _ensure_branche_access(request, production)

    allocations = production.allocations_consommees.select_related(
        "consommation", "consommation__lot"
    ).order_by("-consommation__date", "-created_at")

    ingredients = (
        production.formule.lignes.select_related("intrant").all()
        if production.formule_id
        else []
    )

    return render(
        request,
        "elevage/production_aliment_detail.html",
        {
            "production": production,
            "allocations": allocations,
            "ingredients": ingredients,
            "title": f"تفاصيل الدفعة — {production.intrant_produit.designation}",
        },
    )


@login_required(login_url=LOGIN_URL)
def production_aliment_list(request):
    """
    Cross list of feed replenishment/production records (direct entry or
    via formule). This is where formule-based records — never auto-expensed
    at creation (see _auto_creer_depense_production_aliment) — get selected
    and batched into a labor-cost payment for the feed-mill worker; see
    production_aliment_paiement_create.

    Filters:
      ?intrant=<pk>       — finished feed
      ?formule=<pk>
      ?paiement=impaye|paye
      ?date_debut, ?date_fin
    """
    from intrants.models import Intrant

    branche = get_active_branche(request)

    qs = ProductionAliment.objects.select_related(
        "intrant_produit", "formule", "branche", "depense_paiement"
    ).order_by("-date", "-created_at")
    if branche is not None:
        qs = qs.filter(branche=branche)

    intrant_pk = request.GET.get("intrant", "")
    if intrant_pk:
        qs = qs.filter(intrant_produit_id=intrant_pk)

    formule_pk = request.GET.get("formule", "")
    if formule_pk:
        qs = qs.filter(formule_id=formule_pk)

    date_debut = request.GET.get("date_debut", "")
    date_fin = request.GET.get("date_fin", "")
    if date_debut:
        qs = qs.filter(date__gte=date_debut)
    if date_fin:
        qs = qs.filter(date__lte=date_fin)

    statut_paiement = request.GET.get("paiement", "")
    if statut_paiement == "impaye":
        qs = qs.filter(formule__isnull=False, depense_paiement__isnull=True)
    elif statut_paiement == "paye":
        qs = qs.filter(depense_paiement__isnull=False)

    # Batch costing (BR-request): filter by whether a batch's tracked stock
    # is still open (quantite_restante_kg > 0) or fully consumed — lets an
    # operator find, e.g., every batch still being drawn from.
    statut_lot = request.GET.get("lot_stock", "")
    if statut_lot == "ouvert":
        qs = qs.filter(quantite_restante_kg__gt=0)
    elif statut_lot == "epuise":
        qs = qs.filter(quantite_restante_kg__lte=0)

    page = _paginate(qs, request.GET.get("page"))

    en_attente_qs = ProductionAliment.objects.filter(
        formule__isnull=False, depense_paiement__isnull=True
    )
    if branche is not None:
        en_attente_qs = en_attente_qs.filter(branche=branche)
    total_kg_impaye = (
        en_attente_qs.aggregate(total=Sum("quantite_produite_kg"))["total"] or 0
    )

    intrants = Intrant.objects.filter(categorie__code="ALIMENT", actif=True).order_by(
        "designation"
    )
    formules = FormuleAliment.objects.order_by("nom")

    return render(
        request,
        "elevage/production_aliment_list.html",
        {
            "page": page,
            "intrant_pk": intrant_pk,
            "formule_pk": formule_pk,
            "statut_paiement": statut_paiement,
            "statut_lot": statut_lot,
            "date_debut": date_debut,
            "date_fin": date_fin,
            "intrants": intrants,
            "formules": formules,
            "nb_impaye": en_attente_qs.count(),
            "total_kg_impaye": total_kg_impaye,
            "active_branche": branche,
            "title": "عمليات تصنيع/تزويد العلف",
        },
    )


@login_required(login_url=LOGIN_URL)
@require_branche_context
def production_aliment_paiement_create(request):
    """
    Consolidate one or more unpaid formule-based ProductionAliment records
    into ONE Depense paying the feed-mill worker's labor: a single
    prix_unitaire (د.ج/كغ) × Σ quantite_produite_kg of everything selected.

    GET  ?ids=<pk>&ids=<pk>…   — review screen (from production_aliment_list
                                  checkboxes), prix_unitaire not decided yet.
    POST production_ids=<pk>…  — final submission: creates the Depense and
                                  marks every selected production paid via
                                  depense_paiement (so it drops out of the
                                  next batch — BR: never pay the same
                                  production twice).
    """
    from depenses.models import CategorieDepense, Depense

    branche = get_active_branche(request)

    base_qs = ProductionAliment.objects.filter(
        formule__isnull=False, depense_paiement__isnull=True
    ).select_related("intrant_produit", "formule")
    if branche is not None:
        base_qs = base_qs.filter(branche=branche)

    if request.method == "POST":
        ids = request.POST.getlist("production_ids")
        productions = list(base_qs.filter(pk__in=ids))
        if not productions:
            messages.error(
                request,
                "لم يتم العثور على عمليات تصنيع صالحة للدفع ضمن الاختيار "
                "(ربما تم دفعها من قبل). يرجى إعادة الاختيار.",
            )
            return redirect("elevage:production_aliment_list")

        branches_selectionnees = {p.branche_id for p in productions}
        if len(branches_selectionnees) > 1:
            messages.error(
                request,
                "لا يمكن تجميع عمليات من فروع مختلفة ضمن مصروف دفع واحد. "
                "يرجى اختيار عمليات من نفس الفرع.",
            )
            return redirect("elevage:production_aliment_list")

        form = ProductionAlimentPaiementForm(request.POST)
        if form.is_valid():
            total_kg = sum((p.quantite_produite_kg for p in productions), Decimal("0"))
            prix_unitaire = form.cleaned_data["prix_unitaire"]
            montant = (total_kg * prix_unitaire).quantize(Decimal("0.01"))

            categorie, _ = CategorieDepense.objects.get_or_create(
                code="MAIN_OEUVRE_ALIMENT",
                defaults={
                    "libelle": "يد عاملة تصنيع العلف",
                    "description": (
                        "أجرة عامل مصنع الأعلاف عن كميات مصنّعة عبر تركيبة."
                    ),
                    "actif": True,
                },
            )
            noms = "، ".join(
                sorted({p.intrant_produit.designation for p in productions})
            )

            try:
                with transaction.atomic():
                    depense = Depense.objects.create(
                        date=form.cleaned_data["date"],
                        branche=branche or productions[0].branche,
                        categorie=categorie,
                        description=(
                            f"دفع تصنيع علف — {noms} "
                            f"({total_kg} كغ عبر {len(productions)} عملية)"
                        ),
                        montant=montant,
                        mode_paiement=form.cleaned_data["mode_paiement"],
                        notes=form.cleaned_data.get("notes", ""),
                        enregistre_par=request.user,
                    )
                    # Batch costing (BR-request): stamp the now-known façon
                    # rate on every batch in this payment and retroactively
                    # recognize the cost for whatever portion each batch has
                    # already been consumed (see
                    # _recognize_facon_cost_for_batch).
                    for p in productions:
                        _recognize_facon_cost_for_batch(p, prix_unitaire)

                    ProductionAliment.objects.filter(
                        pk__in=[p.pk for p in productions]
                    ).update(depense_paiement=depense)

                messages.success(
                    request,
                    f"تم إنشاء مصروف بمبلغ {montant} د.ج لدفع {total_kg} كغ "
                    f"عبر {len(productions)} عملية تصنيع.",
                )
                logger.info(
                    "Depense pk=%s created for %s ProductionAliment payment "
                    "(total_kg=%s, prix_unitaire=%s) by '%s'.",
                    depense.pk,
                    len(productions),
                    total_kg,
                    prix_unitaire,
                    request.user,
                )
                # Land the user directly on the expense's edit form (not
                # just the list) so the user can immediately attach the
                # supporting document ("pièce jointe") and fill in the
                # building/location info on the freshly created Depense.
                return redirect("depenses:depense_edit", pk=depense.pk)
            except Exception as exc:
                logger.exception("Error creating paiement production: %s", exc)
                messages.error(request, f"خطأ أثناء التسجيل: {exc}")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        ids = request.GET.getlist("ids")
        productions = list(base_qs.filter(pk__in=ids)) if ids else []
        if not productions:
            messages.error(
                request, "يرجى اختيار عملية تصنيع واحدة على الأقل قبل المتابعة."
            )
            return redirect("elevage:production_aliment_list")
        form = ProductionAlimentPaiementForm()

    total_kg = sum((p.quantite_produite_kg for p in productions), Decimal("0"))

    return render(
        request,
        "elevage/production_aliment_paiement_form.html",
        {
            "form": form,
            "productions": productions,
            "total_kg": total_kg,
            "title": "دفع تصنيع العلف",
        },
    )


@login_required(login_url=LOGIN_URL)
def retrait_oeufs_create(request, lot_pk=None):
    """
    Withdraw eggs from stock outside the formal BLClient sales flow: direct
    truck sale, gift, or loss/breakage (RetraitOeufs — debits the same egg
    StockProduitFini that RecolteOeufs credits, see signals.py).

    `lot_pk` is optional: when given, the withdrawal is attributed to that
    lot's daily table; otherwise it's just scoped to the active branche.
    """
    lot = None
    if lot_pk is not None:
        lot = branche_object_or_404(request, LotElevage, pk=lot_pk)
    branche = lot.branche if lot else get_active_branche(request)

    if request.method == "POST":
        form = RetraitOeufsForm(request.POST, lot=lot, branche=branche)
        if form.is_valid():
            try:
                with transaction.atomic():
                    retrait = form.save(commit=False)
                    if lot:
                        retrait.lot = lot
                        retrait.branche = lot.branche
                    elif branche:
                        retrait.branche = branche
                    retrait.created_by = request.user

                    bl = None
                    if (
                        retrait.client_id
                        and retrait.motif == RetraitOeufs.MOTIF_CLIENT_CAMION
                    ):
                        from clients.models import BLClient, BLClientLigne
                        from elevage.signals import _get_produit_oeufs
                        import uuid

                        produit = _get_produit_oeufs()
                        if not produit:
                            raise RuntimeError(
                                "لا يوجد منتج نهائي «بيض» نشط في الكتالوج — "
                                "تعذّر إنشاء وصل التسليم. راجع كتالوج المنتجات النهائية."
                            )

                        # NOTE: reference scheme is a placeholder (date + short
                        # unique suffix) since no shared BL numbering helper was
                        # available here — swap for the farm's real sequence
                        # generator (clients app) if one exists.
                        reference = (
                            f"BLC-OEUFS-{retrait.branche.code}-"
                            f"{retrait.date:%Y%m%d}-{uuid.uuid4().hex[:6].upper()}"
                        )
                        bl = BLClient.objects.create(
                            reference=reference,
                            branche=retrait.branche,
                            client=retrait.client,
                            date_bl=retrait.date,
                            statut=BLClient.STATUT_LIVRE,
                            notes=(
                                f"مُنشأ تلقائياً من سحب بيض "
                                f"({retrait.get_motif_display()})."
                            ),
                            created_by=request.user,
                        )
                        BLClientLigne.objects.create(
                            bl=bl,
                            produit_fini=produit,
                            quantite=Decimal(retrait.quantite_oeufs),
                            prix_unitaire=produit.prix_vente_defaut,
                        )
                        # bl_genere is set BEFORE retrait.save() below so that
                        # signals.retrait_oeufs_post_save sees it already and
                        # skips its own stock debit — BLClientLigne's signal
                        # (triggered above) is the single source of truth for
                        # the stock movement in this path.
                        retrait.bl_genere = bl

                    # ── Optional FIFO attribution split (soft/advisory) ──
                    # When the entered quantity exceeds the selected lot's
                    # own informational balance, the form JS may offer a
                    # cascading FIFO split across other open lots in the
                    # branche (see utils.get_oeufs_fifo_allocation). This is
                    # attribution-only and only ever safe when there is no
                    # BLClient being generated for this withdrawal (a
                    # client_camion sale is one physical transaction — see
                    # RetraitOeufs.bl_genere docstring — so it always stays
                    # a single row to keep the stock-signal single-sourced).
                    repartition_raw = request.POST.get("repartition_lots", "").strip()
                    lots_repartis = None
                    if repartition_raw and bl is None:
                        try:
                            repartition = json.loads(repartition_raw)
                        except (TypeError, ValueError):
                            repartition = None
                        if isinstance(repartition, list) and repartition:
                            try:
                                total_reparti = sum(
                                    int(item["quantite"]) for item in repartition
                                )
                                valide = (
                                    total_reparti == retrait.quantite_oeufs
                                    and all(
                                        int(item["quantite"]) > 0
                                        for item in repartition
                                    )
                                )
                            except (KeyError, TypeError, ValueError):
                                valide = False
                            if valide:
                                lots_repartis = []
                                for item in repartition:
                                    lot_item = branche_object_or_404(
                                        request, LotElevage, pk=item["lot_id"]
                                    )
                                    r = RetraitOeufs.objects.create(
                                        branche=retrait.branche,
                                        lot=lot_item,
                                        date=retrait.date,
                                        quantite_oeufs=int(item["quantite"]),
                                        motif=retrait.motif,
                                        destinataire=retrait.destinataire,
                                        notes=retrait.notes,
                                        created_by=request.user,
                                    )
                                    lots_repartis.append(r)
                            else:
                                messages.warning(
                                    request,
                                    "توزيع الكميات على الدفعات غير متطابق مع "
                                    "العدد الإجمالي — تم تجاهله وتسجيل السحب "
                                    "على الدفعة المختارة فقط.",
                                )

                    if lots_repartis is None:
                        retrait.save()  # triggers signal → stock sortie + mouvement
                        # (skipped automatically above when bl_genere is set)

                if bl:
                    messages.success(
                        request,
                        f"تم تسجيل سحب {retrait.quantite_oeufs} بيضة "
                        f"وإنشاء وصل تسليم {bl.reference} للعميل {retrait.client.nom}.",
                    )
                elif lots_repartis:
                    detail = "، ".join(
                        f"{r.lot.designation} ({r.quantite_oeufs})"
                        for r in lots_repartis
                    )
                    messages.success(
                        request,
                        f"تم تسجيل سحب {retrait.quantite_oeufs} بيضة، موزعة "
                        f"حسب الأقدمية (FIFO) على: {detail}.",
                    )
                else:
                    messages.success(
                        request,
                        f"تم تسجيل سحب {retrait.quantite_oeufs} بيضة "
                        f"({retrait.get_motif_display()}).",
                    )
                logger.info(
                    "RetraitOeufs created (lot pk=%s, nombre=%s, bl=%s, fifo_split=%s) "
                    "by '%s'.",
                    lot.pk if lot else None,
                    retrait.quantite_oeufs,
                    bl.reference if bl else None,
                    [r.pk for r in lots_repartis] if lots_repartis else None,
                    request.user,
                )
                return (
                    redirect("elevage:lot_detail", pk=lot.pk)
                    if lot
                    else redirect("elevage:recolte_oeufs_list")
                )

            except Exception as exc:
                logger.exception("Error creating RetraitOeufs: %s", exc)
                messages.error(request, f"خطأ أثناء التسجيل: {exc}")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        import datetime

        form = RetraitOeufsForm(
            lot=lot, branche=branche, initial={"date": datetime.date.today()}
        )

    return render(
        request,
        "elevage/retrait_oeufs_form.html",
        {
            "form": form,
            "lot": lot,
            "title": f"سحب بيض — {lot.designation}" if lot else "سحب بيض",
            "action_label": "حفظ",
        },
    )


# ===========================================================================
# Dashboard — Elevage overview
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def elevage_dashboard(request):
    """
    Elevage module dashboard:
      - Open lots with key real-time indicators
      - Recent mortality events (last 7 days)
      - Recent consumption events (last 7 days)
      - Lots with abnormal mortality flags

    v1.4 (§3.5.5): Vue par Branche shows the active branche's data only
    (BR-BRA-01/02); Vue Globale (admin/comptable) aggregates across every
    branche, with no per-branche filter applied.
    """
    import datetime

    branche = get_active_branche(request)
    vue_globale = branche is None

    lots_ouverts = (
        LotElevage.objects.filter(statut=LotElevage.STATUT_OUVERT)
        .select_related("batiment", "fournisseur_poussins")
        .order_by("-date_ouverture")
    )
    if branche is not None:
        lots_ouverts = lots_ouverts.filter(branche=branche)

    today = datetime.date.today()
    sept_jours = today - datetime.timedelta(days=7)

    mortalites_recentes_qs = Mortalite.objects.filter(
        date__gte=sept_jours
    ).select_related("lot")
    if branche is not None:
        mortalites_recentes_qs = mortalites_recentes_qs.filter(lot__branche=branche)
    mortalites_recentes = mortalites_recentes_qs.order_by("-date")[:20]

    consommations_recentes_qs = Consommation.objects.filter(
        date__gte=sept_jours
    ).select_related("lot", "intrant")
    if branche is not None:
        consommations_recentes_qs = consommations_recentes_qs.filter(
            lot__branche=branche
        )
    consommations_recentes = consommations_recentes_qs.order_by("-date")[:20]

    # --- Eggs — collection/withdrawal are the farm's main product line ---
    from django.db.models import Sum
    from elevage.signals import _get_produit_oeufs
    from stock.models import StockProduitFini

    produit_oeufs = _get_produit_oeufs()
    stock_oeufs_qs = (
        StockProduitFini.objects.filter(produit_fini=produit_oeufs)
        if produit_oeufs
        else StockProduitFini.objects.none()
    )
    if branche is not None:
        stock_oeufs_qs = stock_oeufs_qs.filter(branche=branche)
    stock_oeufs_total = int(
        stock_oeufs_qs.aggregate(total=Sum("quantite"))["total"] or 0
    )

    recoltes_oeufs_qs = RecolteOeufs.objects.filter(
        date__gte=sept_jours
    ).select_related("lot")
    if branche is not None:
        recoltes_oeufs_qs = recoltes_oeufs_qs.filter(lot__branche=branche)
    oeufs_collectes_semaine = (
        recoltes_oeufs_qs.aggregate(total=Sum("nombre_oeufs"))["total"] or 0
    )
    recoltes_oeufs_recentes = recoltes_oeufs_qs.order_by("-date")[:20]

    retraits_oeufs_qs = RetraitOeufs.objects.filter(
        date__gte=sept_jours
    ).select_related("lot", "client")
    if branche is not None:
        retraits_oeufs_qs = retraits_oeufs_qs.filter(branche=branche)
    oeufs_retires_semaine = (
        retraits_oeufs_qs.aggregate(total=Sum("quantite_oeufs"))["total"] or 0
    )
    retraits_oeufs_recentes = retraits_oeufs_qs.order_by("-date")[:20]

    # Lots with abnormal mortality
    lots_alerte_mortalite = [
        lot for lot in lots_ouverts if verifier_mortalite_anormale(lot)
    ]

    # Lots in Poussinière past the configured transfer-age threshold
    lots_alerte_transfert = lots_a_transferer(branche=branche)

    # Summary stats
    total_effectif_vivant = sum(lot.effectif_vivant for lot in lots_ouverts)
    nb_lots_ouverts = lots_ouverts.count()
    nb_lots_fermes_qs = LotElevage.objects.filter(statut=LotElevage.STATUT_FERME)
    if branche is not None:
        nb_lots_fermes_qs = nb_lots_fermes_qs.filter(branche=branche)
    nb_lots_fermes = nb_lots_fermes_qs.count()

    # --- Cost-per-chick roll-up (BR-request) -----------------------------
    # The farm's real per-chick raising cost is médicaments (material +
    # vet/team labor) plus aliment (material + mill-worker labor), spread
    # over every chick ever placed (open AND closed lots — a chick's cost
    # doesn't disappear once its lot closes). Mirrors the same
    # material-vs-labor split already surfaced on the payment form and
    # lot_detail page:
    #   cout_medicaments / cout_aliments        → Σ quantite × PMP (stock)
    #   MAIN_OEUVRE_MEDICAMENT / MAIN_OEUVRE_ALIMENT Depense → labor fee
    # Feed-mill labor (MAIN_OEUVRE_ALIMENT) is NEVER attributable to a
    # single lot — ProductionAliment feeds the shared branche stock, not
    # one lot — so this roll-up only makes sense at this branch-wide (or
    # global) scope, not per-lot.
    lots_toutes_qs = LotElevage.objects.all()
    if branche is not None:
        lots_toutes_qs = lots_toutes_qs.filter(branche=branche)

    total_cout_medicaments = sum(
        (Decimal(str(lot.cout_medicaments)) for lot in lots_toutes_qs), Decimal("0")
    )
    total_cout_aliments = sum(
        (Decimal(str(lot.cout_aliments)) for lot in lots_toutes_qs), Decimal("0")
    )
    total_poussins = (
        lots_toutes_qs.aggregate(total=Sum("nombre_poussins_initial"))["total"] or 0
    )

    from depenses.models import Depense

    depenses_main_oeuvre_qs = Depense.objects.filter(
        categorie__code__in=["MAIN_OEUVRE_MEDICAMENT", "MAIN_OEUVRE_ALIMENT"]
    )
    if branche is not None:
        depenses_main_oeuvre_qs = depenses_main_oeuvre_qs.filter(branche=branche)
    main_oeuvre_par_categorie = {
        row["categorie__code"]: row["total"]
        for row in depenses_main_oeuvre_qs.values("categorie__code").annotate(
            total=Sum("montant")
        )
    }
    total_main_oeuvre_medicament = main_oeuvre_par_categorie.get(
        "MAIN_OEUVRE_MEDICAMENT"
    ) or Decimal("0")
    total_main_oeuvre_aliment = main_oeuvre_par_categorie.get(
        "MAIN_OEUVRE_ALIMENT"
    ) or Decimal("0")

    total_cout_traitement = total_cout_medicaments + total_main_oeuvre_medicament
    total_cout_aliment_complet = total_cout_aliments + total_main_oeuvre_aliment
    total_cout_elevage = total_cout_traitement + total_cout_aliment_complet
    cout_par_poussin = (
        (total_cout_elevage / total_poussins).quantize(Decimal("0.01"))
        if total_poussins
        else None
    )

    return render(
        request,
        "elevage/dashboard.html",
        {
            "lots_ouverts": lots_ouverts,
            "mortalites_recentes": mortalites_recentes,
            "consommations_recentes": consommations_recentes,
            "lots_alerte_mortalite": lots_alerte_mortalite,
            "lots_alerte_transfert": lots_alerte_transfert,
            "total_effectif_vivant": total_effectif_vivant,
            "nb_lots_ouverts": nb_lots_ouverts,
            "nb_lots_fermes": nb_lots_fermes,
            "active_branche": branche,
            "vue_globale": vue_globale,
            "stock_oeufs_total": stock_oeufs_total,
            "stock_oeufs_total_plateaux": stock_oeufs_total
            // RecolteOeufs.PLATEAU_SIZE,
            "stock_oeufs_total_hors_plateau": stock_oeufs_total
            % RecolteOeufs.PLATEAU_SIZE,
            "oeufs_collectes_semaine": oeufs_collectes_semaine,
            "oeufs_retires_semaine": oeufs_retires_semaine,
            "recoltes_oeufs_recentes": recoltes_oeufs_recentes,
            "retraits_oeufs_recentes": retraits_oeufs_recentes,
            "total_cout_medicaments": total_cout_medicaments,
            "total_main_oeuvre_medicament": total_main_oeuvre_medicament,
            "total_cout_traitement": total_cout_traitement,
            "total_cout_aliments": total_cout_aliments,
            "total_main_oeuvre_aliment": total_main_oeuvre_aliment,
            "total_cout_aliment_complet": total_cout_aliment_complet,
            "total_cout_elevage": total_cout_elevage,
            "total_poussins": total_poussins,
            "cout_par_poussin": cout_par_poussin,
            "title": "لوحة تحكم — التربية",
        },
    )


# ===========================================================================
# AJAX helpers
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def lot_kpi_json(request, pk):
    """
    Return computed KPIs for one lot as JSON.
    Called by the dashboard auto-refresh and the lot detail page.

    Returns:
        {
          "effectif_vivant": int,
          "total_mortalite": int,
          "taux_mortalite": float,
          "duree_elevage": int,
          "consommation_totale_aliment_kg": float,
          "cout_total_intrants": float,
          "statut": str,
        }
    """
    lot = branche_object_or_404(request, LotElevage, pk=pk)

    data = {
        "effectif_vivant": lot.effectif_vivant,
        "total_mortalite": lot.total_mortalite,
        "taux_mortalite": float(lot.taux_mortalite),
        "duree_elevage": lot.duree_elevage,
        "consommation_totale_aliment_kg": float(lot.consommation_totale_aliment),
        "cout_total_intrants": float(lot.cout_total_intrants),
        "statut": lot.statut,
    }
    return JsonResponse(data)


@login_required(login_url=LOGIN_URL)
def retrait_oeufs_verifier_json(request):
    """
    Soft/advisory check for the withdrawal form (AJAX, GET), called on every
    change of lot / date / quantite_oeufs before submit. Never blocks
    anything — see utils.get_oeufs_stock_lot / get_oeufs_fifo_allocation.

    Query params:
        lot       — LotElevage pk (optional; omit for a lot-less withdrawal)
        date      — ISO date of the withdrawal (defaults to today)
        quantite  — entered quantite_oeufs (defaults to 0)

    Returns:
        {
          "lot_stock": int | null,       # lot's own informational balance
                                          # as of `date`, before this withdrawal
          "would_be_negative": bool,     # true if this withdrawal would take
                                          # that lot's own balance below 0
          "fifo": {                      # only present when would_be_negative
            "allocations": [{"lot_id", "designation", "quantite",
                              "stock_disponible"}, ...],
            "quantite_allouee": int,
            "shortfall": int
          } | null
        }
    """
    import datetime

    lot_pk = request.GET.get("lot") or None
    try:
        quantite = int(request.GET.get("quantite", "0"))
    except (TypeError, ValueError):
        quantite = 0
    try:
        date_retrait = (
            datetime.date.fromisoformat(request.GET["date"])
            if request.GET.get("date")
            else datetime.date.today()
        )
    except ValueError:
        date_retrait = datetime.date.today()

    lot = None
    if lot_pk:
        try:
            lot = branche_object_or_404(request, LotElevage, pk=int(lot_pk))
        except (Http404, ValueError, TypeError):
            lot = None
    branche = lot.branche if lot else get_active_branche(request)

    data = {"lot_stock": None, "would_be_negative": False, "fifo": None}

    if lot is not None:
        stock_actuel = get_oeufs_stock_lot(lot, as_of=date_retrait)
        data["lot_stock"] = stock_actuel
        data["would_be_negative"] = (stock_actuel - quantite) < 0
        if data["would_be_negative"] and branche is not None and quantite > 0:
            data["fifo"] = get_oeufs_fifo_allocation(
                branche, quantite, date_retrait, lot_prioritaire=lot
            )

    return JsonResponse(data)


@login_required(login_url=LOGIN_URL)
def bl_fournisseur_poussins_json(request):
    """
    Return the eligible BL Fournisseur (poussins) options for one supplier,
    as JSON. Called by the lot_form JS when the chick-catalogue dropdown (or
    the fournisseur field directly) selects/changes a fournisseur, so the
    bl_fournisseur_poussins <select> can be rebuilt to only offer that
    supplier's delivery notes — mirrors LotElevageForm's own
    statut/branche filtering (BR-LOT-01: RECU or FACTURE only).

    Query params:
        fournisseur — Fournisseur pk (required; empty result if missing)
        lot         — LotElevage pk (optional — scopes to that lot's own
                      branche, for the edit form; omit on the create form,
                      where the active branche from the session is used)

    Returns:
        {"results": [{"id": int, "label": str}, ...]}
    """
    from achats.models import BLFournisseur

    fournisseur_pk = request.GET.get("fournisseur")
    if not fournisseur_pk:
        return JsonResponse({"results": []})

    lot_pk = request.GET.get("lot")
    if lot_pk:
        lot = branche_object_or_404(request, LotElevage, pk=lot_pk)
        branche = lot.branche
    else:
        branche = get_active_branche(request)

    qs = BLFournisseur.objects.filter(
        fournisseur_id=fournisseur_pk,
        statut__in=[BLFournisseur.STATUT_RECU, BLFournisseur.STATUT_FACTURE],
    )
    if branche is not None:
        qs = qs.filter(branche=branche)

    results = [
        {"id": bl.pk, "label": f"{bl.reference} — {bl.date_bl:%d/%m/%Y}"}
        for bl in qs.order_by("-date_bl")
    ]
    return JsonResponse({"results": results})


@login_required(login_url=LOGIN_URL)
@require_POST
def prix_marche_quick_create_json(request):
    """
    Quick-add a PrixMarche entry from the pesée form's «+ سعر سوق جديد» modal
    (AJAX, POST), so the user never has to leave the weighing form to record
    today's market quote before picking it.

    Body params (form-encoded or JSON):
        date            — ISO date (required)
        prix_marche     — د.ج per plateau (required)
        poids_reference_kg — optional, defaults to the model default (2 kg)
        source          — optional
        notes           — optional

    Returns 201 with {"id", "label", "prix_marche", "poids_reference_kg",
    "date"} on success, or 400 with {"errors": {...}} on validation failure.
    Always targets the farm's single egg ProduitFini (elevage.signals._get_produit_oeufs).
    """
    from clients.models import PrixMarche
    from elevage.signals import _get_produit_oeufs

    produit_oeufs = _get_produit_oeufs()
    if not produit_oeufs:
        return JsonResponse(
            {
                "errors": {
                    "__all__": ["لا يوجد منتج نهائي نشط من نوع «بيض» في الكتالوج."]
                }
            },
            status=400,
        )

    payload = request.POST or json.loads(request.body or "{}")
    data = {
        "produit_fini": produit_oeufs.pk,
        "date": payload.get("date"),
        "prix_marche": payload.get("prix_marche"),
        "poids_reference_kg": payload.get("poids_reference_kg") or "2.000",
        "source": payload.get("source", ""),
        "notes": payload.get("notes", ""),
    }

    instance = PrixMarche(
        produit_fini_id=data["produit_fini"],
        date=data["date"] or None,
        prix_marche=data["prix_marche"] or None,
        poids_reference_kg=data["poids_reference_kg"],
        source=data["source"],
        notes=data["notes"],
        created_by=request.user,
    )
    try:
        instance.full_clean()
        instance.save()
    except Exception as exc:
        from django.core.exceptions import ValidationError as DjangoValidationError

        if isinstance(exc, DjangoValidationError):
            errors = {
                field: [str(e) for e in errs]
                for field, errs in exc.message_dict.items()
            }
        else:
            errors = {"__all__": [str(exc)]}
        return JsonResponse({"errors": errors}, status=400)

    logger.info(
        "PrixMarche pk=%s created via quick-add modal by '%s'.",
        instance.pk,
        request.user,
    )
    return JsonResponse(
        {
            "id": instance.pk,
            "label": f"{instance.date:%d/%m/%Y} — {instance.prix_marche} د.ج",
            "prix_marche": str(instance.prix_marche),
            "poids_reference_kg": str(instance.poids_reference_kg),
            "date": instance.date.isoformat(),
        },
        status=201,
    )
