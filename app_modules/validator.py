import re
from typing import Optional, List, Dict, Any, Callable, Tuple

_EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")
_URL_REGEX = re.compile(r"^https?://[^\s/$.?#].[^\s]*$")
_PORT_REGEX = re.compile(r"^\d{1,5}$")
_CRON_REGEX = re.compile(r"^(\*|[0-5]?\d)(/(\d+))?(\s+(\*|[01]?\d|2[0-3])(/(\d+))?)?(\s+(\*|[1-9]|[12]\d|3[01])(/(\d+))?)?(\s+(\*|[1-9]|1[0-2])(/(\d+))?)?(\s+(\*|[0-6])(/(\d+))?)?$")

def validate_string(value: Any, min_len: int = 0, max_len: int = 500, allow_empty: bool = True) -> Tuple[bool, str]:
    if not isinstance(value, str):
        return False, "must be string"
    if not allow_empty and not value.strip():
        return False, "cannot be empty"
    if len(value) < min_len:
        return False, "too short (min {})".format(min_len)
    if len(value) > max_len:
        return False, "too long (max {})".format(max_len)
    return True, ""

def validate_url(value: Any, required: bool = True) -> Tuple[bool, str]:
    if not value:
        if required:
            return False, "url is required"
        return True, ""
    if not isinstance(value, str):
        return False, "url must be string"
    if not _URL_REGEX.match(value):
        return False, "invalid url format"
    return True, ""

def validate_port(value: Any) -> Tuple[bool, str]:
    if value is None:
        return False, "port is required"
    if isinstance(value, int):
        if 1 <= value <= 65535:
            return True, ""
        return False, "port must be between 1-65535"
    if isinstance(value, str):
        if not _PORT_REGEX.match(value):
            return False, "invalid port format"
        v = int(value)
        if 1 <= v <= 65535:
            return True, ""
        return False, "port must be between 1-65535"
    return False, "port must be number"

def validate_cron(value: Any, required: bool = False) -> Tuple[bool, str]:
    if not value:
        if required:
            return False, "cron is required"
        return True, ""
    if not isinstance(value, str):
        return False, "cron must be string"
    parts = value.strip().split()
    if len(parts) != 5:
        return False, "cron must have 5 fields"
    return True, ""

def validate_time(value: Any, required: bool = False) -> Tuple[bool, str]:
    if not value:
        if required:
            return False, "time is required"
        return True, ""
    if not isinstance(value, str):
        return False, "time must be string"
    parts = value.split(":")
    if len(parts) != 2 or len(parts[0]) != 2 or len(parts[1]) != 2:
        return False, "time must be HH:MM format"
    try:
        h, m = int(parts[0]), int(parts[1])
        if 0 <= h <= 23 and 0 <= m <= 59:
            return True, ""
        return False, "invalid time (HH:MM)"
    except ValueError:
        return False, "invalid time format"

def validate_positive_int(value: Any, min_val: int = 1, max_val: Optional[int] = None) -> Tuple[bool, str]:
    if value is None:
        return False, "value is required"
    if isinstance(value, str):
        value = value.strip()
        if not value.isdigit():
            return False, "must be positive integer"
        value = int(value)
    if not isinstance(value, int):
        return False, "must be integer"
    if value < min_val:
        return False, "must be >= {}".format(min_val)
    if max_val is not None and value > max_val:
        return False, "must be <= {}".format(max_val)
    return True, ""

def validate_list(value: Any, min_len: int = 0, max_len: int = 1000, item_validator: Optional[Callable[[Any], Tuple[bool, str]]] = None) -> Tuple[bool, str]:
    if not isinstance(value, list):
        return False, "must be list"
    if len(value) < min_len:
        return False, "list too short (min {})".format(min_len)
    if len(value) > max_len:
        return False, "list too long (max {})".format(max_len)
    if item_validator:
        for i, item in enumerate(value):
            ok, msg = item_validator(item)
            if not ok:
                return False, "item {}: {}".format(i, msg)
    return True, ""

def validate_dict(value: Any, required_keys: Optional[List[str]] = None, allowed_keys: Optional[List[str]] = None) -> Tuple[bool, str]:
    if not isinstance(value, dict):
        return False, "must be object"
    if required_keys:
        for k in required_keys:
            if k not in value:
                return False, "missing required key: {}".format(k)
    if allowed_keys:
        for k in value:
            if k not in allowed_keys:
                return False, "unknown key: {}".format(k)
    return True, ""

def validate_task(task: Dict[str, Any]) -> Tuple[bool, str]:
    ok, msg = validate_dict(task, required_keys=["path", "type", "savepath"])
    if not ok:
        return False, msg
    ok, msg = validate_string(task.get("path"), min_len=1, max_len=100)
    if not ok:
        return False, "path: {}".format(msg)
    ok, msg = validate_string(task.get("type"), min_len=1, max_len=50)
    if not ok:
        return False, "type: {}".format(msg)
    ok, msg = validate_string(task.get("savepath"), min_len=1, max_len=500)
    if not ok:
        return False, "savepath: {}".format(msg)
    return True, ""