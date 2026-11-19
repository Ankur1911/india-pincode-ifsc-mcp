# india-pincode-ifsc-mcp

A [Model Context Protocol](https://modelcontextprotocol.io) server for two free public Indian reference datasets: PIN codes and post offices from India Post (via [postalpincode.in](https://api.postalpincode.in)), and bank branches and IFSC codes from the open [Razorpay IFSC](https://ifsc.razorpay.com) dataset. It needs no API key and is read-only.

The tools are shaped around common jobs rather than one tool per endpoint: bulk validation with malformed and unknown codes sorted into separate buckets (malformed ones are rejected locally, without a request), compact post-office lists, bank-name resolution (`"SBI"`, `"HDFC Bank"`) for branch search with paging, and an optional cross-check that takes the PIN code found in a branch address and resolves it against India Post.

## Setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/Ankur1911/india-pincode-ifsc-mcp && cd india-pincode-ifsc-mcp
uv sync
uv run india-pincode-ifsc-mcp          # stdio transport
```

Add it to any MCP client that launches stdio servers:

```json
{
  "mcpServers": {
    "india-pincode-ifsc": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/india-pincode-ifsc-mcp", "india-pincode-ifsc-mcp"]
    }
  }
}
```

For HTTP instead of stdio: `MCP_TRANSPORT=streamable-http MCP_HOST=127.0.0.1 MCP_PORT=8000 uv run india-pincode-ifsc-mcp`.

## Tools

All tools are read-only.

| Tool | What it returns |
|---|---|
| `lookup_pincodes` | 1 to 25 PIN codes in one call. Per PIN: state, district, division, region, postal circle, and the post offices under it with type (HO head, SO sub, BO branch) and whether they deliver mail. Malformed PINs are returned under `invalid_format` (checked locally); well-formed PINs India Post does not know under `not_found`. `detail="full"` returns every office with block and raw fields. |
| `search_post_offices` | Post offices matching a locality or office name, with PIN code, type, delivery status, district and state. Use it to go from a place name to a PIN. |
| `lookup_ifsc` | 1 to 25 IFSC codes in one call (any case or spacing). Per match: bank, branch, address, city, district, state, phone, MICR, SWIFT, supported services (NEFT, RTGS, IMPS, UPI) and the PIN code found in the address. `with_postal_area=true` also resolves that PIN with India Post (district, state, post offices) as a consistency check. Malformed codes go under `invalid_format`, unknown ones under `not_found`. |
| `search_ifsc` | Branches filtered by bank (4-letter code or a well-known name), city and/or branch name, with paging (`next_offset`). |
| `list_bank_codes` | The 4-letter bank codes known by name (about 45 larger banks), optionally filtered by text. |

## Configuration

All optional environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `PINCODE_BASE_URL` | `https://api.postalpincode.in` | India Post PIN service root (for a mirror or proxy) |
| `IFSC_BASE_URL` | `https://ifsc.razorpay.com` | IFSC service root (the dataset can be self-hosted) |
| `INDIA_LOOKUP_TIMEOUT` | `20` | Request timeout in seconds |
| `INDIA_LOOKUP_RETRIES` | `2` | Retries on HTTP 429, 502, 503, 504 and timeouts |
| `INDIA_LOOKUP_CONCURRENCY` | `5` | Parallel upstream requests for bulk lookups |
| `INDIA_LOOKUP_MAX_CHARS` | `20000` | Responses longer than this are cut with a notice |
| `MCP_TRANSPORT` | `stdio` | `stdio` or `streamable-http` |

## Limits

- India only. These are community-run public services with no published SLA; the server retries politely but does not queue or cache requests.
- Bulk lookups send one upstream request per code (up to 5 in parallel) and are capped at 25 codes per call.
- PIN data has no coordinates. A PIN code covers a delivery area, often several post offices, so district and state are reliable but a PIN is not a precise location.
- `search_ifsc` filters on bank, city and branch name, which the IFSC service matches exactly (this server upper-cases the input). City and district spellings follow the dataset (for example `BANGALORE URBAN`, `GREATER BOMBAY`), so a differently spelled city returns nothing. There is no filter by state or PIN.
- Bank-name resolution covers about 45 larger banks; for any other bank pass its 4-letter code (the first four characters of its IFSC codes).
- The PIN found in a branch address is parsed from free text, so it can be missing or wrong; `with_postal_area` is a sanity check, not an authority.
- Data freshness follows the upstream datasets. A branch that closed or merged recently may still be listed, and new post offices may be missing.

## Development

```bash
uv sync
uv run pytest
```

Tests mock all HTTP traffic with `respx`; no network access is needed.

## Licence

MIT. Author: Ankur Kapuriya. Not affiliated with or endorsed by India Post, the Reserve Bank of India, Razorpay or postalpincode.in. Check each upstream service's terms before redistributing results or sending heavy traffic.
