from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def param_replace(context, **kwargs):
    """
    Return the current query string with the given parameters replaced.
    Used for pagination while preserving filter state.
    """
    request = context['request']
    querydict = request.GET.copy()
    for key, val in kwargs.items():
        querydict[key] = val
    for key in list(querydict.keys()):
        if querydict[key] == '' or querydict[key] is None:
            del querydict[key]
    return querydict.urlencode()


@register.filter
def get_item(dictionary, key):
    """Get an item from a dictionary by key."""
    if dictionary is None:
        return None
    try:
        return dictionary.get(key)
    except (AttributeError, TypeError):
        return None


@register.filter
def div(value, arg):
    """Safe division filter."""
    try:
        return float(value) / float(arg)
    except (ValueError, ZeroDivisionError, TypeError):
        return 0
