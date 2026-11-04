from .models import AuditLog


def model_snapshot(instance, exclude=("password",)):
    """Plain dict of a model's field values, safe to store as JSON."""
    data = {}
    for field in instance._meta.concrete_fields:
        if field.name in exclude:
            continue
        value = getattr(instance, field.attname)
        if not isinstance(value, (str, int, float, bool, type(None))):
            value = str(value)
        data[field.name] = value
    return data


def record(actor, action, entity, entity_id="", before=None, after=None):
    """Write one audit entry. actor is request.user (or None for system actions)."""
    is_user = bool(getattr(actor, "is_authenticated", False))
    AuditLog.objects.create(
        actor=actor if is_user else None,
        actor_email=(getattr(actor, "email", "") or "") if is_user else "",
        action=action,
        entity=entity,
        entity_id=str(entity_id),
        before=before,
        after=after,
    )
