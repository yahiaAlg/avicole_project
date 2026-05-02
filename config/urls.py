from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = (
    [
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
    ]
    + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    + static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
)
