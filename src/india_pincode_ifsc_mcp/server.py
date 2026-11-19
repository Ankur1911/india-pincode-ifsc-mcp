"""MCP server exposing Indian PIN code and bank IFSC lookups as a small set of task-shaped tools."""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from . import tools
from .client import ApiClient, ApiError

MAX_CHARS = 20000
READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True, idempotent_hint=True)

client = ApiClient()
mcp = MCPServer(
    "india-pincode-ifsc",
    instructions=(
        "Indian address and banking reference data from two free public services: India Post (PIN codes and post offices) "
        "and the Razorpay IFSC dataset (bank branches). Use lookup_pincodes to validate PIN codes and get state, district "
        "and post offices; search_post_offices to find the PIN for a locality name; lookup_ifsc to verify an IFSC and get "
        "the branch, address and payment services (optionally cross-checked with the postal area of its PIN); search_ifsc "
        "to list branches of a bank in a city. India only. Read-only, no credentials."
    ),
)


def render(data: Any) -> str:
    text = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    limit = int(os.environ.get("INDIA_LOOKUP_MAX_CHARS", MAX_CHARS))
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n[truncated: response was {len(text)} characters; send fewer codes or use a smaller limit]"


async def run(coro: Any) -> str:
    try:
        return render(await coro)
    except ApiError as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(
    name="lookup_pincodes",
    description=(
        "Validate and look up 1 to 25 Indian PIN codes in one call. Each match returns state, district, division, region, "
        "postal circle and the post offices under that PIN with their type (HO head, SO sub, BO branch office) and whether "
        "they deliver mail. PINs that are not 6 digits are reported under 'invalid_format' without a request; well-formed "
        "PINs that India Post does not know are under 'not_found'. detail='full' adds each office's block and raw fields. "
        "Use search_post_offices when you only have a locality name."
    ),
    annotations=READ_ONLY,
    structured_output=False,
)
async def lookup_pincodes(
    pincodes: Annotated[list[str], Field(description="PIN codes such as ['110001', '560001'].", min_length=1, max_length=25)],
    detail: Annotated[Literal["summary", "full"], Field(description="'summary' (default) lists office names compactly.")] = "summary",
) -> str:
    return await run(tools.lookup_pincodes(client, pincodes, detail))


@mcp.tool(
    name="search_post_offices",
    description=(
        "Find Indian post offices by name (locality, town or office name) and get each one's PIN code, type, delivery "
        "status, district and state. Names repeat across India (there are many 'Gandhi Nagar'), so check district and state. "
        "Use it to go from a place name to a PIN code; use lookup_pincodes to go from a PIN to its area."
    ),
    annotations=READ_ONLY,
    structured_output=False,
)
async def search_post_offices(
    name: Annotated[str, Field(description="Post office or locality name, at least 3 characters, e.g. 'Connaught Place'.", min_length=3)],
    limit: Annotated[int, Field(description="Maximum post offices returned.", ge=1, le=100)] = 20,
) -> str:
    return await run(tools.search_post_offices(client, name, limit))


@mcp.tool(
    name="lookup_ifsc",
    description=(
        "Verify and look up 1 to 25 bank IFSC codes in one call (any case or spacing). Each match returns bank, branch, "
        "address, city, district, state, phone, MICR, SWIFT and which payment services the branch supports (NEFT, RTGS, "
        "IMPS, UPI), plus the PIN code found in the address. Set with_postal_area=true to also look up that PIN with India "
        "Post and return its district, state and post offices, which is a quick check that the branch address is "
        "consistent. Malformed codes are listed under 'invalid_format' without a request; unknown ones under 'not_found'."
    ),
    annotations=READ_ONLY,
    structured_output=False,
)
async def lookup_ifsc(
    codes: Annotated[list[str], Field(description="IFSC codes such as ['HDFC0001069'].", min_length=1, max_length=25)],
    with_postal_area: Annotated[bool, Field(description="Also resolve the PIN found in each address via India Post.")] = False,
) -> str:
    return await run(tools.lookup_ifsc(client, codes, with_postal_area))


@mcp.tool(
    name="search_ifsc",
    description=(
        "List bank branches and their IFSC codes, filtered by bank, city and/or branch name (at least one filter). 'bank' "
        "accepts a 4-letter bank code or a well-known name such as 'HDFC Bank', 'SBI' or 'Kotak'. City and branch names are "
        "matched exactly as the dataset spells them (for example 'MUMBAI', 'SAROJINI NAGAR'), so an unlisted spelling returns "
        "nothing. Results are paged: when more exist the reply has next_offset to pass back as offset. For one known "
        "IFSC use lookup_ifsc."
    ),
    annotations=READ_ONLY,
    structured_output=False,
)
async def search_ifsc(
    bank: Annotated[str | None, Field(description="Bank code ('HDFC') or name ('Punjab National Bank', 'SBI').")] = None,
    city: Annotated[str | None, Field(description="City exactly as the dataset spells it, e.g. 'PUNE'.")] = None,
    branch: Annotated[str | None, Field(description="Branch name, e.g. 'ANDHERI'.")] = None,
    limit: Annotated[int, Field(description="Branches per page.", ge=1, le=50)] = 10,
    offset: Annotated[int, Field(description="Skip this many results (use next_offset from the previous reply).", ge=0)] = 0,
) -> str:
    return await run(tools.search_ifsc(client, bank, city, branch, limit, offset))


@mcp.tool(
    name="list_bank_codes",
    description=(
        "List the 4-letter IFSC bank codes this server knows by name (about 45 larger banks), optionally filtered by text. "
        "Use it to find the code for search_ifsc; any other bank's code is the first 4 characters of its IFSC codes."
    ),
    annotations=READ_ONLY,
    structured_output=False,
)
async def list_bank_codes(
    query: Annotated[str | None, Field(description="Filter by part of a bank name or code.")] = None,
) -> str:
    return render(tools.list_bank_codes(query))


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=os.environ.get("LOG_LEVEL", "WARNING").upper())
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport == "stdio":
        mcp.run("stdio")
    else:
        mcp.run(transport, host=os.environ.get("MCP_HOST", "127.0.0.1"), port=int(os.environ.get("MCP_PORT", "8000")))


if __name__ == "__main__":
    main()
