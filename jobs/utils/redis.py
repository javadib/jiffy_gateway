"""Redis utility functions."""
import json
from typing import Any, Dict

import redis
from django.conf import settings

_redis_client = None


def get_redis():
    """Returns a Redis client instance."""
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(settings.REDIS_URL, protocol=2)
    return _redis_client


def load_payload_from_redis(task_id: int) -> Dict[str, Any]:
    """Loads the task payload from Redis."""
    r = get_redis()
    key = f"jiffy:task:{task_id}:payload"
    payload_raw = r.get(key)
    if not payload_raw:
        raise ValueError(f"Payload for task {task_id} not found in Redis or has expired.")
    return json.loads(payload_raw)
