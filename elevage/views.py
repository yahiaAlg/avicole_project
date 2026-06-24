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

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db import transaction
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from elevage.forms import (
    ConsommationForm,
    LotElevageForm,
    LotFermetureForm,
    MortaliteForm,
    PeseeEchantillonForm,
    RecolteOeufsForm,
    TransfertLotForm,
)
from elevage.models import (
    Consommation,
    LotElevage,
    Mortalite,
    PeseeEchantillon,
    RecolteOeufs,
    TransfertLot,
)
from elevage.utils import (
    get_lot_summary,
    lots_a_transferer,
    verifier_mortalite_anormale,
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


# ===========================================================================
# LotElevage — List
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def lot_list(request):
    """
    List all lots d'élevage.

    Filters:
      ?statut=ouvert|ferme  — filter by lot status
      ?batiment=<pk>        — filter by building
      ?q=<search>           — search by designation or souche
    """
    from intrants.models import Batiment

    qs = LotElevage.objects.select_related(
        "batiment", "fournisseur_poussins", "created_by"
    ).order_by("-date_ouverture")

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
    batiments = Batiment.objects.filter(actif=True).order_by("nom")

    # Count summaries for dashboard context
    nb_ouverts = LotElevage.objects.filter(statut=LotElevage.STATUT_OUVERT).count()
    nb_fermes = LotElevage.objects.filter(statut=LotElevage.STATUT_FERME).count()

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
            "title": "دفعات التربية",
        },
    )


# ===========================================================================
# LotElevage — Create
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def lot_create(request):
    """
    Open a new lot d'élevage.

    BR-LOT-01: designation, date_ouverture, nombre_poussins_initial,
    fournisseur_poussins, and batiment are required.
    The BL fournisseur (poussins) is optional but recommended.
    """
    if request.method == "POST":
        form = LotElevageForm(request.POST)
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
        form = LotElevageForm()

    return render(
        request,
        "elevage/lot_form.html",
        {
            "form": form,
            "title": "فتح دفعة جديدة",
            "action_label": "فتح الدفعة",
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
    lot = get_object_or_404(
        LotElevage.objects.select_related(
            "batiment", "fournisseur_poussins", "bl_fournisseur_poussins", "created_by"
        ),
        pk=pk,
    )

    summary = get_lot_summary(lot)
    mortalite_anormale = verifier_mortalite_anormale(lot)

    # Paginate sub-lists for large lots
    mortalites_page = _paginate(
        summary["mortalites"], request.GET.get("page_mort"), per_page=10
    )
    consommations_page = _paginate(
        summary["consommations"], request.GET.get("page_conso"), per_page=10
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

    return render(
        request,
        "elevage/lot_detail.html",
        {
            "lot": lot,
            "summary": summary,
            "mortalite_anormale": mortalite_anormale,
            "mortalites_page": mortalites_page,
            "consommations_page": consommations_page,
            "productions": summary["productions"],
            "depenses": summary["depenses"],
            "suggest_fermeture": suggest_fermeture,
            "transferts": transferts,
            "pesees_page": pesees_page,
            "recoltes_oeufs_page": recoltes_oeufs_page,
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
    lot = get_object_or_404(LotElevage, pk=pk)

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
               closure is allowed.
    BR-LOT-05: once closed, no further entries are accepted.

    GET  — renders the closure confirmation form.
    POST — validates, then calls lot.fermer(date_fermeture).
    """
    lot = get_object_or_404(LotElevage, pk=pk)

    if lot.statut == LotElevage.STATUT_FERME:
        messages.warning(request, f"الدفعة « {lot.designation} » مغلقة مسبقًا.")
        return redirect("elevage:lot_detail", pk=lot.pk)

    # BR-LOT-04: at least one validated production record required.
    from production.models import ProductionRecord

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
    lot = get_object_or_404(LotElevage, pk=lot_pk)

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
# Consommation — Create
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def consommation_create(request, lot_pk):
    """
    Record an input consumption event (feed, medicine) attributed to a lot.

    BR-LOT-03: only permitted on open lots.
    BR-INT-03: quantity cannot exceed available stock — enforced in
               ConsommationForm.clean() and double-checked here atomically.

    On success the post_save signal (elevage/signals.py) automatically:
      - Decreases StockIntrant.quantite
      - Creates a StockMouvement (sortie / consommation)
    """
    lot = get_object_or_404(LotElevage, pk=lot_pk)

    if not _assert_lot_ouvert(lot, request):
        return redirect("elevage:lot_detail", pk=lot.pk)

    if request.method == "POST":
        form = ConsommationForm(request.POST, lot=lot)
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
                            intrant=intrant
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
                                "elevage/consommation_form.html",
                                {
                                    "form": form,
                                    "lot": lot,
                                    "title": f"تسجيل استهلاك — {lot.designation}",
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
                            "elevage/consommation_form.html",
                            {
                                "form": form,
                                "lot": lot,
                                "title": f"تسجيل استهلاك — {lot.designation}",
                                "action_label": "حفظ",
                            },
                        )

                    conso = form.save(commit=False)
                    conso.lot = lot
                    conso.created_by = request.user
                    conso.save()  # triggers signal → stock decrease + mouvement

                messages.success(
                    request,
                    f"تم تسجيل الاستهلاك: {conso.quantite} {intrant.unite_mesure} من « {intrant.designation} » بتاريخ {conso.date}.",
                )
                logger.info(
                    "Consommation pk=%s created (lot pk=%s, intrant pk=%s, "
                    "quantite=%s, date=%s) by '%s'.",
                    conso.pk,
                    lot.pk,
                    intrant.pk,
                    conso.quantite,
                    conso.date,
                    request.user,
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

        form = ConsommationForm(lot=lot, initial={"date": datetime.date.today()})

    return render(
        request,
        "elevage/consommation_form.html",
        {
            "form": form,
            "lot": lot,
            "title": f"تسجيل استهلاك — {lot.designation}",
            "action_label": "حفظ",
        },
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
        Consommation.objects.select_related("lot", "intrant"), pk=pk
    )
    lot = conso.lot

    if not _assert_lot_ouvert(lot, request):
        return redirect("elevage:lot_detail", pk=lot.pk)

    if request.method == "POST":
        form = ConsommationForm(request.POST, instance=conso, lot=lot)
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
                                intrant=stock_check_intrant
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
                                    "elevage/consommation_form.html",
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
                                "elevage/consommation_form.html",
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
        form = ConsommationForm(instance=conso, lot=lot)

    return render(
        request,
        "elevage/consommation_form.html",
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

    qs = Consommation.objects.select_related(
        "lot", "intrant__categorie", "created_by"
    ).order_by("-date", "-created_at")

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
    lots = LotElevage.objects.order_by("-date_ouverture")
    intrants = (
        Intrant.objects.filter(categorie__consommable_en_lot=True, actif=True)
        .select_related("categorie")
        .order_by("designation")
    )

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
            "title": "الاستهلاكات",
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
    qs = Mortalite.objects.select_related("lot").order_by("-date", "-created_at")

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
    lots = LotElevage.objects.order_by("-date_ouverture")

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
            "title": "النفوق",
        },
    )


# ===========================================================================
# TransfertLot — Create  (Poussinière → Poulailler, immutable audit record)
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def transfert_create(request, lot_pk):
    """
    Move a lot from its current building to another (typically Poussinière
    → Poulailler once it reaches age_transfert_poussiniere_jours).

    The lot itself is never closed by this — only batiment_destination is
    applied to it (transfert_lot_post_save signal). TransfertLot records are
    immutable once created (no edit/delete view is provided).
    """
    lot = get_object_or_404(LotElevage, pk=lot_pk)

    if not _assert_lot_ouvert(lot, request):
        return redirect("elevage:lot_detail", pk=lot.pk)

    if request.method == "POST":
        form = TransfertLotForm(request.POST, lot=lot)
        if form.is_valid():
            try:
                transfert = form.save(commit=False)
                transfert.lot = lot
                transfert.created_by = request.user
                transfert.save()  # signal moves lot.batiment → destination

                messages.success(
                    request,
                    f"تم نقل الدفعة « {lot.designation} » إلى "
                    f"« {transfert.batiment_destination.nom} » "
                    f"({transfert.effectif_transfere} طير، العمر {transfert.age_jours_transfert} يوم).",
                )
                logger.info(
                    "TransfertLot pk=%s created for lot pk=%s by '%s' (%s → %s).",
                    transfert.pk,
                    lot.pk,
                    request.user,
                    transfert.batiment_origine_id,
                    transfert.batiment_destination_id,
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
        import datetime

        form = TransfertLotForm(
            lot=lot, initial={"date_transfert": datetime.date.today()}
        )

    return render(
        request,
        "elevage/transfert_form.html",
        {
            "form": form,
            "lot": lot,
            "title": f"نقل الدفعة — {lot.designation}",
            "action_label": "تأكيد النقل",
        },
    )


# ===========================================================================
# PeseeEchantillon — Create / Delete
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def pesee_create(request, lot_pk):
    """Record a sample weighing (birds or eggs) for a lot."""
    lot = get_object_or_404(LotElevage, pk=lot_pk)

    if not _assert_lot_ouvert(lot, request):
        return redirect("elevage:lot_detail", pk=lot.pk)

    if request.method == "POST":
        form = PeseeEchantillonForm(request.POST, lot=lot)
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

        form = PeseeEchantillonForm(lot=lot, initial={"date": datetime.date.today()})

    return render(
        request,
        "elevage/pesee_form.html",
        {
            "form": form,
            "lot": lot,
            "title": f"وزن عينة — {lot.designation}",
            "action_label": "حفظ",
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
    """
    lot = get_object_or_404(LotElevage, pk=lot_pk)

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
    """
    qs = RecolteOeufs.objects.select_related("lot", "pesee").order_by(
        "-date", "-created_at"
    )

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
            "title": "جمع البيض",
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
    """
    import datetime

    lots_ouverts = (
        LotElevage.objects.filter(statut=LotElevage.STATUT_OUVERT)
        .select_related("batiment", "fournisseur_poussins")
        .order_by("-date_ouverture")
    )

    today = datetime.date.today()
    sept_jours = today - datetime.timedelta(days=7)

    mortalites_recentes = (
        Mortalite.objects.filter(date__gte=sept_jours)
        .select_related("lot")
        .order_by("-date")[:20]
    )

    consommations_recentes = (
        Consommation.objects.filter(date__gte=sept_jours)
        .select_related("lot", "intrant")
        .order_by("-date")[:20]
    )

    # Lots with abnormal mortality
    lots_alerte_mortalite = [
        lot for lot in lots_ouverts if verifier_mortalite_anormale(lot)
    ]

    # Lots in Poussinière past the configured transfer-age threshold
    lots_alerte_transfert = lots_a_transferer()

    # Summary stats
    total_effectif_vivant = sum(lot.effectif_vivant for lot in lots_ouverts)
    nb_lots_ouverts = lots_ouverts.count()
    nb_lots_fermes = LotElevage.objects.filter(statut=LotElevage.STATUT_FERME).count()

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
    lot = get_object_or_404(LotElevage, pk=pk)

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
