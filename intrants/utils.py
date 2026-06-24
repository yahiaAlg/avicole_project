"""
intrants/utils.py

Utility / helper functions for the intrants application.

  determiner_qualite — resolve a CategorieQualite bracket from a sample's
                        average weight. Called by elevage.PeseeEchantillon
                        .qualite (and transitively by RecolteOeufs.qualite),
                        so this is load-bearing — without it those
                        properties raise ImportError.
"""

from decimal import Decimal


def determiner_qualite(poids_moyen, type_pesee):
    """
    Return the active CategorieQualite whose [poids_min, poids_max] bracket
    contains *poids_moyen* for the given *type_pesee*.

    Brackets are user-managed (intrants.CategorieQualite, same pattern as
    CategorieIntrant) and ordered by `ordre`; the first matching bracket
    wins. Returns None if no bracket covers the weight — callers treat that
    as "ungraded" rather than as an error.

    Args:
        poids_moyen (Decimal | float | int | None): Average weight in grams.
        type_pesee (str): CategorieQualite.TYPE_OISEAUX or TYPE_OEUFS.

    Returns:
        CategorieQualite | None
    """
    from intrants.models import CategorieQualite

    if poids_moyen is None:
        return None

    poids = Decimal(str(poids_moyen))

    return (
        CategorieQualite.objects.filter(
            type_pesee=type_pesee,
            actif=True,
            poids_min__lte=poids,
            poids_max__gte=poids,
        )
        .order_by("ordre")
        .first()
    )
