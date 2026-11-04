from django.core.paginator import Paginator
from django.shortcuts import render

from accounts.permissions import role_required

from .models import AuditLog


@role_required("admin")
def audit_log(request):
    entries = AuditLog.objects.all()
    entity = request.GET.get("entity", "").strip()
    if entity:
        entries = entries.filter(entity=entity)
    page = Paginator(entries, 25).get_page(request.GET.get("page"))
    entities = AuditLog.objects.order_by("entity").values_list("entity", flat=True).distinct()
    return render(request, "audit/log.html", {"page": page, "entities": entities, "entity": entity})
