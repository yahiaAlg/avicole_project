from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.conf.urls.static import static
from django.views.static import serve

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("core.urls", namespace="core")),
    path("intrants/", include("intrants.urls", namespace="intrants")),
    path("stock/", include("stock.urls", namespace="stock")),
    path("elevage/", include("elevage.urls", namespace="elevage")),
    path("production/", include("production.urls", namespace="production")),
    path("achats/", include("achats.urls", namespace="achats")),
    path("clients/", include("clients.urls", namespace="clients")),
    path("depenses/", include("depenses.urls", namespace="depenses")),
    path("reporting/", include("reporting.urls", namespace="reporting")),
    # Always serve media files (WhiteNoise handles static; media needs explicit routing)
    re_path(r"^media/(?P<path>.*)$", serve, {"document_root": settings.MEDIA_ROOT}),
] + static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
