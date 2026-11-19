"""Task-shaped tools on top of the India Post PIN service and the IFSC service. Each returns plain data."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from .banks import BANKS, resolve_bank
from .client import ApiClient, ApiError, enc

PINCODE_RE = re.compile(r"^[1-9][0-9]{5}$")
IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
ADDRESS_PIN_RE = re.compile(r"(?<![0-9])[1-9][0-9]{5}(?![0-9])")
BULK_MAX = 25
OFFICE_CAP = 40
SERVICES = ("NEFT", "RTGS", "IMPS", "UPI")
TYPE_SHORT = {"head post office": "HO", "sub post office": "SO", "branch post office": "BO"}


def _clean(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if v is not None and v != "" and v != []}


def _unique(values: list[Any]) -> list[Any]:
    return list(dict.fromkeys(v for v in values if v))


def _one_or_many(values: list[Any]) -> Any:
    uniq = _unique(values)
    if not uniq:
        return None
    return uniq[0] if len(uniq) == 1 else uniq


def _type(office: dict[str, Any]) -> str:
    raw = str(office.get("BranchType") or "")
    return TYPE_SHORT.get(raw.lower(), raw)


def _delivers(office: dict[str, Any]) -> bool:
    return str(office.get("DeliveryStatus") or "").strip().lower() == "delivery"


def _office_label(office: dict[str, Any]) -> str:
    return f"{office.get('Name')} [{_type(office)}{'' if _delivers(office) else ', no delivery'}]"


def _offices(payload: Any) -> list[dict[str, Any]]:
    """Post offices from the India Post response; an empty list when the service reports no records."""
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        return []
    return payload[0].get("PostOffice") or []


def pincode_area(offices: list[dict[str, Any]]) -> dict[str, Any]:
    return _clean(
        {
            "state": _one_or_many([o.get("State") for o in offices]),
            "district": _one_or_many([o.get("District") for o in offices]),
            "division": _one_or_many([o.get("Division") for o in offices]),
            "region": _one_or_many([o.get("Region") for o in offices]),
            "circle": _one_or_many([o.get("Circle") for o in offices]),
        }
    )


# ---------------------------------------------------------------- PIN codes

async def _pincode_one(client: ApiClient, pin: str, detail: str) -> dict[str, Any] | None:
    offices = _offices(await client.pincode(f"/pincode/{enc(pin)}"))
    if not offices:
        return None
    out: dict[str, Any] = {"pincode": pin, **pincode_area(offices), "post_office_count": len(offices)}
    out["delivery_offices"] = sum(1 for o in offices if _delivers(o))
    if detail == "full":
        out["post_offices"] = [
            _clean({k: o.get(k) for k in ("Name", "BranchType", "DeliveryStatus", "Block", "Description")}) for o in offices
        ]
    else:
        labels = [_office_label(o) for o in offices[:OFFICE_CAP]]
        out["post_offices"] = labels
        if len(offices) > OFFICE_CAP:
            out["post_offices_truncated"] = f"showing {OFFICE_CAP} of {len(offices)}; use detail='full' for all"
    return out


async def lookup_pincodes(client: ApiClient, pincodes: list[str], detail: str = "summary") -> dict[str, Any]:
    valid: list[str] = []
    invalid: list[str] = []
    for raw in pincodes:
        pin = re.sub(r"\s+", "", str(raw))
        if not pin:
            continue
        if PINCODE_RE.match(pin):
            if pin not in valid:
                valid.append(pin)
        elif raw not in invalid:
            invalid.append(raw)
    if len(valid) > BULK_MAX:
        raise ApiError(f"Too many PIN codes ({len(valid)}). Send at most {BULK_MAX} per call.")
    results = await asyncio.gather(*[_pincode_one(client, p, detail) for p in valid]) if valid else []
    found = [r for r in results if r]
    not_found = [p for p, r in zip(valid, results) if not r]
    out: dict[str, Any] = {"found": found}
    if not_found:
        out["not_found"] = not_found
    if invalid:
        out["invalid_format"] = invalid
        out["note"] = "A PIN code is exactly 6 digits and does not start with 0."
    return out


async def search_post_offices(client: ApiClient, name: str, limit: int) -> dict[str, Any]:
    q = name.strip()
    if len(q) < 3:
        raise ApiError("Give at least 3 characters of a post office, locality or town name.")
    offices = _offices(await client.pincode(f"/postoffice/{enc(q)}"))
    items = [
        _clean(
            {
                "name": o.get("Name"),
                "pincode": o.get("Pincode"),
                "type": _type(o),
                "delivers": _delivers(o),
                "district": o.get("District"),
                "state": o.get("State"),
            }
        )
        for o in offices[:limit]
    ]
    out: dict[str, Any] = {"query": q, "total": len(offices), "post_offices": items}
    if not offices:
        out["note"] = "No post office matched. Try a shorter or differently spelled name (the match is on the post office name)."
    elif len(offices) > limit:
        out["note"] = f"Showing {limit} of {len(offices)}; raise limit or make the name more specific."
    return out


# --------------------------------------------------------------------- IFSC

def pin_from_address(address: str | None) -> str | None:
    """The last standalone 6-digit PIN-like number in a branch address, if any."""
    found = ADDRESS_PIN_RE.findall(address or "")
    return found[-1] if found else None


def branch_view(row: dict[str, Any]) -> dict[str, Any]:
    """A branch record trimmed to useful fields with payment services listed together."""
    centre = row.get("CENTRE")
    out = {
        "ifsc": row.get("IFSC"),
        "bank": row.get("BANK"),
        "branch": row.get("BRANCH"),
        "address": re.sub(r"\s+", " ", row.get("ADDRESS") or "").strip() or None,
        "city": row.get("CITY"),
        "district": row.get("DISTRICT"),
        "centre": centre if centre not in (row.get("CITY"), row.get("DISTRICT")) else None,
        "state": row.get("STATE"),
        "contact": row.get("CONTACT"),
        "micr": row.get("MICR"),
        "swift": row.get("SWIFT"),
        "services": [s for s in SERVICES if row.get(s)],
    }
    return _clean(out)


async def _ifsc_one(client: ApiClient, code: str, with_postal: bool) -> dict[str, Any] | None:
    row = await client.ifsc(f"/{enc(code)}")
    if not isinstance(row, dict) or not row.get("IFSC"):
        return None
    view = branch_view(row)
    pin = pin_from_address(row.get("ADDRESS"))
    if pin:
        view["pincode_in_address"] = pin
        if with_postal:
            offices = _offices(await client.pincode(f"/pincode/{enc(pin)}"))
            if offices:
                view["postal_area"] = {**pincode_area(offices), "post_offices": [o.get("Name") for o in offices[:10]]}
    return view


async def lookup_ifsc(client: ApiClient, codes: list[str], with_postal: bool = False) -> dict[str, Any]:
    valid: list[str] = []
    invalid: list[str] = []
    for raw in codes:
        code = re.sub(r"\s+", "", str(raw)).upper()
        if not code:
            continue
        if IFSC_RE.match(code):
            if code not in valid:
                valid.append(code)
        elif raw not in invalid:
            invalid.append(raw)
    if len(valid) > BULK_MAX:
        raise ApiError(f"Too many IFSC codes ({len(valid)}). Send at most {BULK_MAX} per call.")
    results = await asyncio.gather(*[_ifsc_one(client, c, with_postal) for c in valid]) if valid else []
    out: dict[str, Any] = {"found": [r for r in results if r]}
    not_found = [c for c, r in zip(valid, results) if not r]
    if not_found:
        out["not_found"] = not_found
    if invalid:
        out["invalid_format"] = invalid
        out["note"] = "An IFSC is 11 characters: 4 letters (bank), the digit 0, then 6 letters or digits (branch)."
    return out


async def search_ifsc(
    client: ApiClient, bank: str | None, city: str | None, branch: str | None, limit: int, offset: int
) -> dict[str, Any]:
    params: dict[str, Any] = {}
    resolved = None
    if bank and bank.strip():
        resolved = resolve_bank(bank)
        if not resolved:
            raise ApiError(
                f"Could not tell which bank '{bank}' is. Pass the 4-letter IFSC bank code (the first 4 characters of any "
                "of its IFSC codes) or use list_bank_codes."
            )
        params["bankcode"] = resolved
    if city and city.strip():
        params["city"] = re.sub(r"\s+", " ", city).strip().upper()
    if branch and branch.strip():
        params["branch"] = re.sub(r"\s+", " ", branch).strip().upper()
    if not params:
        raise ApiError("Give at least one of bank, city or branch to search IFSC codes.")
    params["limit"] = limit
    params["offset"] = offset
    payload = await client.ifsc("/search", params)
    if not isinstance(payload, dict):
        payload = {}
    rows = payload.get("data") or []
    total = payload.get("count")
    out: dict[str, Any] = {
        "filters": {k: v for k, v in params.items() if k not in ("limit", "offset")},
        "total": total,
        "returned": len(rows),
        "branches": [branch_view(r) for r in rows],
    }
    if payload.get("hasNext"):
        out["next_offset"] = offset + len(rows)
    if not rows:
        out["note"] = (
            "No branch matched. The IFSC service matches city and branch names exactly (case-insensitive here), "
            "so try the official spelling, e.g. 'BENGALURU URBAN' or 'BANGALORE URBAN' for the same city."
            if total in (0, None)
            else "Offset is past the last result."
        )
    return out


def list_bank_codes(query: str | None) -> dict[str, Any]:
    q = (query or "").strip().lower()
    rows = [{"code": c, "bank": n} for c, n in BANKS.items() if not q or q in n.lower() or q in c.lower()]
    return {
        "count": len(rows),
        "banks": rows,
        "note": "Larger banks only. Any other bank's code is the first 4 characters of its IFSC codes and works in search_ifsc.",
    }
