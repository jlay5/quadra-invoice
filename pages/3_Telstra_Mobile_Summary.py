import re
import io
from collections import defaultdict

import pdfplumber
import pandas as pd
import streamlit as st

st.title("📱 Telstra Mobile Summary (OCR PDF → Excel)")

st.markdown("""
Upload the **OCR'd Telstra Enterprise invoice PDF** (with a text layer).
This page will create a **one-row-per-mobile** summary including:

- Call & Usage line-items (counts + totals)
- Service charges (excl & incl GST)
- Total WAP / Mobile Internet volume (KB)
- Overseas usage (countries)
- Itemised call details are **excluded**
""")

uploaded_pdf = st.file_uploader(
    "Upload the OCR'd Telstra PDF",
    type=["pdf"],
    help="Use the OCR-processed version of the Telstra invoice."
)

# Expand this list as needed when you see other country names in your data
KNOWN_COUNTRIES = ["Fiji", "Nauru", "Chile", "Singapore", "USA", "UK"]


def parse_telstra_pdf(file_obj) -> pd.DataFrame:
    """
    Parse OCR'd Telstra invoice PDF and return one row per mobile service.

    Strategy per page:
    - Identify the mobile number from the header ("Mobile 04xx xxx xxx").
    - From the page text, read Call & Usage + Service summary totals.
    - From the tables, sum WAP / Internet Vol(KB).
    - From the tables, capture Origin values ONLY for
      "Data usage overseas (GST FREE)" rows.
    """
    mobiles = defaultdict(lambda: {
        "National Direct Calls": 0,
        "SMS (Mobile Originated)": 0,
        "Enhanced SMS": 0,
        "Call Diversion Calls": 0,
        "Calls Made Overseas": 0,
        "Calls Received Overseas": 0,
        "Overseas Data Sessions": 0,
        "Total Call Charges (Excl GST)": 0.0,
        "Total Call Charges (Incl GST)": 0.0,
        "Total Service Charges (Excl GST)": 0.0,
        "Total Service Charges (Incl GST)": 0.0,
        "Total WAP Volume (KB)": 0,
        "Overseas Countries": set(),
    })

    with pdfplumber.open(file_obj) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            lines = [ln.rstrip() for ln in text.splitlines()]

            # ---------- Which mobile does this page belong to? ----------
            m_header = re.search(r"Mobile\s+([0-9 ]{8,15})", text)
            if not m_header:
                continue  # skip pages without a mobile header

            raw = m_header.group(1)
            digits = re.sub(r"\D", "", raw)
            mobile = digits[-10:] if len(digits) >= 10 else digits

            data = mobiles[mobile]

            # ---------- Call & Usage + Service summaries (text) ----------
            for l in lines:
                l_stripped = l.strip()

                # Call & Usage counts
                m_nat = re.search(r"National Direct.*?(\d+)\s*calls", l_stripped)
                if m_nat:
                    data["National Direct Calls"] = int(m_nat.group(1))

                m_sms = re.search(r"Mobile Originated SMS.*?(\d+)\s*calls", l_stripped)
                if m_sms:
                    data["SMS (Mobile Originated)"] = int(m_sms.group(1))

                m_enh = re.search(r"Mobile Enhanced SMS.*?(\d+)\s*calls?", l_stripped)
                if m_enh:
                    data["Enhanced SMS"] = int(m_enh.group(1))

                m_div = re.search(r"Call Diversion.*?(\d+)\s*calls", l_stripped)
                if m_div:
                    data["Call Diversion Calls"] = int(m_div.group(1))

                m_calls_os = re.search(r"Calls made O/S.*?(\d+)\s*calls", l_stripped)
                if m_calls_os:
                    data["Calls Made Overseas"] = int(m_calls_os.group(1))

                m_calls_rec = re.search(r"Calls received O/S.*?(\d+)\s*calls", l_stripped)
                if m_calls_rec:
                    data["Calls Received Overseas"] = int(m_calls_rec.group(1))

                m_data_os = re.search(r"Data Usage Overseas.*?(\d+)\s*calls?", l_stripped)
                if m_data_os:
                    data["Overseas Data Sessions"] = int(m_data_os.group(1))

                # Total call charges
                m_tot_call = re.search(
                    r"Total call charges\s*\$?\s*([\d.]+)\s*\$?\s*([\d.]+)",
                    l_stripped,
                )
                if m_tot_call:
                    try:
                        ex = float(m_tot_call.group(1))
                        inc = float(m_tot_call.group(2))
                        data["Total Call Charges (Excl GST)"] += ex
                        data["Total Call Charges (Incl GST)"] += inc
                    except ValueError:
                        pass

                # Total service charges
                m_svc_tot = re.search(
                    r"Total service charges\s*\$?\s*([\d.]+)\s*\$?\s*([\d.]+)",
                    l_stripped,
                )
                if m_svc_tot:
                    try:
                        ex = float(m_svc_tot.group(1))
                        inc = float(m_svc_tot.group(2))
                        data["Total Service Charges (Excl GST)"] += ex
                        data["Total Service Charges (Incl GST)"] += inc
                    except ValueError:
                        pass

            # ---------- Tables: WAP KB + Overseas Countries ----------
            tables = page.extract_tables()
            for tbl in tables:
                if not tbl or len(tbl) < 2:
                    continue

                header = [(c or "").strip() for c in tbl[0]]
                header_lower = [h.lower() for h in header]

                # Find Vol(KB) column (for WAP volume)
                vol_idx = None
                for idx, h in enumerate(header_lower):
                    if ("vol" in h and "kb" in h) or h in {"kb", "vol", "volume"}:
                        vol_idx = idx
                        break

                # Find Origin column (for countries)
                origin_idx = None
                for idx, h in enumerate(header_lower):
                    if "origin" in h:
                        origin_idx = idx
                        break

                # Walk data rows
                for row in tbl[1:]:
                    cells = [(c or "") for c in row]
                    cells_lower = [c.lower() for c in cells]

                    # WAP / internet sessions: detect by description
                    if any("telstra.wap" in c or "telstra.internet" in c for c in cells_lower):
                        if vol_idx is not None and vol_idx < len(cells):
                            cell = cells[vol_idx].replace(",", "").strip()
                            if cell.isdigit():
                                data["Total WAP Volume (KB)"] += int(cell)

                    # Overseas countries: ONLY from "Data usage overseas (GST FREE)" rows
                    if any("data usage overseas" in c for c in cells_lower):
                        if origin_idx is not None and origin_idx < len(cells):
                            country = cells[origin_idx].strip()
                            if country:
                                data["Overseas Countries"].add(country)

    # ---------- Build final DataFrame ----------
    rows = []
    for mobile, d in mobiles.items():
        countries_str = (
            ", ".join(sorted(d["Overseas Countries"]))
            if d["Overseas Countries"]
            else ""
        )
        total_ex = d["Total Call Charges (Excl GST)"] + d["Total Service Charges (Excl GST)"]
        total_inc = d["Total Call Charges (Incl GST)"] + d["Total Service Charges (Incl GST)"]

        rows.append({
            "Mobile Number": mobile,
            "National Direct Calls": d["National Direct Calls"],
            "SMS (Mobile Originated)": d["SMS (Mobile Originated)"],
            "Enhanced SMS": d["Enhanced SMS"],
            "Call Diversion Calls": d["Call Diversion Calls"],
            "Calls Made Overseas": d["Calls Made Overseas"],
            "Calls Received Overseas": d["Calls Received Overseas"],
            "Overseas Data Sessions": d["Overseas Data Sessions"],
            "Total Call Charges (Excl GST)": d["Total Call Charges (Excl GST)"],
            "Total Call Charges (Incl GST)": d["Total Call Charges (Incl GST)"],
            "Total Service Charges (Excl GST)": d["Total Service Charges (Excl GST)"],
            "Total Service Charges (Incl GST)": d["Total Service Charges (Incl GST)"],
            "Total WAP Volume (KB)": d["Total WAP Volume (KB)"],
            "Overseas Countries": countries_str,
            "Total Spend per Mobile (Excl GST)": total_ex,
            "Total Spend per Mobile (Incl GST)": total_inc,
        })

    df = pd.DataFrame(rows)

    if not df.empty:
        df = (
            df.sort_values(["Mobile Number", "Total Spend per Mobile (Incl GST)"])
              .drop_duplicates(subset=["Mobile Number"], keep="last")
        )

    return df


# ------------------ UI flow ------------------ #
if uploaded_pdf:
    st.info("Processing PDF… this can take a little while for 100+ pages.")

    try:
        df_summary = parse_telstra_pdf(uploaded_pdf)
    except Exception as e:
        st.error(f"Error while parsing PDF:\n\n{e}")
        st.stop()

    if df_summary.empty:
        st.warning(
            "No mobile summaries were detected. "
            "Check that this is the OCR'd Telstra invoice."
        )
    else:
        st.success(f"Extracted {len(df_summary)} mobile services.")

        st.subheader("Preview: Mobile Summary")
        st.dataframe(df_summary, use_container_width=True)

        # Build Excel in memory
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="xlsxwriter") as writer:
            df_summary.to_excel(writer, sheet_name="Mobile Summary", index=False)
        buffer.seek(0)

        st.download_button(
            "Download Excel (Mobile Summary)",
            data=buffer,
            file_name="telstra_mobile_summary.xlsx",
            mime=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
        )
else:
    st.info("Upload an OCR'd Telstra invoice PDF to begin.")
