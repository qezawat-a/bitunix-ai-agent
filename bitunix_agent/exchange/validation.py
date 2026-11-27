"""Validate and normalize request parameters against the generated official spec, before anything is signed."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from .constants import MARGIN_MODE_ALIASES, POSITION_MODE_ALIASES, STOP_TYPE_ALIASES
from .errors import ValidationError
from .spec_generated import SPEC

_STOP_FIELDS = {"tpStopType", "slStopType"}


def fmt_decimal(v: Any) -> str:
    """Number -> plain decimal string (never scientific notation, no trailing zeros)."""
    if isinstance(v, bool):
        raise TypeError("bool is not a number")
    if isinstance(v, str):
        s = v.strip()
        try:
            d = Decimal(s)
        except InvalidOperation:
            return s  # non-numeric string (ids, symbols) pass through
    else:
        d = Decimal(str(v))
    s = format(d.normalize(), "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def _alias(name: str, value: Any, enum: list[str] | None) -> Any:
    if not isinstance(value, str):
        return value
    v = value.strip()
    if name.split(".")[-1] in _STOP_FIELDS:
        return STOP_TYPE_ALIASES.get(v.upper(), v.upper())
    if name.split(".")[-1] == "marginMode":
        return MARGIN_MODE_ALIASES.get(v.upper(), v.upper())
    if name.split(".")[-1] == "positionMode" and enum:
        return POSITION_MODE_ALIASES.get(v.upper(), v.upper())
    if enum:
        if v in enum:
            return v
        up = v.upper()
        for e in enum:
            if e.upper() == up:
                return e
    return v


def _coerce(p: dict, value: Any, path: str, problems: list[str]) -> Any:
    t = p["type"]
    enum = p.get("enum")
    value = _alias(path, value, enum)
    try:
        if t == "string":
            if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
                value = fmt_decimal(value)
            elif isinstance(value, str):
                name = p["name"]
                if name in ("qty", "price", "amount", "tpPrice", "slPrice", "tpOrderPrice", "slOrderPrice", "tpQty", "slQty"):
                    value = fmt_decimal(value)
                    Decimal(value)
                if name == "symbol" or name == "marginCoin":
                    value = value.upper().replace("/", "").replace("-", "").replace("_", "")
                if name == "symbols":
                    value = ",".join(s.strip().upper().replace("/", "") for s in value.split(",") if s.strip())
            elif isinstance(value, (list, tuple)) and p["name"] == "symbols":
                value = ",".join(str(s).upper() for s in value)
            else:
                value = str(value)
        elif t == "int":
            if isinstance(value, bool):
                raise ValueError
            value = int(Decimal(str(value)))
        elif t == "bool":
            if isinstance(value, str):
                if value.lower() not in ("true", "false", "1", "0", "yes", "no"):
                    raise ValueError
                value = value.lower() in ("true", "1", "yes")
            else:
                value = bool(value)
        elif t == "list":
            if not isinstance(value, (list, tuple)):
                raise ValueError
            out = []
            for i, item in enumerate(value):
                if not isinstance(item, dict):
                    problems.append(f"{path}[{i}] must be an object")
                    continue
                out.append(_validate_obj(p.get("children", []), item, f"{path}[{i}]", problems))
            value = out
    except (ValueError, InvalidOperation, TypeError):
        problems.append(f"{path} must be of type {t}, got {value!r}")
        return value
    if enum and value not in enum:
        problems.append(f"{path}={value!r} not allowed; use one of {enum}")
    return value


def _validate_obj(params: list[dict], given: dict, prefix: str, problems: list[str]) -> dict:
    by_name = {p["name"]: p for p in params}
    out: dict[str, Any] = {}
    for k, v in given.items():
        if v is None:
            continue
        if k not in by_name:
            problems.append(f"unknown parameter {prefix + '.' if prefix else ''}{k} (allowed: {sorted(by_name)})")
            continue
        out[k] = _coerce(by_name[k], v, f"{prefix + '.' if prefix else ''}{k}", problems)
    for p in params:
        if p["required"] and p["name"] not in out:
            problems.append(f"missing required {prefix + '.' if prefix else ''}{p['name']}")
    return out


def _get(obj: dict, dotted: str):
    """Returns list of (container, value) for dotted paths through lists (orderList.price)."""
    head, _, rest = dotted.partition(".")
    if not rest:
        return [(obj, obj.get(head))]
    sub = obj.get(head)
    res = []
    if isinstance(sub, list):
        for it in sub:
            res += _get(it, rest)
    elif isinstance(sub, dict):
        res += _get(sub, rest)
    return res


def _check_rules(rules: list[dict], obj: dict, problems: list[str], position_mode: str | None):
    for r in rules:
        kind = r["rule"]
        if kind == "required_if":
            field = r["field"]
            (wk, wv), = r["when"].items()
            # evaluate per list item when both live in the same list
            if "." in field:
                lst, _, leaf = field.partition(".")
                wleaf = wk.partition(".")[2]
                for i, it in enumerate(obj.get(lst) or []):
                    if it.get(wleaf) == wv and it.get(leaf) in (None, ""):
                        problems.append(f"{lst}[{i}].{leaf} is required when {wleaf}={wv}")
            elif obj.get(wk) == wv and obj.get(field) in (None, ""):
                problems.append(f"{field} is required when {wk}={wv}")
        elif kind == "one_of":
            fields = r["fields"]
            if "." in fields[0]:
                lst = fields[0].partition(".")[0]
                leaves = [f.partition(".")[2] for f in fields]
                for i, it in enumerate(obj.get(lst) or []):
                    if not any(it.get(l) for l in leaves):
                        problems.append(f"{lst}[{i}] needs one of {leaves}")
            elif not any(obj.get(f) for f in fields):
                problems.append(f"one of {fields} is required")
        elif kind == "any_of":
            if not any(obj.get(f) not in (None, "") for f in r["fields"]):
                problems.append(f"at least one of {r['fields']} is required")
        elif kind == "max_len":
            v = obj.get(r["field"])
            if isinstance(v, list) and len(v) > r["value"]:
                problems.append(f"{r['field']} has {len(v)} items; maximum is {r['value']}")
            if isinstance(v, list) and len(v) == 0:
                problems.append(f"{r['field']} must not be empty")
        elif kind == "max":
            v = obj.get(r["field"])
            if v is not None and int(v) > r["value"]:
                problems.append(f"{r['field']}={v} exceeds maximum {r['value']}")
        elif kind == "max_csv":
            v = obj.get(r["field"])
            if v and len(str(v).split(",")) > r["value"]:
                problems.append(f"{r['field']} has more than {r['value']} entries")
        elif kind == "hedge_required":
            if position_mode == "HEDGE":
                field = r["field"]
                if "." in field:
                    lst, _, leaf = field.partition(".")
                    for i, it in enumerate(obj.get(lst) or []):
                        if not it.get(leaf):
                            problems.append(f"{lst}[{i}].{leaf} is required in HEDGE mode (OPEN or CLOSE)")
                elif not obj.get(field):
                    problems.append(f"{field} is required in HEDGE mode (OPEN or CLOSE)")


def validate(key: str, params: dict | None, *, position_mode: str | None = None) -> dict:
    """Returns the normalized params or raises ValidationError listing every problem at once."""
    if key not in SPEC:
        raise ValidationError(key, [f"unknown endpoint {key!r}"])
    ep = SPEC[key]
    problems: list[str] = []
    out = _validate_obj(ep["params"], dict(params or {}), "", problems)
    _check_rules(ep["rules"], out, problems, position_mode)
    if problems:
        raise ValidationError(key, problems)
    return out
