from django.http import HttpResponse
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
            return HttpResponse('Too many requests.', status=429)
        return None
