"""Import CSV page (M7 C): upload a file and run every row through n8n's lead-intake workflow.

All checks and requests live in `csv_import` (tested without Streamlit). This page only shows the
upload, a preview, a progress bar and the per-row results. Each row becomes one n8n execution;
the API stays the only writer and decides created, duplicate or invalid.
"""

from __future__ import annotations

import streamlit as st

from flowpilot.ui import csv_import
from flowpilot.ui.dashboard_page import notice

RESULT_KEY = "fp_import_result"  # (file digest, results) of the last run in this session
OUTCOME_LABELS = {
    "created": "Created",
    "duplicate": "Duplicate",
    "invalid": "Invalid",
    "error": "Not processed",
}


def render() -> None:
    try:
        n8n = csv_import.load_n8n_config()
    except csv_import.ImportConfigError as error:
        notice("CSV import is misconfigured", str(error))
        return
    if n8n is None:
        notice(
            "CSV import is not set up",
            "Set FLOWPILOT_N8N_URL and FLOWPILOT_N8N_WEBHOOK_TOKEN so the console can reach the "
            "n8n lead-intake workflow.",
            tone="info",
        )
        return

    st.markdown(
        "Each row is sent to the **n8n `flowpilot-lead-intake` workflow**, which passes it to "
        "the FlowPilot API to validate, de-duplicate and score. Importing the same file again "
        "creates nothing new."
    )
    st.caption(
        f"Required columns: {', '.join(csv_import.REQUIRED_COLUMNS)}. Optional: source. "
        f"Up to {csv_import.MAX_ROWS} rows, {csv_import.MAX_FILE_BYTES // 1024} KiB, UTF-8. "
        "Emails must use reserved demo domains such as name@company.demo.example."
    )
    st.download_button(
        "Download template",
        csv_import.TEMPLATE_CSV.encode(),
        file_name="flowpilot-leads-template.csv",
        mime="text/csv",
        icon=":material/download:",
        key="fp_import_template",
    )
    upload = st.file_uploader("CSV file", type=["csv"], key="fp_import_file")
    if upload is None:
        return
    parsed = csv_import.parse_csv(upload.getvalue())
    if parsed.problems:
        notice("This file can't be imported", " ".join(parsed.problems))
        return

    st.markdown(f"**Preview:** {len(parsed.rows)} rows. The first rows are shown below.")
    st.dataframe(
        [{c: r.get(c, "") for c in csv_import.COLUMNS} for r in parsed.rows[:5]],
        hide_index=True,
        width="stretch",
    )
    if st.button(
        f"Import {len(parsed.rows)} rows through n8n",
        type="primary",
        icon=":material/upload:",
        key="fp_import_run",
    ):
        progress = st.progress(0.0, text="Sending rows to n8n…")
        results = []
        for index, row in enumerate(parsed.rows):
            results.append(csv_import.submit_row(n8n, row, line=index + 2, digest=parsed.digest))
            progress.progress((index + 1) / len(parsed.rows), text=f"Row {index + 2} done")
        progress.empty()
        st.session_state[RESULT_KEY] = (parsed.digest, results)

    stored = st.session_state.get(RESULT_KEY)
    if not stored or stored[0] != parsed.digest:
        return
    results = stored[1]
    counts = csv_import.summarize(results)
    cols = st.columns(4)
    for col, key in zip(cols, ("created", "duplicate", "invalid", "error"), strict=True):
        col.metric(OUTCOME_LABELS[key], counts[key])
    st.dataframe(
        [
            {
                "Row": r.row,
                "Company": r.company,
                "Result": OUTCOME_LABELS[r.outcome],
                "Tier": r.tier or "",
                "Lead": r.lead_id or "",
                "Detail": r.detail,
            }
            for r in results
        ],
        hide_index=True,
        width="stretch",
    )
    if counts["created"]:
        st.success(
            f"{counts['created']} new leads were scored and appear on the Leads page. "
            "Each row is a separate execution in n8n."
        )
