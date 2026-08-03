from django.shortcuts import render
from django_ratelimit.exceptions import Ratelimited


class RatelimitTo429Middleware:
    """Render rate-limit rejections as 429 rather than 403.

    `Ratelimited` subclasses `PermissionDenied`, so Django would otherwise
    return 403 -- the same status as an authorization denial. Keeping them
    distinct is what lets logs and monitoring tell "wrong role" apart from
    "too many requests".
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        if isinstance(exception, Ratelimited):
            # The body used to be the bare string `Too many requests.`, served
            # as text/html: English in a Spanish app, no layout, no way back.
            # The template extends base_v2 rather than base_shell_v2 because
            # some limits are keyed by IP, so an anonymous caller reaches this
            # and the shell's nav branches on a role it would not have.
            return render(request, 'too_many_requests.html', status=429)
        return None
