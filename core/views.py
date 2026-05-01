"""
core/views.py

Views for:
  - Authentication        : login, logout
  - Dashboard             : main home page with key KPIs and alerts
  - Company Information   : view / edit singleton CompanyInfo
  - User Management       : list, create, edit, deactivate, password change
"""

import logging

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.models import User
from django.db.models import Count, Sum, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core.forms import CompanyInfoForm, UserCreateForm, UserUpdateForm
from core.models import CompanyInfo, UserProfile

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Role / permission helpers
# ---------------------------------------------------------------------------


def is_admin(user):
    """True when the authenticated user has the 'admin' role in UserProfile."""
    try:
        return user.profile.role == "admin"
    except UserProfile.DoesNotExist:
        return False


def is_admin_or_manager(user):
    try:
        return user.profile.role in ("admin", "manager")
    except UserProfile.DoesNotExist:
        return False


admin_required = user_passes_test(is_admin, login_url="core:login")
admin_or_manager_required = user_passes_test(
    is_admin_or_manager, login_url="core:login"
)


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


def login_view(request):
    """
    Standard username/password login.
    Redirects to dashboard on success, or to GET parameter ``next``.
    """
    if request.user.is_authenticated:
        return redirect("core:dashboard")

    error = None

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        next_url = request.POST.get("next", "core:dashboard")

        user = authenticate(request, username=username, password=password)
        if user is not None:
            if user.is_active:
                login(request, user)
                logger.info("User '%s' logged in.", user.username)
                return redirect(next_url if next_url else "core:dashboard")
            else:
                error = "Ce compte est désactivé. Contactez un administrateur."
        else:
            error = "Nom d'utilisateur ou mot de passe incorrect."

    return render(
        request,
        "core/login.html",
        {
            "error": error,
            "next": request.GET.get("next", ""),
        },
    )


@login_required(login_url="core:login")
@require_POST
def logout_view(request):
    """POST-only logout (CSRF-protected)."""
    logger.info("User '%s' logged out.", request.user.username)
    logout(request)
    messages.success(request, "Vous avez été déconnecté avec succès.")
    return redirect("core:login")


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@login_required(login_url="core:login")
def dashboard(request):
    """
    Main dashboard.  Aggregates KPIs from every domain:
      - Open lots and alert conditions
      - Stock alerts (intrants + produits finis below seuil)
      - Overdue supplier invoices (dette en retard)
      - Overdue client invoices (créances en retard)
      - Uninvoiced supplier BLs awaiting invoicing
      - Recent stock movements (last 10)
    """
    import datetime
    from decimal import Decimal

    today = datetime.date.today()

    # ── Lots d'élevage ───────────────────────────────────────────────────
    try:
        from elevage.models import LotElevage

        lots_ouverts_count = LotElevage.objects.filter(
            statut=LotElevage.STATUT_OUVERT
        ).count()
    except Exception:
        lots_ouverts_count = 0

    # ── Stock alerts ─────────────────────────────────────────────────────
    try:
        from stock.utils import get_alertes_stock

        alertes_stock = get_alertes_stock()
        nb_alertes_intrants = len(alertes_stock.get("intrants", []))
        nb_alertes_produits = len(alertes_stock.get("produits_finis", []))
    except Exception:
        nb_alertes_intrants = 0
        nb_alertes_produits = 0
        alertes_stock = {"intrants": [], "produits_finis": []}

    # ── Supplier AP ──────────────────────────────────────────────────────
    try:
        from achats.models import FactureFournisseur

        factures_fournisseur_retard = FactureFournisseur.objects.filter(
            statut__in=[
                FactureFournisseur.STATUT_NON_PAYE,
                FactureFournisseur.STATUT_PARTIELLEMENT_PAYE,
            ],
            date_echeance__lt=today,
        ).count()

        dette_globale_total = FactureFournisseur.objects.filter(
            statut__in=[
                FactureFournisseur.STATUT_NON_PAYE,
                FactureFournisseur.STATUT_PARTIELLEMENT_PAYE,
            ]
        ).aggregate(total=Sum("reste_a_payer"))["total"] or Decimal("0")
    except Exception:
        factures_fournisseur_retard = 0
        dette_globale_total = Decimal("0")

    # ── Uninvoiced supplier BLs (RECU but not yet FACTURE) ───────────────
    try:
        from achats.models import BLFournisseur

        bls_non_factures = BLFournisseur.objects.filter(
            statut=BLFournisseur.STATUT_RECU
        ).count()
    except Exception:
        bls_non_factures = 0

    # ── Client AR ────────────────────────────────────────────────────────
    try:
        from clients.models import FactureClient

        factures_client_retard = FactureClient.objects.filter(
            statut__in=[
                FactureClient.STATUT_NON_PAYEE,
                FactureClient.STATUT_PARTIELLEMENT_PAYEE,
            ],
            date_echeance__lt=today,
        ).count()

        creance_globale_total = FactureClient.objects.filter(
            statut__in=[
                FactureClient.STATUT_NON_PAYEE,
                FactureClient.STATUT_PARTIELLEMENT_PAYEE,
            ]
        ).aggregate(total=Sum("reste_a_payer"))["total"] or Decimal("0")
    except Exception:
        factures_client_retard = 0
        creance_globale_total = Decimal("0")

    # ── Uninvoiced client BLs (LIVRE but not yet FACTURE) ────────────────
    try:
        from clients.models import BLClient

        bls_client_non_factures = BLClient.objects.filter(
            statut=BLClient.STATUT_LIVRE
        ).count()
    except Exception:
        bls_client_non_factures = 0

    # ── Recent stock movements (last 10, both segments) ──────────────────
    try:
        from stock.models import StockMouvement

        mouvements_recents = StockMouvement.objects.select_related(
            "intrant", "produit_fini"
        ).order_by("-date_mouvement", "-created_at")[:10]
    except Exception:
        mouvements_recents = []

    # ── Open lots summary (for widget) ────────────────────────────────────
    try:
        from elevage.models import LotElevage

        lots_actifs = (
            LotElevage.objects.filter(statut=LotElevage.STATUT_OUVERT)
            .select_related("batiment")
            .order_by("-date_ouverture")[:5]
        )
    except Exception:
        lots_actifs = []

    context = {
        # Lots
        "lots_ouverts_count": lots_ouverts_count,
        "lots_actifs": lots_actifs,
        # Stock alerts
        "nb_alertes_intrants": nb_alertes_intrants,
        "nb_alertes_produits": nb_alertes_produits,
        "alertes_stock": alertes_stock,
        # Supplier
        "factures_fournisseur_retard": factures_fournisseur_retard,
        "dette_globale_total": dette_globale_total,
        "bls_non_factures": bls_non_factures,
        # Client
        "factures_client_retard": factures_client_retard,
        "creance_globale_total": creance_globale_total,
        "bls_client_non_factures": bls_client_non_factures,
        # Recent activity
        "mouvements_recents": mouvements_recents,
        # Alert badge totals
        "total_alertes": (
            nb_alertes_intrants
            + nb_alertes_produits
            + factures_fournisseur_retard
            + factures_client_retard
        ),
    }
    return render(request, "core/dashboard.html", context)


# ---------------------------------------------------------------------------
# Company Information (singleton)
# ---------------------------------------------------------------------------


@login_required(login_url="core:login")
@admin_required
def company_info_view(request):
    """
    View / edit the singleton CompanyInfo record.
    GET  → display current values + pre-filled form.
    POST → validate and save; redirect back with success message (PRG).
    """
    instance = CompanyInfo.get_instance()

    if request.method == "POST":
        form = CompanyInfoForm(request.POST, request.FILES, instance=instance)
        if form.is_valid():
            form.save()
            messages.success(
                request,
                "Informations de l'entreprise mises à jour avec succès.",
            )
            logger.info("CompanyInfo updated by user '%s'.", request.user.username)
            return redirect("core:company_info")
        else:
            messages.error(
                request,
                "Veuillez corriger les erreurs dans le formulaire.",
            )
    else:
        form = CompanyInfoForm(instance=instance)

    return render(
        request,
        "core/company_info.html",
        {
            "form": form,
            "instance": instance,
            "title": "Informations de l'entreprise",
        },
    )


# ---------------------------------------------------------------------------
# User Management
# ---------------------------------------------------------------------------


@login_required(login_url="core:login")
@admin_required
def user_list(request):
    """
    List all application users with their profile roles.
    Active users are shown first, then inactive, alphabetically by username.
    """
    users = User.objects.select_related("profile").order_by("-is_active", "username")
    return render(
        request,
        "core/user_list.html",
        {
            "users": users,
            "title": "Gestion des utilisateurs",
        },
    )


@login_required(login_url="core:login")
@admin_required
def user_create(request):
    """
    Create a new application user + linked UserProfile.
    POST-Redirect-Get: on success redirects to user_list.
    """
    if request.method == "POST":
        form = UserCreateForm(request.POST)
        if form.is_valid():
            user = form.save()
            messages.success(
                request,
                f"L'utilisateur « {user.username} » a été créé avec succès.",
            )
            logger.info(
                "User '%s' created by '%s'.",
                user.username,
                request.user.username,
            )
            return redirect("core:user_list")
        else:
            messages.error(request, "Veuillez corriger les erreurs dans le formulaire.")
    else:
        form = UserCreateForm()

    return render(
        request,
        "core/user_form.html",
        {
            "form": form,
            "title": "Créer un utilisateur",
            "action_label": "Créer",
        },
    )


@login_required(login_url="core:login")
@admin_required
def user_edit(request, pk):
    """
    Edit an existing user's info and UserProfile.
    Password changes are handled separately via user_password_change.
    """
    target_user = get_object_or_404(User, pk=pk)

    if request.method == "POST":
        form = UserUpdateForm(request.POST, instance=target_user)
        if form.is_valid():
            form.save()
            messages.success(
                request,
                f"L'utilisateur « {target_user.username} » a été mis à jour avec succès.",
            )
            logger.info(
                "User '%s' edited by '%s'.",
                target_user.username,
                request.user.username,
            )
            return redirect("core:user_list")
        else:
            messages.error(request, "Veuillez corriger les erreurs dans le formulaire.")
    else:
        form = UserUpdateForm(instance=target_user)

    return render(
        request,
        "core/user_form.html",
        {
            "form": form,
            "target_user": target_user,
            "title": f"Modifier l'utilisateur « {target_user.username} »",
            "action_label": "Enregistrer",
        },
    )


@login_required(login_url="core:login")
@admin_required
@require_POST
def user_toggle_active(request, pk):
    """
    Activate or deactivate a user account (POST only, CSRF-protected).
    An admin cannot deactivate their own account.
    """
    target_user = get_object_or_404(User, pk=pk)

    if target_user == request.user:
        messages.error(
            request,
            "Vous ne pouvez pas désactiver votre propre compte.",
        )
        return redirect("core:user_list")

    target_user.is_active = not target_user.is_active
    target_user.save(update_fields=["is_active"])

    if target_user.is_active:
        messages.success(
            request,
            f"Le compte de « {target_user.username} » a été activé.",
        )
        logger.info(
            "User '%s' activated by '%s'.",
            target_user.username,
            request.user.username,
        )
    else:
        messages.warning(
            request,
            f"Le compte de « {target_user.username} » a été désactivé.",
        )
        logger.info(
            "User '%s' deactivated by '%s'.",
            target_user.username,
            request.user.username,
        )

    return redirect("core:user_list")


@login_required(login_url="core:login")
def user_password_change(request, pk):
    """
    Change a user's password.

    Admins may change any user's password.
    Non-admins may only change their own password, and must supply the
    current password (Django's built-in PasswordChangeForm).

    POST-Redirect-Get: on success the session is updated to prevent logout
    (for self-change) and a success message is displayed.
    """
    target_user = get_object_or_404(User, pk=pk)

    # Permission gate: only admins or the user themselves.
    if not (is_admin(request.user) or request.user == target_user):
        messages.error(
            request,
            "Vous n'avez pas la permission de modifier ce mot de passe.",
        )
        return redirect("core:dashboard")

    if request.method == "POST":
        form = PasswordChangeForm(user=target_user, data=request.POST)
        if form.is_valid():
            form.save()
            # Keep the requesting user's session alive if they changed their own password.
            if request.user == target_user:
                update_session_auth_hash(request, target_user)
            messages.success(
                request,
                f"Le mot de passe de « {target_user.username} » a été modifié avec succès.",
            )
            logger.info(
                "Password changed for user '%s' by '%s'.",
                target_user.username,
                request.user.username,
            )
            return redirect(
                "core:user_list" if is_admin(request.user) else "core:dashboard"
            )
        else:
            messages.error(request, "Veuillez corriger les erreurs dans le formulaire.")
    else:
        form = PasswordChangeForm(user=target_user)

    return render(
        request,
        "core/password_change.html",
        {
            "form": form,
            "target_user": target_user,
            "title": f"Changer le mot de passe — {target_user.username}",
        },
    )


@login_required(login_url="core:login")
def own_password_change(request):
    """
    Shortcut for an authenticated user to change their own password.
    Delegates to user_password_change with the current user's pk.
    """
    return user_password_change(request, pk=request.user.pk)


@login_required(login_url="core:login")
def profile_view(request):
    """
    Authenticated user views and edits their own profile (name, email, phone).
    Role is NOT editable by the user themselves — admin-only via user_edit.
    Password change is a separate link.
    """
    target_user = request.user

    if request.method == "POST":
        form = UserUpdateForm(request.POST, instance=target_user)
        if form.is_valid():
            # Preserve the existing role — prevent self-escalation.
            saved_user = form.save(commit=False)
            saved_user.save()
            try:
                profile = saved_user.profile
                profile.telephone = form.cleaned_data.get("telephone", "")
                profile.notes = form.cleaned_data.get("notes", "")
                # Role is not changed here.
                profile.save(update_fields=["telephone", "notes", "updated_at"])
            except UserProfile.DoesNotExist:
                pass
            messages.success(request, "Votre profil a été mis à jour.")
            return redirect("core:profile")
        else:
            messages.error(request, "Veuillez corriger les erreurs dans le formulaire.")
    else:
        form = UserUpdateForm(instance=target_user)
        # Disable role field for non-admins.
        if not is_admin(request.user):
            form.fields["role"].disabled = True

    return render(
        request,
        "core/profile.html",
        {
            "form": form,
            "target_user": target_user,
            "title": "Mon profil",
        },
    )
