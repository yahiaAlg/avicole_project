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
import datetime
import logging

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
        logger.info(
            "appliquer_paiement_client_fifo: paiement pk=%s surplus %s DZD "
            "(aucune facture ouverte restante dans la branche %s).",
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

    return {
        "allocations_creees": allocations_creees,
        "montant_alloue_total": montant_alloue_total,
    }


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
    from clients.models import FactureClient, PaiementClient
    from django.db.models import Sum

    factures_ouvertes = FactureClient.objects.filter(
        client=client,
        statut__in=[
            FactureClient.STATUT_NON_PAYEE,
            FactureClient.STATUT_PARTIELLEMENT_PAYEE,
        ],
    ).order_by("date_facture", "pk")

    paiements_qs = PaiementClient.objects.filter(client=client)

    if branche is not None:
        factures_ouvertes = factures_ouvertes.filter(branche=branche)
        paiements_qs = paiements_qs.filter(branche=branche)

    # creance_globale is now a method on Client (takes an optional branche)
    # since it has to aggregate across branch-scoped FactureClient rows.
    creance_globale = client.creance_globale(branche)

    total_paiements = paiements_qs.aggregate(total=Sum("montant"))["total"] or Decimal(
        "0"
    )

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
