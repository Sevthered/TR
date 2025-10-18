from django import template

register = template.Library()


@register.inclusion_tag('mainapp/sidebar_courses_list.html', takes_context=True)
def sidebar_courses(context):
    """Provide courses grouped by type plus an 'all' list.

    Import models inside the function to avoid raising ImportError at
    import-time (which prevents Django from registering the tag). This
    helps when the DB driver isn't available during startup.
    """
    try:
        from mainapp.models import Course
    except Exception:
        # If models can't be imported (e.g. DB driver missing), return empty lists
        return {
            'request': context.get('request'),
            'all_courses': [],
            'eso_courses': [],
            'bach_courses': [],
            'ib_courses': [],
        }

    courses = Course.objects.all()
    eso = courses.filter(Tipo='Eso').order_by('Section')
    bach = courses.filter(Tipo='Bachillerato').order_by('Section')
    ib = courses.filter(Tipo='IB').order_by('Section')
    return {
        'request': context.get('request'),
        'all_courses': courses.order_by('Tipo', 'Section'),
        'eso_courses': eso,
        'bach_courses': bach,
        'ib_courses': ib,
    }
