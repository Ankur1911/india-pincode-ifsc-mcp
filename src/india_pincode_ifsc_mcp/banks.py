"""Bank name to IFSC bank-code table for the larger banks. Every code was checked against the IFSC service."""

from __future__ import annotations

import re

BANKS: dict[str, str] = {
    "SBIN": "State Bank of India",
    "HDFC": "HDFC Bank",
    "ICIC": "ICICI Bank",
    "UTIB": "Axis Bank",
    "KKBK": "Kotak Mahindra Bank",
    "PUNB": "Punjab National Bank",
    "BARB": "Bank of Baroda",
    "CNRB": "Canara Bank",
    "BKID": "Bank of India",
    "UBIN": "Union Bank of India",
    "IDIB": "Indian Bank",
    "IOBA": "Indian Overseas Bank",
    "MAHB": "Bank of Maharashtra",
    "CBIN": "Central Bank of India",
    "UCBA": "UCO Bank",
    "PSIB": "Punjab & Sind Bank",
    "YESB": "Yes Bank",
    "INDB": "IndusInd Bank",
    "IDFB": "IDFC FIRST Bank",
    "FDRL": "Federal Bank",
    "SIBL": "South Indian Bank",
    "KVBL": "Karur Vysya Bank",
    "RATN": "RBL Bank",
    "BDBL": "Bandhan Bank",
    "AUBL": "AU Small Finance Bank",
    "CIUB": "City Union Bank",
    "TMBL": "Tamilnad Mercantile Bank",
    "KARB": "Karnataka Bank",
    "DLXB": "Dhanlaxmi Bank",
    "JAKA": "Jammu and Kashmir Bank",
    "CSBK": "CSB Bank (Catholic Syrian Bank)",
    "IBKL": "IDBI Bank",
    "SCBL": "Standard Chartered Bank",
    "HSBC": "HSBC",
    "CITI": "Citibank",
    "DBSS": "DBS Bank",
    "PYTM": "Paytm Payments Bank",
    "AIRP": "Airtel Payments Bank",
    "IPOS": "India Post Payments Bank",
    "ESFB": "Equitas Small Finance Bank",
    "FINF": "Fincare Small Finance Bank",
    "DCBL": "DCB Bank",
    "NESF": "North East Small Finance Bank",
    "JSFB": "Jana Small Finance Bank",
}

ALIASES: dict[str, str] = {
    "sbi": "SBIN",
    "icici": "ICIC",
    "axis": "UTIB",
    "kotak": "KKBK",
    "pnb": "PUNB",
    "bob": "BARB",
    "baroda": "BARB",
    "canara": "CNRB",
    "boi": "BKID",
    "union": "UBIN",
    "indian": "IDIB",
    "iob": "IOBA",
    "bom": "MAHB",
    "maharashtra": "MAHB",
    "central": "CBIN",
    "uco": "UCBA",
    "yes": "YESB",
    "indusind": "INDB",
    "idfc": "IDFB",
    "idfc first": "IDFB",
    "federal": "FDRL",
    "rbl": "RATN",
    "bandhan": "BDBL",
    "au": "AUBL",
    "au small finance": "AUBL",
    "karur vysya": "KVBL",
    "jk": "JAKA",
    "j&k": "JAKA",
    "jammu and kashmir": "JAKA",
    "csb": "CSBK",
    "idbi": "IBKL",
    "standard chartered": "SCBL",
    "dbs": "DBSS",
    "paytm": "PYTM",
    "airtel": "AIRP",
    "india post": "IPOS",
    "ippb": "IPOS",
    "equitas": "ESFB",
    "fincare": "FINF",
    "dcb": "DCBL",
    "jana": "JSFB",
}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9& ]", " ", text.lower())).strip()


_BY_NAME: dict[str, str] = {_norm(name): code for code, name in BANKS.items()}
for _code, _name in BANKS.items():
    short = re.sub(r"\s*\(.*\)", "", _name)
    _BY_NAME[_norm(short)] = _code
    _BY_NAME[_norm(re.sub(r"\bbank\b", "", short))] = _code
_BY_NAME.pop("", None)


def resolve_bank(value: str) -> str | None:
    """Return the 4-letter IFSC bank code for a code or a well-known bank name, or None if it cannot be resolved."""
    raw = value.strip()
    key = _norm(raw)
    if not key:
        return None
    if key in ALIASES:
        return ALIASES[key]
    if key in _BY_NAME:
        return _BY_NAME[key]
    if re.fullmatch(r"[A-Za-z]{4}", raw):
        return raw.upper()
    unique = {code for name, code in _BY_NAME.items() if key in name}
    if len(unique) == 1:
        return unique.pop()
    return None
