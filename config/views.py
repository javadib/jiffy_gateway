import time

from django.http import JsonResponse

_start_time = time.monotonic()


def meta(request):
    return JsonResponse({
        "version": "0.1.0",
        "time": round(time.monotonic() - _start_time, 2),
    })
