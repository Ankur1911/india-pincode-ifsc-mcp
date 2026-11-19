import json

import httpx
import pytest
import respx
from mcp import Client

from india_pincode_ifsc_mcp import server, tools
from india_pincode_ifsc_mcp.banks import resolve_bank
from india_pincode_ifsc_mcp.client import ApiError

PIN = "https://api.postalpincode.in"
IFSC = "https://ifsc.razorpay.com"


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setenv("INDIA_LOOKUP_RETRY_DELAY", "0")
    for name in ("PINCODE_BASE_URL", "IFSC_BASE_URL", "INDIA_LOOKUP_MAX_CHARS"):
        monkeypatch.delenv(name, raising=False)


async def call(tool, args=None):
    async with Client(server.mcp) as c:
        return await c.call_tool(tool, args or {})


def text(result):
    return "".join(x.text for x in result.content)


def data(result):
    return json.loads(text(result))


def office(name="Connaught Place", pincode="110001", kind="Sub Post Office", delivery="Non-Delivery", **over):
    base = {
        "Name": name, "Description": None, "BranchType": kind, "DeliveryStatus": delivery, "Circle": "Delhi",
        "District": "Central Delhi", "Division": "New Delhi Central", "Region": "Delhi", "Block": "New Delhi",
        "State": "Delhi", "Country": "India", "Pincode": pincode,
    }
    base.update(over)
    return base


def pin_payload(*offices):
    return [{"Message": f"Number of pincode(s) found:{len(offices)}", "Status": "Success", "PostOffice": list(offices)}]


NOT_FOUND = [{"Message": "No records found", "Status": "Error", "PostOffice": None}]


def branch(ifsc="HDFC0001069", **over):
    base = {
        "MICR": "110240133", "BRANCH": "SAROJINI NAGAR", "ADDRESS": "HDFC BANK LTD SHOP 85  SAROJINI NAGAR MARKET NEW DELHI DELHI 110023",
        "STATE": "DELHI", "CONTACT": "+911161606161", "UPI": True, "RTGS": True, "CITY": "DELHI", "CENTRE": "DELHI",
        "DISTRICT": "DELHI", "NEFT": True, "IMPS": False, "SWIFT": "HDFCINBB", "ISO3166": "IN-DL", "BANK": "HDFC Bank",
        "BANKCODE": "HDFC", "IFSC": ifsc,
    }
    base.update(over)
    return base


# ------------------------------------------------------------------ banks

def test_resolve_bank():
    assert resolve_bank("HDFC Bank") == "HDFC"
    assert resolve_bank("hdfc") == "HDFC"
    assert resolve_bank("SBI") == "SBIN"
    assert resolve_bank("State Bank of India") == "SBIN"
    assert resolve_bank("Axis") == "UTIB"
    assert resolve_bank("idbi") == "IBKL"
    assert resolve_bank("Punjab National Bank") == "PUNB"
    assert resolve_bank("zzzz") == "ZZZZ"  # four letters is treated as a code
    assert resolve_bank("Some Unknown Cooperative Bank") is None
    assert resolve_bank("  ") is None


# ---------------------------------------------------------------- pincodes

@respx.mock
async def test_lookup_pincodes_groups_results():
    respx.get(f"{PIN}/pincode/110001").mock(return_value=httpx.Response(200, json=pin_payload(
        office(), office("New Delhi ", kind="Head Post Office", delivery="Delivery", District="New Delhi"))))
    respx.get(f"{PIN}/pincode/999999").mock(return_value=httpx.Response(200, json=NOT_FOUND))
    out = data(await call("lookup_pincodes", {"pincodes": ["110001", "1100 01", "999999", "12345", "0123456", "abcdef"]}))
    assert [f["pincode"] for f in out["found"]] == ["110001"]
    hit = out["found"][0]
    assert hit["state"] == "Delhi" and hit["district"] == ["Central Delhi", "New Delhi"]
    assert hit["post_office_count"] == 2 and hit["delivery_offices"] == 1
    assert hit["post_offices"] == ["Connaught Place [SO, no delivery]", "New Delhi  [HO]"]
    assert out["not_found"] == ["999999"]
    assert out["invalid_format"] == ["12345", "0123456", "abcdef"]


@respx.mock
async def test_lookup_pincodes_full_and_truncation():
    many = [office(f"Office {i}") for i in range(45)]
    respx.get(f"{PIN}/pincode/110001").mock(return_value=httpx.Response(200, json=pin_payload(*many)))
    summary = data(await call("lookup_pincodes", {"pincodes": ["110001"]}))["found"][0]
    assert len(summary["post_offices"]) == tools.OFFICE_CAP
    assert "showing 40 of 45" in summary["post_offices_truncated"]
    full = data(await call("lookup_pincodes", {"pincodes": ["110001"], "detail": "full"}))["found"][0]
    assert len(full["post_offices"]) == 45
    assert full["post_offices"][0]["BranchType"] == "Sub Post Office"


@respx.mock
async def test_lookup_pincodes_only_invalid_sends_no_request():
    out = data(await call("lookup_pincodes", {"pincodes": ["abc"]}))
    assert out["found"] == [] and out["invalid_format"] == ["abc"]
    assert len(respx.calls) == 0


async def test_lookup_pincodes_too_many():
    pins = [str(100000 + i) for i in range(26)]
    with pytest.raises(ApiError):
        await tools.lookup_pincodes(server.client, pins)


@respx.mock
async def test_search_post_offices():
    respx.get(f"{PIN}/postoffice/Gandhi%20Nagar").mock(return_value=httpx.Response(200, json=pin_payload(
        office("Gandhi Nagar (Agra)", "282003", District="Agra", State="Uttar Pradesh"),
        office("Gandhi Nagar (Bhopal)", "462036", delivery="Delivery", District="Bhopal", State="Madhya Pradesh"),
        office("Gandhi Nagar (Jammu)", "180004", District="Jammu", State="Jammu & Kashmir"))))
    out = data(await call("search_post_offices", {"name": "Gandhi Nagar", "limit": 2}))
    assert out["total"] == 3 and len(out["post_offices"]) == 2
    assert out["post_offices"][1] == {
        "name": "Gandhi Nagar (Bhopal)", "pincode": "462036", "type": "SO", "delivers": True,
        "district": "Bhopal", "state": "Madhya Pradesh",
    }
    assert "Showing 2 of 3" in out["note"]


@respx.mock
async def test_search_post_offices_no_match_and_short_name():
    respx.get(f"{PIN}/postoffice/Nowhere").mock(return_value=httpx.Response(200, json=NOT_FOUND))
    out = data(await call("search_post_offices", {"name": "Nowhere"}))
    assert out["total"] == 0 and "No post office matched" in out["note"]
    short = await call_error("search_post_offices", {"name": "ab"})
    assert short


async def call_error(tool, args):
    try:
        res = await call(tool, args)
    except Exception as exc:  # argument validation raises before the tool runs
        return str(exc)
    assert res.is_error
    return text(res)


# -------------------------------------------------------------------- IFSC

@respx.mock
async def test_lookup_ifsc_normalises_and_buckets():
    respx.get(f"{IFSC}/HDFC0001069").mock(return_value=httpx.Response(200, json=branch()))
    respx.get(f"{IFSC}/ABCD0000000").mock(return_value=httpx.Response(404, json="Not Found"))
    out = data(await call("lookup_ifsc", {"codes": ["hdfc 0001069", "HDFC0001069", "ABCD0000000", "HDFC1001069", "short"]}))
    assert len(out["found"]) == 1
    row = out["found"][0]
    assert row["ifsc"] == "HDFC0001069" and row["bank"] == "HDFC Bank"
    assert row["services"] == ["NEFT", "RTGS", "UPI"]  # IMPS false is dropped
    assert row["pincode_in_address"] == "110023"
    assert "centre" not in row and "postal_area" not in row
    assert "  " not in row["address"]
    assert out["not_found"] == ["ABCD0000000"]
    assert out["invalid_format"] == ["HDFC1001069", "short"]


@respx.mock
async def test_lookup_ifsc_with_postal_area():
    respx.get(f"{IFSC}/HDFC0001069").mock(return_value=httpx.Response(200, json=branch()))
    respx.get(f"{PIN}/pincode/110023").mock(return_value=httpx.Response(200, json=pin_payload(
        office("Sarojini Nagar", "110023", District="South West Delhi"))))
    out = data(await call("lookup_ifsc", {"codes": ["HDFC0001069"], "with_postal_area": True}))
    area = out["found"][0]["postal_area"]
    assert area["district"] == "South West Delhi" and area["post_offices"] == ["Sarojini Nagar"]


@respx.mock
async def test_lookup_ifsc_postal_area_skipped_without_pin_in_address():
    respx.get(f"{IFSC}/HDFC0001069").mock(return_value=httpx.Response(200, json=branch(ADDRESS="MAIN ROAD")))
    out = data(await call("lookup_ifsc", {"codes": ["HDFC0001069"], "with_postal_area": True}))
    assert "pincode_in_address" not in out["found"][0]
    assert len(respx.calls) == 1


def test_pin_from_address():
    assert tools.pin_from_address("X ROAD, MUMBAI 400059") == "400059"
    assert tools.pin_from_address("PLOT 123456789 MUMBAI") is None
    assert tools.pin_from_address("PIN 110001 AND 110002") == "110002"
    assert tools.pin_from_address(None) is None


@respx.mock
async def test_search_ifsc_resolves_bank_and_pages():
    route = respx.get(f"{IFSC}/search").mock(return_value=httpx.Response(
        200, json={"data": [branch("HDFC0000001"), branch("HDFC0000002", CENTRE="FORT")], "hasNext": True, "count": 19}))
    out = data(await call("search_ifsc", {"bank": "HDFC Bank", "city": "mumbai", "limit": 2, "offset": 4}))
    params = dict(route.calls.last.request.url.params)
    assert params == {"bankcode": "HDFC", "city": "MUMBAI", "limit": "2", "offset": "4"}
    assert out["total"] == 19 and out["returned"] == 2 and out["next_offset"] == 6
    assert out["filters"] == {"bankcode": "HDFC", "city": "MUMBAI"}
    assert out["branches"][1]["centre"] == "FORT"


@respx.mock
async def test_search_ifsc_last_page_and_empty():
    respx.get(f"{IFSC}/search").mock(return_value=httpx.Response(200, json={"data": [], "hasNext": False, "count": 0}))
    out = data(await call("search_ifsc", {"city": "Nowhereville"}))
    assert out["branches"] == [] and "next_offset" not in out and "matches city and branch names exactly" in out["note"]


async def test_search_ifsc_requires_filter_and_known_bank():
    assert "at least one" in await call_error("search_ifsc", {})
    assert "Could not tell which bank" in await call_error("search_ifsc", {"bank": "Some Unknown Cooperative Bank"})


async def test_list_bank_codes():
    allbanks = data(await call("list_bank_codes"))
    assert allbanks["count"] == len(allbanks["banks"]) > 40
    sbi = data(await call("list_bank_codes", {"query": "state bank"}))
    assert sbi["banks"] == [{"code": "SBIN", "bank": "State Bank of India"}]


# ------------------------------------------------------------ client/server

@respx.mock
async def test_retries_then_succeeds():
    route = respx.get(f"{PIN}/pincode/110001").mock(side_effect=[
        httpx.Response(503), httpx.Response(429, headers={"retry-after": "0"}), httpx.Response(200, json=pin_payload(office()))])
    out = data(await call("lookup_pincodes", {"pincodes": ["110001"]}))
    assert route.call_count == 3 and out["found"][0]["pincode"] == "110001"


@respx.mock
async def test_persistent_failure_is_a_tool_error():
    respx.get(f"{PIN}/pincode/110001").mock(return_value=httpx.Response(503))
    msg = await call_error("lookup_pincodes", {"pincodes": ["110001"]})
    assert "HTTP 503" in msg


@respx.mock
async def test_rate_limit_message_and_timeout():
    respx.get(f"{IFSC}/HDFC0001069").mock(return_value=httpx.Response(429))
    assert "rate limited" in await call_error("lookup_ifsc", {"codes": ["HDFC0001069"]})
    respx.get(f"{IFSC}/SBIN0000691").mock(side_effect=httpx.ConnectTimeout("slow"))
    assert "timed out" in await call_error("lookup_ifsc", {"codes": ["SBIN0000691"]})


@respx.mock
async def test_non_json_response():
    respx.get(f"{IFSC}/HDFC0001069").mock(return_value=httpx.Response(200, text="<html>oops</html>"))
    assert "non-JSON" in await call_error("lookup_ifsc", {"codes": ["HDFC0001069"]})


@respx.mock
async def test_base_url_override(monkeypatch):
    monkeypatch.setenv("IFSC_BASE_URL", "http://localhost:9000/")
    respx.get("http://localhost:9000/HDFC0001069").mock(return_value=httpx.Response(200, json=branch()))
    out = data(await call("lookup_ifsc", {"codes": ["HDFC0001069"]}))
    assert out["found"][0]["ifsc"] == "HDFC0001069"


@respx.mock
async def test_output_truncation(monkeypatch):
    monkeypatch.setenv("INDIA_LOOKUP_MAX_CHARS", "300")
    respx.get(f"{PIN}/pincode/110001").mock(return_value=httpx.Response(200, json=pin_payload(*[office(f"Office {i}") for i in range(30)])))
    assert "[truncated" in text(await call("lookup_pincodes", {"pincodes": ["110001"]}))


async def test_tools_are_read_only_and_described():
    async with Client(server.mcp) as c:
        listed = (await c.list_tools()).tools
    assert [t.name for t in listed] == ["lookup_pincodes", "search_post_offices", "lookup_ifsc", "search_ifsc", "list_bank_codes"]
    for t in listed:
        assert t.annotations.read_only_hint is True
        assert len(t.description) > 100
