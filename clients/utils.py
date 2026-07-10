"""
clients/utils.py

Business-logic helpers for the client AR cycle.

  appliquer_paiement_client        — Apply a payment to selected invoices (BR-FAC-03)
  generer_reference_bl_client      — Sequential BLC reference
  generer_reference_facture_client — Sequential FAC reference
  get_client_solde                 — Full financial snapshot for one client
  get_client_aging_buckets         — Aged-receivable analysis for reporting

v1.4 — Multi-Branch Architecture (§3.5): Client stays global (BR-BRA-06), but
BLClient / FactureClient / PaiementClient / AbonnementClient are branch-scoped
(BR-BRA-01). Both FIFO and manual allocation below now refuse to cross branch
boundaries — a payment recorded in one branch can never settle another
branch's invoices, even for the same client (mirrors
PaiementClientAllocation.clean()). Reporting helpers take an optional
`branche` (Vue par Branche when given, Vue Globale — summed across all
branches — when omitted, per §3.5.5).
"""

from decimal import Decimal
import calendar
import datetime
import logging

from django.db import models

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Manual payment allocation  (BR-FAC-03)
# ---------------------------------------------------------------------------


def appliquer_paiement_client_fifo(paiement) -> dict:
    """
    Apply a PaiementClient to open invoices using FIFO ordering (oldest first),
    scoped to the payment's OWN branche (BR-BRA-01).

    Mirrors appliquer_reglement_fifo on the supplier side: a client served by
    two branches has two fully independent FIFO queues, even though it is
    the same Client record (§3.5.3 ¶4) — a payment recorded in one branch
    must never reach another branch's open invoices.
    Any surplus beyond all open invoices in this branche is logged (no
    client acompte model).

    Args:
        paiement (PaiementClient): Freshly saved payment record.

    Returns:
        dict: allocations_creees, montant_alloue_total, surplus.
    """
    from clients.models import PaiementClientAllocation, FactureClient

    montant_restant = Decimal(str(paiement.montant))
    allocations_creees = 0
    montant_alloue_total = Decimal("0")

    factures_ouvertes = FactureClient.objects.filter(
        client=paiement.client,
        branche=paiement.branche,
        statut__in=[
            FactureClient.STATUT_NON_PAYEE,
            FactureClient.STATUT_PARTIELLEMENT_PAYEE,
        ],
    ).order_by("date_facture", "pk")

    for facture in factures_ouvertes:
        if montant_restant <= 0:
            break
        alloue = min(montant_restant, facture.reste_a_payer)
        if alloue <= 0:
            continue

        PaiementClientAllocation.objects.create(
            paiement=paiement,
            facture=facture,
            montant_alloue=alloue,
        )
        facture.montant_regle = facture.montant_regle + alloue
        facture.recalculer_solde()

        montant_restant -= alloue
        montant_alloue_total += alloue
        allocations_creees += 1

        logger.debug(
            "FIFO client: paiement pk=%s → facture %s alloué %s DZD.",
            paiement.pk,
            facture.reference,
            alloue,
        )

    if montant_restant > 0:
        creer_acompte_client_si_surplus(paiement, montant_restant)
        logger.info(
            "appliquer_paiement_client_fifo: paiement pk=%s surplus %s DZD "
            "stocké comme AcompteClient (branche %s).",
            paiement.pk,
            montant_restant,
            paiement.branche.code,
        )

    logger.info(
        "appliquer_paiement_client_fifo: paiement pk=%s — %d allocation(s) totalling %s DZD.",
        paiement.pk,
        allocations_creees,
        montant_alloue_total,
    )

    return {
        "allocations_creees": allocations_creees,
        "montant_alloue_total": montant_alloue_total,
        "surplus": montant_restant,
    }


def appliquer_paiement_client(paiement, allocations: list[dict]) -> dict:
    """
    Apply a PaiementClient to one or more FactureClient records according to
    the user's explicit selection (BR-FAC-03 — unlike supplier FIFO, the
    client selects which invoices to pay).

    Each entry in *allocations* is a dict::

        {"facture": <FactureClient instance>, "montant_alloue": <Decimal>}

    Validation performed here (raises ValueError on failure):
      - Sum of allocations must not exceed paiement.montant.
      - Each individual montant_alloue must not exceed facture.reste_a_payer.
      - All factures must belong to paiement.client.
      - All factures must belong to paiement.branche (BR-BRA-01) — a payment
        recorded in one branch can never settle another branch's invoice,
        even for the same client (mirrors PaiementClientAllocation.clean()).

    On success:
      - Creates PaiementClientAllocation records.
      - Calls facture.recalculer_solde() for each touched invoice.

    Args:
        paiement (PaiementClient): Freshly-created (or existing) payment record.
        allocations (list[dict]):  Validated allocation entries from the view.

    Returns:
        dict: Summary with keys ``allocations_creees`` (int) and
              ``montant_alloue_total`` (Decimal).
    """
    from clients.models import PaiementClientAllocation

    montant_disponible = Decimal(str(paiement.montant))
    montant_alloue_total = Decimal("0")
    allocations_creees = 0

    for entry in allocations:
        facture = entry["facture"]
        montant = Decimal(str(entry["montant_alloue"]))

        if montant <= 0:
            continue

        # Guard: invoice must belong to the same client.
        if facture.client_id != paiement.client_id:
            raise ValueError(
                f"La facture {facture.reference} n'appartient pas au client "
                f"{paiement.client.nom}."
            )

        # Guard: invoice must belong to the same branche (BR-BRA-01) — a
        # payment cannot be silently routed to settle another branch's debt.
        if facture.branche_id != paiement.branche_id:
            raise ValueError(
                f"BR-BRA-01 : la facture {facture.reference} appartient à "
                f"une branche différente de celle du paiement."
            )

        # Guard: do not over-allocate a single invoice.
        if montant > facture.reste_a_payer:
            raise ValueError(
                f"Le montant alloué ({montant} DZD) dépasse le reste à payer "
                f"de la facture {facture.reference} ({facture.reste_a_payer} DZD)."
            )

        # Guard: do not exceed the available payment balance.
        if montant_alloue_total + montant > montant_disponible:
            raise ValueError(
                f"La somme des allocations ({montant_alloue_total + montant} DZD) "
                f"dépasse le montant du paiement ({montant_disponible} DZD)."
            )

        PaiementClientAllocation.objects.create(
            paiement=paiement,
            facture=facture,
            montant_alloue=montant,
        )

        facture.montant_regle = facture.montant_regle + montant
        facture.recalculer_solde()

        montant_alloue_total += montant
        allocations_creees += 1
        logger.debug(
            "Paiement client pk=%s: allocated %s DZD to facture %s.",
            paiement.pk,
            montant,
            facture.reference,
        )

    logger.info(
        "appliquer_paiement_client: paiement pk=%s — %d allocation(s) totalling %s DZD.",
        paiement.pk,
        allocations_creees,
        montant_alloue_total,
    )

    # BR-FAC-03 leaves the choice of which invoices to pay to the user —
    # whatever they didn't attribute becomes a prepayment (AcompteClient)
    # instead of sitting idle, so the client doesn't need to come back and
    # manually allocate it once a new facture exists.
    surplus = montant_disponible - montant_alloue_total
    if surplus > 0:
        creer_acompte_client_si_surplus(paiement, surplus)

    return {
        "allocations_creees": allocations_creees,
        "montant_alloue_total": montant_alloue_total,
        "surplus": surplus,
    }


def creer_acompte_client_si_surplus(paiement, surplus):
    """
    Store *surplus* (the portion of `paiement` not attributed to any
    invoice) as an AcompteClient — a prepayment that
    consommer_acomptes_client_fifo will draw down automatically against the
    client's next facture(s), oldest first.

    Idempotency guard: PaiementClient.paiement is a OneToOneField on
    AcompteClient, so this is a no-op if one already exists for this
    paiement (defensive — callers should only invoke this once per
    paiement).

    Args:
        paiement (PaiementClient): The payment that produced the surplus.
        surplus (Decimal): Amount left unallocated (> 0).

    Returns:
        AcompteClient | None
    """
    from clients.models import AcompteClient

    if surplus <= 0:
        return None

    existing = getattr(paiement, "acompte", None)
    if existing is not None:
        logger.warning(
            "creer_acompte_client_si_surplus: paiement pk=%s already has an "
            "AcompteClient — skipping.",
            paiement.pk,
        )
        return existing

    acompte = AcompteClient.objects.create(
        client=paiement.client,
        branche=paiement.branche,
        paiement=paiement,
        montant=surplus,
        montant_restant=surplus,
        date=paiement.date_paiement,
        notes=(
            f"Surplus automatique depuis le paiement du "
            f"{paiement.date_paiement} — {paiement.montant} DZD total."
        ),
    )
    logger.info(
        "Surplus of %s DZD stored as AcompteClient for %s (branche=%s).",
        surplus,
        paiement.client.nom,
        paiement.branche.code,
    )
    return acompte


# ---------------------------------------------------------------------------
# Prepayment consumption engine (mirrors achats.utils.consommer_acomptes_fifo)
# ---------------------------------------------------------------------------


def consommer_acomptes_client_fifo(facture):
    """
    Draw down unused AcompteClient advances (prepayments / overpayment
    surplus) against a freshly-created facture, oldest advance first
    (FIFO), scoped to the SAME client + branche (BR-BRA-01) — mirrors
    achats.utils.consommer_acomptes_fifo but for the client AR cycle.

    Typical flow this enables: a client pays in advance (e.g. a cheque
    handed over before any facture exists), the whole amount becomes one
    AcompteClient (see creer_acompte_client_si_surplus). Each time a new
    facture for that client is created afterwards, this function is called
    (from the m2m_changed signal, right after montant_ttc is computed) and
    consumes the advance(s) automatically, one invoice at a time, until the
    advance is exhausted.

    For every dinar consumed:
      - An immutable AllocationAcompteClient record is created (audit trail).
      - The acompte's montant_restant is decremented; `utilise` flips to
        True once it reaches 0.
      - facture.montant_regle is increased and recalculer_solde() is called.

    Called inside the same DB transaction as facture creation.

    Args:
        facture (FactureClient): The freshly-created invoice (its
            montant_ttc/reste_a_payer must already be set).
    """
    from clients.models import AcompteClient, AllocationAcompteClient

    if facture.reste_a_payer <= 0:
        return

    acomptes = AcompteClient.objects.filter(
        client=facture.client,
        branche=facture.branche,
        montant_restant__gt=0,
    ).order_by("date", "pk")

    montant_a_couvrir = facture.reste_a_payer

    for acompte in acomptes:
        if montant_a_couvrir <= 0:
            break

        montant_a_consommer = min(acompte.montant_restant, montant_a_couvrir)
        if montant_a_consommer <= 0:
            continue

        AllocationAcompteClient.objects.create(
            acompte=acompte,
            facture=facture,
            montant_alloue=montant_a_consommer,
        )

        acompte.montant_restant = acompte.montant_restant - montant_a_consommer
        acompte.utilise = acompte.montant_restant <= 0
        acompte.save(update_fields=["montant_restant", "utilise"])

        facture.montant_regle = facture.montant_regle + montant_a_consommer
        montant_a_couvrir -= montant_a_consommer

        logger.info(
            "Prepayment: consumed %s DZD from acompte pk=%s for %s to pay "
            "facture %s. Acompte remaining: %s DZD.",
            montant_a_consommer,
            acompte.pk,
            facture.client.nom,
            facture.reference,
            acompte.montant_restant,
        )

    if facture.montant_regle > 0:
        facture.recalculer_solde()


# ---------------------------------------------------------------------------
# Admin-only cascade delete — Paiement (destructive)
# ---------------------------------------------------------------------------


def _montant_prorata_periode(montant_mensuel, periode_debut, periode_fin):
    """
    Prorated forfait amount due for [periode_debut, periode_fin], inclusive,
    counted in actual days rather than whole months.

    Walks the range one calendar month at a time; for each month touched,
    only the days that actually fall inside [periode_debut, periode_fin]
    are counted, as a fraction of that month's real length (28-31 days) —
    so a full calendar month always comes out to exactly `montant_mensuel`,
    while a partial month is billed proportionally to the days left in it.
    """
    total = Decimal("0")
    curseur = periode_debut.replace(day=1)
    while curseur <= periode_fin:
        annee, mois = curseur.year, curseur.month
        jours_du_mois = calendar.monthrange(annee, mois)[1]
        debut_mois = curseur
        fin_mois = curseur.replace(day=jours_du_mois)

        chevauchement_debut = max(periode_debut, debut_mois)
        chevauchement_fin = min(periode_fin, fin_mois)
        jours_couverts = (chevauchement_fin - chevauchement_debut).days + 1

        fraction = Decimal(jours_couverts) / Decimal(jours_du_mois)
        total += montant_mensuel * fraction

        curseur = (
            curseur.replace(year=annee + 1, month=1, day=1)
            if mois == 12
            else curseur.replace(month=mois + 1, day=1)
        )
    return total.quantize(Decimal("0.01"))


def generer_facture_abonnement(
    abonnement,
    periode_debut=None,
    periode_fin=None,
    date_facture=None,
    date_echeance=None,
    created_by=None,
):
    """
    Bill a forfait AbonnementClient (BR-ABO-03) for the given period — a
    fixed amount due per calendar month, prorated by actual days for any
    partial month, regardless of whether anything was actually collected/
    delivered that period (LivraisonPartielle stays optional/informational
    under forfait).

    Amount logic (day-prorated): the period can span several calendar
    months (e.g. 01/2025 → 05/2025), so the due amount is computed
    month-by-month as `montant_forfait × (jours couverts / jours du mois)`
    and summed — a full month bills the full monthly amount, a partial
    month (e.g. 15 days of a 30-day month) bills half. Any FactureClient
    already issued against this same abonnement for a period overlapping
    the requested range is then deducted from that total, so re-running
    this for a wider or shifted range only ever bills the real remainder —
    never double-bills days that were already invoiced.

    Guardrail: the requested [periode_debut, periode_fin] must fall
    entirely within the subscription's own lifetime (date_debut → date_fin,
    when date_fin is set) — you can't bill for a period before the
    subscription started or after it ended.

    Creates a FactureClient directly (abonnement + periode_debut/periode_fin
    set, bls left empty) with montant_ht/tva/ttc computed here instead of in
    the m2m_changed signal, then immediately calls
    consommer_acomptes_client_fifo so a client who prepaid (AcompteClient)
    has this due auto-settled — this is what makes `mode_paiement=prepaye`
    work: record the client's advance payment whenever it suits them, and
    it quietly covers each new échéance as it's generated.

    Args:
        abonnement (AbonnementClient): must have mode_facturation=forfait
            and statut=actif.
        periode_debut/periode_fin (date | None): billed period; defaults to
            abonnement.periode_courante() (current calendar month).
        date_facture (date | None): defaults to today.
        date_echeance (date | None): optional due date.
        created_by (User | None).

    Returns:
        FactureClient: the newly created invoice (for the net remainder).

    Raises:
        ValueError: subscription is not an active forfait, montant_forfait
            is not configured, the requested period falls outside the
            subscription's date_debut/date_fin, or the requested period is
            already fully covered by prior invoices (net remainder <= 0).
    """
    from clients.models import AbonnementClient, FactureClient

    if abonnement.mode_facturation != AbonnementClient.MODE_FACTURATION_FORFAIT:
        raise ValueError(
            "BR-ABO-03 : لا يمكن توليد فاتورة جزافية لاشتراك «بحسب الكمية»."
        )
    if abonnement.statut != AbonnementClient.STATUT_ACTIF:
        raise ValueError("لا يمكن توليد فاتورة لاشتراك غير نشط.")
    if not abonnement.montant_forfait or abonnement.montant_forfait <= 0:
        raise ValueError("المبلغ الجزافي الدوري غير محدَّد لهذا الاشتراك.")

    if periode_debut is None or periode_fin is None:
        periode_debut, periode_fin = abonnement.periode_courante(date_facture)

    if periode_fin < periode_debut:
        raise ValueError("نهاية الفترة يجب أن تكون بعد بدايتها.")

    # Guardrail: period must sit entirely within the subscription's own
    # lifetime — no billing before it started or after it ended.
    if periode_debut < abonnement.date_debut:
        raise ValueError(
            f"بداية الفترة ({periode_debut}) قبل تاريخ بدء الاشتراك "
            f"({abonnement.date_debut}) — لا يمكن الفوترة قبل بدء الاشتراك."
        )
    if abonnement.date_fin and periode_fin > abonnement.date_fin:
        raise ValueError(
            f"نهاية الفترة ({periode_fin}) بعد تاريخ انتهاء الاشتراك "
            f"({abonnement.date_fin}) — لا يمكن الفوترة بعد انتهاء الاشتراك."
        )

    montant_mensuel = Decimal(str(abonnement.montant_forfait))
    montant_du_periode = _montant_prorata_periode(
        montant_mensuel, periode_debut, periode_fin
    )

    # Deduct any invoice already issued for this abonnement whose billed
    # period overlaps the requested range — this is what makes generating
    # a wider/shifted range idempotent instead of double-billing days
    # that were already covered.
    factures_chevauchantes = abonnement.factures_abonnement.filter(
        periode_debut__lte=periode_fin, periode_fin__gte=periode_debut
    )
    deja_facture = factures_chevauchantes.aggregate(total=models.Sum("montant_ttc"))[
        "total"
    ] or Decimal("0")

    montant_ht = montant_du_periode - deja_facture

    if montant_ht <= 0:
        raise ValueError(
            f"تم توليد فاتورة (فواتير) تغطي هذه الفترة بالكامل مسبقاً "
            f"({periode_debut} → {periode_fin}) — المبلغ المستحق للفترة "
            f"{montant_du_periode} د.ج، وقد فُوتر منه {deja_facture} د.ج بالفعل."
        )

    date_facture = date_facture or datetime.date.today()

    facture = FactureClient.objects.create(
        reference=generer_reference_facture_client(abonnement.branche),
        branche=abonnement.branche,
        client=abonnement.client,
        abonnement=abonnement,
        periode_debut=periode_debut,
        periode_fin=periode_fin,
        date_facture=date_facture,
        date_echeance=date_echeance,
        montant_ht=montant_ht,
        taux_tva=Decimal("0"),
        montant_tva=Decimal("0"),
        montant_ttc=montant_ht,
        montant_regle=Decimal("0"),
        reste_a_payer=montant_ht,
        statut=FactureClient.STATUT_NON_PAYEE,
        notes=(
            f"اشتراك جزافي — {abonnement.produit_fini.designation} — "
            f"الفترة {periode_debut} → {periode_fin} (بالتناسب اليومي = "
            f"{montant_du_periode} د.ج على أساس {montant_mensuel} د.ج/شهر"
            + (f"، مخصوم منه {deja_facture} د.ج مفوتر سابقاً" if deja_facture else "")
            + ")."
        ),
        created_by=created_by,
    )

    # Auto-settle from any prepayment (AcompteClient) the client already has
    # on this branche — this is the whole point of mode_paiement=prepaye.
    consommer_acomptes_client_fifo(facture)

    logger.info(
        "generer_facture_abonnement: abonnement pk=%s → facture %s "
        "(%s DZD net / %s DZD période brute au prorata, déjà facturé %s DZD, "
        "période %s → %s).",
        abonnement.pk,
        facture.reference,
        montant_ht,
        montant_du_periode,
        deja_facture,
        periode_debut,
        periode_fin,
    )
    return facture


def generer_echeances_abonnements_forfait(
    branche=None, date_facture=None, created_by=None
):
    """
    Bulk-generate this period's due for every active forfait
    AbonnementClient (scoped to *branche*, or every branche when omitted),
    skipping any subscription whose current period was already billed.

    Meant to be triggered manually (a "توليد فواتير هذا الشهر" button) —
    idempotent, so re-running it mid-month is harmless.

    Returns:
        dict: {"crees": [FactureClient, ...], "ignores": [AbonnementClient, ...],
               "erreurs": [(AbonnementClient, str), ...]}
    """
    from clients.models import AbonnementClient

    qs = AbonnementClient.objects.filter(
        statut=AbonnementClient.STATUT_ACTIF,
        mode_facturation=AbonnementClient.MODE_FACTURATION_FORFAIT,
    ).select_related("client", "branche", "produit_fini")
    if branche is not None:
        qs = qs.filter(branche=branche)

    crees, ignores, erreurs = [], [], []
    for abonnement in qs:
        try:
            facture = generer_facture_abonnement(
                abonnement, date_facture=date_facture, created_by=created_by
            )
            crees.append(facture)
        except ValueError as exc:
            if "مسبقاً" in str(exc):
                ignores.append(abonnement)
            else:
                erreurs.append((abonnement, str(exc)))
        except Exception as exc:
            logger.exception(
                "generer_echeances_abonnements_forfait: abonnement pk=%s: %s",
                abonnement.pk,
                exc,
            )
            erreurs.append((abonnement, str(exc)))

    logger.info(
        "generer_echeances_abonnements_forfait: %d créée(s), %d ignorée(s) "
        "(déjà facturée), %d erreur(s).",
        len(crees),
        len(ignores),
        len(erreurs),
    )
    return {"crees": crees, "ignores": ignores, "erreurs": erreurs}


def supprimer_paiement_client_cascade(paiement):
    """
    ADMIN-ONLY hard delete: remove a PaiementClient and reverse every side
    effect it produced — its manual/FIFO allocations to factures, and
    (if it created one) everything its AcompteClient went on to fund via
    consommer_acomptes_client_fifo, possibly on invoices created well after
    this payment. Mirrors
    achats.utils.supprimer_reglement_fournisseur_cascade.

    The caller (the view) MUST verify the requesting user is an admin
    before calling this — payments are normally immutable after creation.

    Side effects, all within one DB transaction:
      1. Every PaiementClientAllocation this payment made to a facture is
         removed; that facture's montant_regle / reste_a_payer / statut
         are recalculated.
      2. If this payment produced an AcompteClient (surplus / prepayment),
         everything THAT acompte went on to fund is reversed the same way
         (facture balances recalculated), then the acompte itself is
         deleted (cascades automatically with the paiement too, but done
         explicitly here so the funded factures are recalculated first).
      3. The paiement itself is deleted.

    Note: unlike supprimer_facture_client_cascade, this never touches BLs
    or stock — a paiement never created any stock movement.

    Args:
        paiement (PaiementClient): The payment to delete.

    Returns:
        dict: {
            "paiement_montant": Decimal,
            "client_nom": str,
            "factures_impactees": list[str],
            "acompte_supprime": bool,
        }
    """
    from django.db import transaction
    from clients.models import AcompteClient

    summary = {
        "paiement_montant": paiement.montant,
        "client_nom": paiement.client.nom,
        "factures_impactees": [],
        "acompte_supprime": False,
    }

    with transaction.atomic():
        # 1. Direct allocations this paiement made.
        for alloc in list(paiement.allocations.select_related("facture")):
            autre_facture = alloc.facture
            autre_facture.montant_regle = max(
                Decimal("0"), autre_facture.montant_regle - alloc.montant_alloue
            )
            alloc.delete()
            autre_facture.recalculer_solde()
            summary["factures_impactees"].append(autre_facture.reference)
            logger.warning(
                "Suppression du paiement pk=%s a retiré %s DZD alloués à la "
                "facture %s (solde recalculé).",
                paiement.pk,
                alloc.montant_alloue,
                autre_facture.reference,
            )

        # 2. Overpayment / prepayment credit created by this paiement.
        try:
            acompte = paiement.acompte
        except AcompteClient.DoesNotExist:
            acompte = None

        if acompte is not None:
            for alloc in list(acompte.allocations.select_related("facture")):
                autre_facture = alloc.facture
                autre_facture.montant_regle = max(
                    Decimal("0"), autre_facture.montant_regle - alloc.montant_alloue
                )
                alloc.delete()
                autre_facture.recalculer_solde()
                summary["factures_impactees"].append(autre_facture.reference)
                logger.warning(
                    "Suppression du paiement pk=%s a retiré %s DZD consommés "
                    "depuis son acompte sur la facture %s (solde recalculé).",
                    paiement.pk,
                    alloc.montant_alloue,
                    autre_facture.reference,
                )
            acompte.delete()
            summary["acompte_supprime"] = True

        # 3. The paiement itself.
        paiement.delete()

    logger.info(
        "ADMIN CASCADE DELETE: paiement (%s DZD, %s) supprimé. %d facture(s) "
        "impactée(s) : %s. Acompte supprimé : %s.",
        summary["paiement_montant"],
        summary["client_nom"],
        len(summary["factures_impactees"]),
        summary["factures_impactees"],
        summary["acompte_supprime"],
    )
    return summary


# ---------------------------------------------------------------------------
# Admin-only cascade delete — Facture + BLs + Paiements (destructive)
# ---------------------------------------------------------------------------


def supprimer_facture_client_cascade(facture):
    """
    ADMIN-ONLY hard delete: remove a FactureClient together with every BL it
    includes and every PaiementClient that allocated money to it — a full
    undo of an invoice cycle, for correcting mistakes.

    This intentionally bypasses two rules enforced everywhere else in the
    app, so the caller (the view) MUST verify the requesting user is an
    admin before calling this:
      - BR-BLC-03 : a Facturé BL is normally locked — here it is deleted
        outright.
      - PaiementClientAllocation immutability : paiements are normally
        never edited after creation — here any paiement that touched this
        facture is deleted outright.

    Side effects (everything stays scoped to its own branche — BR-BRA-01):
      1. For every PaiementClient that has at least one
         PaiementClientAllocation pointing at `facture`:
           - Any OTHER allocations of that same paiement (i.e. money it
             also paid toward a DIFFERENT facture — either from FIFO or a
             manual multi-invoice selection) are removed too, and that
             other facture's montant_regle / reste_a_payer / statut are
             recalculated, since the cash behind that allocation no longer
             exists in the system once the paiement is gone. Logged so it
             can be audited.
           - The paiement itself is deleted (there is no client-side
             "acompte" surplus model to worry about, unlike the supplier
             side).
      2. For every BLClient included in `facture`:
           - Its LIVRE stock decrease is reversed (StockProduitFini
             increased back, corrective StockMouvement ENTREE logged — see
             clients.signals.annuler_sortie_stock_bl_client).
           - The BL is deleted (cascades to its lignes).
      3. `facture` itself is deleted last.

    Wrapped in a single DB transaction — either the whole cascade succeeds
    or none of it is applied.

    Args:
        facture (FactureClient): The invoice to delete, with everything it
            created.

    Returns:
        dict: {
            "facture_reference": str,
            "bls_references": list[str],
            "paiements_references": list[str],
            "factures_tierces_impactees": list[str],
        }
    """
    from django.db import transaction
    from clients.models import PaiementClientAllocation, PaiementClient
    from clients.signals import annuler_sortie_stock_bl_client

    summary = {
        "facture_reference": facture.reference,
        "bls_references": [],
        "paiements_references": [],
        "factures_tierces_impactees": [],
    }

    with transaction.atomic():
        # 1. Paiements that paid (any part of) this facture.
        paiement_ids = list(
            PaiementClientAllocation.objects.filter(facture=facture)
            .values_list("paiement_id", flat=True)
            .distinct()
        )
        paiements = list(PaiementClient.objects.filter(pk__in=paiement_ids))

        for paiement in paiements:
            # 1a. Allocations of this paiement to OTHER factures also
            #     vanish with it — recompute those factures' soldes.
            autres_allocations = list(
                paiement.allocations.exclude(facture=facture).select_related("facture")
            )
            for alloc in autres_allocations:
                autre_facture = alloc.facture
                autre_facture.montant_regle = max(
                    Decimal("0"), autre_facture.montant_regle - alloc.montant_alloue
                )
                alloc.delete()
                autre_facture.recalculer_solde()
                summary["factures_tierces_impactees"].append(autre_facture.reference)
                logger.warning(
                    "Suppression cascade de la facture %s a retiré %s DZD "
                    "alloués par le paiement pk=%s à la facture tierce %s "
                    "(solde recalculé).",
                    facture.reference,
                    alloc.montant_alloue,
                    paiement.pk,
                    autre_facture.reference,
                )

            # 1b. Remaining allocations (the ones pointing at `facture`).
            paiement.allocations.all().delete()

            summary["paiements_references"].append(
                f"{paiement.montant} DZD ({paiement.date_paiement})"
            )
            paiement.delete()

        # 1c. This facture may itself have been paid (in whole or in part)
        #     from an AcompteClient belonging to a DIFFERENT paiement (one
        #     that did not touch it via PaiementClientAllocation at all).
        #     Give that money back to the advance.
        for alloc in list(facture.allocations_acompte.select_related("acompte")):
            source_acompte = alloc.acompte
            source_acompte.montant_restant = (
                source_acompte.montant_restant + alloc.montant_alloue
            )
            source_acompte.utilise = source_acompte.montant_restant <= 0
            source_acompte.save(update_fields=["montant_restant", "utilise"])
            alloc.delete()
            logger.info(
                "Suppression de la facture %s : %s DZD restitués à " "l'acompte pk=%s.",
                facture.reference,
                alloc.montant_alloue,
                source_acompte.pk,
            )

        # 2. BLs included in the facture: reverse stock, then delete.
        bls = list(
            facture.bls.select_related("branche").prefetch_related(
                "lignes__produit_fini"
            )
        )
        for bl in bls:
            annuler_sortie_stock_bl_client(bl)
            summary["bls_references"].append(bl.reference)
            bl.delete()

        # 3. The facture itself.
        facture.delete()

    logger.info(
        "ADMIN CASCADE DELETE: facture client %s supprimée avec %d BL(s) et "
        "%d paiement(s). %d facture(s) tierce(s) impactée(s) : %s.",
        summary["facture_reference"],
        len(summary["bls_references"]),
        len(summary["paiements_references"]),
        len(summary["factures_tierces_impactees"]),
        summary["factures_tierces_impactees"],
    )
    return summary


# ---------------------------------------------------------------------------
# Sequential reference generators
# ---------------------------------------------------------------------------


def generer_reference_bl_client(branche) -> str:
    """
    Generate the next BL Client reference, scoped to *branche*.
    Format: <prefixe_bl_client>-<code_branche>-<YYYY>-<NNNN>
            e.g. BLC-EST-2026-0001 (BR-BRA-05).

    Args:
        branche (Branche): The branch this BL belongs to (BLClient.branche
            is a required FK — BR-BRA-01).
    """
    from clients.models import BLClient
    from core.utils import generer_reference, get_company_prefix

    prefix = get_company_prefix("prefixe_bl_client")
    return generer_reference(BLClient, prefix, branche=branche)


def generer_reference_facture_client(branche) -> str:
    """
    Generate the next Facture Client reference, scoped to *branche*.
    Format: <prefixe_facture_client>-<code_branche>-<YYYY>-<NNNN>
            e.g. FAC-EST-2026-0001 (BR-BRA-05).

    Args:
        branche (Branche): The branch this invoice belongs to
            (FactureClient.branche is a required FK — BR-BRA-01).
    """
    from clients.models import FactureClient
    from core.utils import generer_reference, get_company_prefix

    prefix = get_company_prefix("prefixe_facture_client")
    return generer_reference(FactureClient, prefix, branche=branche)


# ---------------------------------------------------------------------------
# Client financial summary
# ---------------------------------------------------------------------------


def get_client_solde(client, branche=None) -> dict:
    """
    Return a complete financial snapshot for one client.

    v1.4 (§3.5.3 ¶4): BLClient/FactureClient/PaiementClient are
    branch-scoped, while Client itself stays global. Pass `branche` for the
    figures exactly as that branch's chef de branche sees them; omit it for
    the Vue Globale figures, summed across every branch this client has
    ever been served by.

    Keys:
        creance_globale      — sum of reste_a_payer on open invoices
        factures_ouvertes    — queryset ordered by date_facture ASC
        total_paiements      — sum of all payments ever recorded
        nb_factures_retard   — count of overdue invoices
        depasse_plafond      — bool: créance > plafond_credit (if configured)

    Args:
        client (Client): The client instance.
        branche (Branche | None): Scope to one branch; omit for Vue Globale.
    """
    from clients.models import FactureClient, PaiementClient, AcompteClient
    from django.db.models import Sum

    factures_ouvertes = FactureClient.objects.filter(
        client=client,
        statut__in=[
            FactureClient.STATUT_NON_PAYEE,
            FactureClient.STATUT_PARTIELLEMENT_PAYEE,
        ],
    ).order_by("date_facture", "pk")

    paiements_qs = PaiementClient.objects.filter(client=client)
    acomptes_qs = AcompteClient.objects.filter(client=client, montant_restant__gt=0)

    if branche is not None:
        factures_ouvertes = factures_ouvertes.filter(branche=branche)
        paiements_qs = paiements_qs.filter(branche=branche)
        acomptes_qs = acomptes_qs.filter(branche=branche)

    # creance_globale is now a method on Client (takes an optional branche)
    # since it has to aggregate across branch-scoped FactureClient rows.
    creance_globale = client.creance_globale(branche)

    total_paiements = paiements_qs.aggregate(total=Sum("montant"))["total"] or Decimal(
        "0"
    )

    acompte_disponible = acomptes_qs.aggregate(total=Sum("montant_restant"))[
        "total"
    ] or Decimal("0")

    today = datetime.date.today()
    nb_factures_retard = factures_ouvertes.filter(date_echeance__lt=today).count()

    depasse_plafond = bool(
        client.plafond_credit
        and client.plafond_credit > 0
        and creance_globale > client.plafond_credit
    )

    return {
        "creance_globale": creance_globale,
        "factures_ouvertes": factures_ouvertes,
        "total_paiements": total_paiements,
        "acompte_disponible": acompte_disponible,
        "nb_factures_retard": nb_factures_retard,
        "depasse_plafond": depasse_plafond,
    }


# ---------------------------------------------------------------------------
# Client aged-receivable analysis  (for reporting — §9.12)
# ---------------------------------------------------------------------------


def get_client_aging_buckets(client=None, branche=None) -> list[dict]:
    """
    Compute an aged-receivable breakdown for one client or all clients.

    Aging buckets (days past due date):
        current   — not yet due
        1_30      — 1–30 days overdue
        31_60     — 31–60 days overdue
        61_90     — 61–90 days overdue
        over_90   — > 90 days overdue

    v1.4 (§3.5.5): pass `branche` for the Vue par Branche figures (exactly
    what that branch's chef de branche sees); omit for Vue Globale, which
    sums a client's receivables across every branch that has served them.

    Args:
        client (Client | None): Filter to one client; None = all.
        branche (Branche | None): Scope to one branch; omit for Vue Globale.

    Returns:
        list[dict]: One dict per client with bucket totals.
    """
    from clients.models import FactureClient

    today = datetime.date.today()

    qs = FactureClient.objects.filter(
        statut__in=[
            FactureClient.STATUT_NON_PAYEE,
            FactureClient.STATUT_PARTIELLEMENT_PAYEE,
        ]
    ).select_related("client")

    if client:
        qs = qs.filter(client=client)
    if branche is not None:
        qs = qs.filter(branche=branche)

    buckets_by_client: dict[int, dict] = {}

    for facture in qs:
        cli = facture.client
        if cli.pk not in buckets_by_client:
            buckets_by_client[cli.pk] = {
                "client": cli,
                "current": Decimal("0"),
                "1_30": Decimal("0"),
                "31_60": Decimal("0"),
                "61_90": Decimal("0"),
                "over_90": Decimal("0"),
                "total": Decimal("0"),
            }

        entry = buckets_by_client[cli.pk]
        rap = facture.reste_a_payer
        entry["total"] += rap

        if not facture.date_echeance or facture.date_echeance >= today:
            entry["current"] += rap
        else:
            days_late = (today - facture.date_echeance).days
            if days_late <= 30:
                entry["1_30"] += rap
            elif days_late <= 60:
                entry["31_60"] += rap
            elif days_late <= 90:
                entry["61_90"] += rap
            else:
                entry["over_90"] += rap

    return sorted(buckets_by_client.values(), key=lambda x: x["client"].nom)
