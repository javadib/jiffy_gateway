import time
import tomllib
from pathlib import Path

from django.http import JsonResponse

_start_time = time.monotonic()

__version__ = "0.0.0"
BASE_DIR = Path(__file__).resolve().parent.parent.parent

try:
    with open(BASE_DIR / "pyproject.toml", "rb") as f:
        _pyproject = tomllib.load(f)
        __version__ = _pyproject["project"]["version"]
except (FileNotFoundError, KeyError, tomllib.TOMLDecodeError):
    pass

def meta(request):
    return JsonResponse({
        "version": __version__,
        "time": round(time.monotonic() - _start_time, 2),
    })
