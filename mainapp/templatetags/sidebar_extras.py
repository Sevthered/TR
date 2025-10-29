from django import template

register = template.Library()


@register.inclusion_tag('mainapp/sidebar_courses_list.html', takes_context=True)
def sidebar_courses(context):
    """Provide courses grouped by type plus an 'all' list, filtered by school year.

    Import models inside the function to avoid raising ImportError at
    import-time (which prevents Django from registering the tag). This
    helps when the DB driver isn't available during startup.
    """
    try:
        from mainapp.models import Course, School_year
    except Exception:
        # If models can't be imported (e.g. DB driver missing), return empty lists
        return {
            'request': context.get('request'),
            'all_courses': [],
            'eso_courses': [],
            'bach_courses': [],
            'ib_courses': [],
            'selected_school_year': None,
        }

    # Obtiene el año escolar seleccionado del contexto (pasado desde la vista)
    selected_school_year = context.get('selected_school_year')

    if selected_school_year:
        courses = Course.objects.filter(school_year=selected_school_year)
    else:
        # Si no hay año escolar seleccionado, usa el más reciente
        latest_school_year = School_year.objects.all().order_by('-year').first()
        if latest_school_year:
            courses = Course.objects.filter(school_year=latest_school_year)
        else:
            courses = Course.objects.none()

    eso = courses.filter(Tipo='Eso').order_by('Section')
    bach = courses.filter(Tipo='Bachillerato').order_by('Section')
    ib = courses.filter(Tipo='IB').order_by('Section')

    return {
        'request': context.get('request'),
        'all_courses': courses.order_by('Tipo', 'Section'),
        'eso_courses': eso,
        'bach_courses': bach,
        'ib_courses': ib,
        'selected_school_year': selected_school_year,
    }
