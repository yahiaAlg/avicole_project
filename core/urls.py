"""
core/urls.py

URL patterns for:
  - Authentication      (login, logout)
  - Dashboard           (/)
  - Company Info        (/parametres/entreprise/)
  - User Management     (/parametres/utilisateurs/)
  - My Profile          (/profil/)
  - Multi-branch (v1.4) : branch switcher, Branche CRUD (admin only,
                           BR-BRA-06) (/parametres/branches/)
  - Pièces jointes (v1.5): generic attachment delete, shared by every app
                           (/pieces-jointes/<pk>/supprimer/)
"""

from django.urls import path
from core import views

app_name = "core"

urlpatterns = [
    # ── Authentication ──────────────────────────────────────────────────
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    # ── Dashboard ───────────────────────────────────────────────────────
    path("", views.dashboard, name="dashboard"),
    # ── My Profile ──────────────────────────────────────────────────────
    path("profil/", views.profile_view, name="profile"),
    path(
        "profil/mot-de-passe/",
        views.own_password_change,
        name="own_password_change",
    ),
    # ── Company Information ─────────────────────────────────────────────
    path(
        "parametres/entreprise/",
        views.company_info_view,
        name="company_info",
    ),
    # ── User Management (admin only) ────────────────────────────────────
    path(
        "parametres/utilisateurs/",
        views.user_list,
        name="user_list",
    ),
    path(
        "parametres/utilisateurs/creer/",
        views.user_create,
        name="user_create",
    ),
    path(
        "parametres/utilisateurs/<int:pk>/modifier/",
        views.user_edit,
        name="user_edit",
    ),
    path(
        "parametres/utilisateurs/<int:pk>/activer/",
        views.user_toggle_active,
        name="user_toggle_active",
    ),
    path(
        "parametres/utilisateurs/<int:pk>/mot-de-passe/",
        views.user_password_change,
        name="user_password_change",
    ),
    # ── Multi-branch context (v1.4, §3.5) ───────────────────────────────
    # Branch switcher — admin / unbound comptable only (BR-BRA-03/04)
    path(
        "branche/changer/",
        views.branche_switch,
        name="branche_switch",
    ),
    # Branche CRUD — admin only (BR-BRA-06)
    path(
        "parametres/branches/",
        views.branche_list,
        name="branche_list",
    ),
    path(
        "parametres/branches/creer/",
        views.branche_create,
        name="branche_create",
    ),
    path(
        "parametres/branches/<int:pk>/modifier/",
        views.branche_edit,
        name="branche_edit",
    ),
    # ── Pièces jointes (v1.5) — generic delete shared by every app ──────
    # Every app's detail/edit templates (BL, facture, règlement, dépense,
    # acompte, ...) point their delete buttons at this single POST-only
    # view instead of each app re-implementing attachment deletion.
    path(
        "pieces-jointes/<int:pk>/supprimer/",
        views.piece_jointe_delete,
        name="piece_jointe_delete",
    ),
]
