"""CSV import through n8n (M7 C). No Streamlit import, so it is tested in the default suite.

The console parses and checks an uploaded CSV, then sends each row to n8n's
`flowpilot-lead-intake` webhook, the same tested workflow used for live intake. n8n forwards the
lead to the API, which validates, de-duplicates, scores and audits it; every row is one n8n
execution, visible in the editor. Each row carries an Idempotency-Key derived from the file and
row, so re-importing the same file never creates a lead twice.

The webhook is reached only inside the container network (public demo: `http://n8n:5678`) or on
a loopback address locally; Caddy never routes webhook paths. Uploaded values are only sent as
JSON strings to the API and never evaluated, so spreadsheet formulas in cells stay inert text.
"""

from __future__ import annotations

import csv
import hashlib
import io
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from flowpilot.settings import ENV_PREFIX, LOOPBACK_HOSTS, _merged_env

REQUIRED_COLUMNS = (
    "company_name",
    "contact_name",
    "contact_email",
    "company_size",
    "industry",
    "service_interest",
    "budget_band",
    "timeline",
    "message",
)
OPTIONAL_COLUMNS = ("source",)
COLUMNS = REQUIRED_COLUMNS + OPTIONAL_COLUMNS
MAX_FILE_BYTES = 256 * 1024
MAX_ROWS = 100
MAX_CELL = 5000
WEBHOOK_PATH = "/webhook/flowpilot-lead-intake"
PUBLIC_DEMO_N8N_HOST = "n8n"  # the private Compose service name (M7)
MIN_TOKEN_LENGTH = 24
REQUEST_TIMEOUT_S = 15.0
TEMPLATE_CSV = (
    ",".join(COLUMNS)
    + "\nHarbor Crest Logistics,Morgan Reyes,morgan.reyes@harborcrest.demo.example,51-200,"
    "logistics,process_automation,50k_100k,1_3_months,"
    '"We need to automate order intake across three warehouses.",referral\n'
)


class ImportConfigError(ValueError):
    """The console is not configured to reach n8n."""


@dataclass(frozen=True)
class N8nConfig:
    url: str
    token: str = field(repr=False)
    public_demo: bool = False

    def __post_init__(self) -> None:
        parts = urlsplit(self.url)
        allowed = parts.hostname in LOOPBACK_HOSTS or (
            self.public_demo and parts.hostname == PUBLIC_DEMO_N8N_HOST
        )
        if (
            parts.scheme != "http"
            or not allowed
            or parts.username
            or parts.password
            or parts.path not in {"", "/"}
            or parts.query
            or parts.fragment
        ):
            raise ImportConfigError(
                "FLOWPILOT_N8N_URL must be a bare http:// URL on a loopback address "
                "(or http://n8n:5678 in the public demo)"
            )
        if len(self.token) < MIN_TOKEN_LENGTH or not self.token.isascii():
            raise ImportConfigError("FLOWPILOT_N8N_WEBHOOK_TOKEN is missing or too short")


def load_n8n_config(
    environ: Mapping[str, str] | None = None, env_file: Path | None = Path(".env")
) -> N8nConfig | None:
    """None when CSV import is not set up (no URL); ImportConfigError when it is set up wrongly."""
    merged = _merged_env(environ, env_file)
    url = (merged.get(ENV_PREFIX + "N8N_URL") or "").strip().rstrip("/")
    if not url:
        return None
    return N8nConfig(
        url=url,
        token=merged.get(ENV_PREFIX + "N8N_WEBHOOK_TOKEN") or "",
        public_demo=merged.get(ENV_PREFIX + "PUBLIC_DEMO") == "1",
    )


@dataclass(frozen=True)
class ParsedCsv:
    rows: tuple[dict[str, str], ...] = ()
    problems: tuple[str, ...] = ()
    digest: str = ""


def parse_csv(data: bytes) -> ParsedCsv:
    """Check the file and return its rows (known columns only, values trimmed) or problems."""
    if not data.strip():
        return ParsedCsv(problems=("The file is empty.",))
    if len(data) > MAX_FILE_BYTES:
        return ParsedCsv(problems=(f"The file is larger than {MAX_FILE_BYTES // 1024} KiB.",))
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return ParsedCsv(problems=("The file is not UTF-8 text. In Excel, save as CSV UTF-8.",))
    try:
        records = list(csv.reader(io.StringIO(text, newline="")))
    except csv.Error:
        return ParsedCsv(problems=("The file is not a valid CSV.",))
    header = [h.strip().lower() for h in records[0]] if records else []
    problems = []
    repeated = sorted({h for h in header if h and header.count(h) > 1})
    if repeated:
        problems.append("Repeated columns: " + ", ".join(repeated))
    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing:
        problems.append("Missing columns: " + ", ".join(missing))
    unknown = [h for h in header if h and h not in COLUMNS]
    if unknown:
        problems.append("Unknown columns: " + ", ".join(sorted(set(unknown))))
    body = [r for r in records[1:] if any(cell.strip() for cell in r)]
    if not body and not problems:
        problems.append("The file has a header but no rows.")
    if len(body) > MAX_ROWS:
        problems.append(f"The file has {len(body)} rows; the limit is {MAX_ROWS} per import.")
    if problems:
        return ParsedCsv(problems=tuple(problems))
    rows = []
    for number, record in enumerate(body, start=2):
        if len(record) > len(header):
            return ParsedCsv(problems=(f"Row {number} has more cells than the header.",))
        row = {}
        for name, value in zip(header, record, strict=False):
            value = value.strip()
            if len(value) > MAX_CELL:
                return ParsedCsv(problems=(f"Row {number}: {name} is too long.",))
            if value:
                row[name] = value
        rows.append(row)
    return ParsedCsv(rows=tuple(rows), digest=hashlib.sha256(data).hexdigest()[:16])


@dataclass(frozen=True)
class RowResult:
    row: int  # the line number in the file (the header is line 1)
    company: str
    outcome: str  # created | duplicate | invalid | error
    lead_id: int | None = None
    tier: str | None = None
    detail: str = ""


def _invalid_detail(body: dict) -> str:
    fields = []
    for item in body.get("detail") or []:
        if isinstance(item, dict) and item.get("loc"):
            fields.append(str(item["loc"][-1]))
    return "Invalid: " + ", ".join(sorted(set(fields))) if fields else "Rejected by FlowPilot"


def submit_row(
    config: N8nConfig,
    row: Mapping[str, str],
    *,
    line: int,
    digest: str,
    transport: httpx.BaseTransport | None = None,
) -> RowResult:
    """Send one row to the n8n intake webhook and describe what happened."""
    company = row.get("company_name", "")
    headers = {
        "X-Webhook-Token": config.token,
        "Idempotency-Key": f"csv-{digest}-{line}",
    }
    try:
        with httpx.Client(
            base_url=config.url,
            timeout=REQUEST_TIMEOUT_S,
            trust_env=False,
            follow_redirects=False,
            transport=transport,
        ) as client:
            response = client.post(WEBHOOK_PATH, json=dict(row), headers=headers)
    except httpx.HTTPError:
        return RowResult(line, company, "error", detail="n8n is not reachable")
    try:
        body = response.json()
    except ValueError:
        body = {}
    body = body if isinstance(body, dict) else {}
    result = body.get("result")
    if response.status_code == 201 and result == "created":
        return RowResult(
            line, company, "created", body.get("lead_id"), body.get("tier"), "Scored and stored"
        )
    if response.status_code == 200 and result == "duplicate":
        return RowResult(line, company, "duplicate", body.get("lead_id"), detail="Already exists")
    if response.status_code == 422 and result == "invalid":
        return RowResult(line, company, "invalid", detail=_invalid_detail(body))
    if response.status_code == 403:
        return RowResult(line, company, "error", detail="n8n refused the webhook token")
    if response.status_code == 404:
        return RowResult(line, company, "error", detail="The n8n intake workflow is not active")
    return RowResult(line, company, "error", detail=f"n8n answered HTTP {response.status_code}")


def summarize(results: list[RowResult]) -> dict[str, int]:
    counts = {"created": 0, "duplicate": 0, "invalid": 0, "error": 0}
    for result in results:
        counts[result.outcome] += 1
    return counts
