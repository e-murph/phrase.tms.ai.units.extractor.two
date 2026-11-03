"""Exact JSON/CSV numerics: do not route Phrase counts through binary floats."""

from decimal import Decimal, localcontext
import json
import math


def number(value):
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if type(value) is int:
        return value
    if type(value) is float and math.isfinite(value):
        return value
    return None


def decimal(value):
    return Decimal(str(value)) if number(value) is not None else None


def total(values):
    values = list(values)
    if not values or any(decimal(v) is None for v in values):
        return None
    numbers = [decimal(v) for v in values]
    # Accommodate large exponents, fractional scales, and carry digits exactly.
    upper = max(v.adjusted() for v in numbers)
    lower = min(v.as_tuple().exponent for v in numbers)
    with localcontext() as context:
        context.prec = max(50, upper - lower + len(str(len(numbers))) + 4)
        return sum(numbers, Decimal(0))


def difference(raw, net):
    return total((raw, decimal(net).copy_negate())) if decimal(net) is not None else None


def percentage(saved, gross):
    if decimal(saved) is None or decimal(gross) is None or gross <= 0:
        return None
    with localcontext() as context:
        context.prec = max(50, len(decimal(saved).as_tuple().digits) + len(decimal(gross).as_tuple().digits) + 20)
        return decimal(saved) * 100 / decimal(gross)


def dumps(value):
    """Encode Decimal as a JSON number, retaining every original decimal digit."""
    if value is None or isinstance(value, (str, bool, int, float)):
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Nonfinite decimal cannot be exported as JSON.")
        return str(value)
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("JSON object keys must be strings.")
        return "{" + ",".join(dumps(key) + ":" + dumps(value[key]) for key in sorted(value)) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(dumps(item) for item in value) + "]"
    raise TypeError("Unsupported JSON value type.")
