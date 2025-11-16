import re
import io

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
    """Parse OCR'd Telstra invoice PDF and return one row per mobile service."""
    rows = []

    with pdfplumber.open(file_obj) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            lines = text.splitlines()

            i = 0
            while i < len(lines):
                line = lines[i].strip()

                # Header line like: "Mobile 0400 936296"
                m_header = re.match(r"Mobile\s+(\d{4}\s?\d{3}\s?\d{3})", line)
                if not m_header:
                    i += 1
                    continue

                mobile = m_header.group(1).replace(" ", "")

                # Initialise metrics for this mobile
                nat = sms = enh = div = calls_os = calls_os_rec = os_data = 0
                total_call_ex = total_call_inc = 0.0
                svc_ex = svc_inc = 0.0
                wap_kb = 0
                countries = set()

                j = i + 1
                while j < len(lines):
                    l = lines[j].strip()

                    # Stop when we hit the next mobile block
                    if l.startswith("Mobile ") and j != i:
                        break

                    # Skip itemised detail block entirely
                    if l.startswith("Itemised call details"):
                        j += 1
                        continue

                    # ----- Call & Usage summaries -----
                    m_nat = re.search(r"National Direct.*?(\d+)\s*calls", l)
                    if m_nat:
                        nat = int(m_nat.group(1))

                    m_sms = re.search(r"Mobile Originated SMS.*?(\d+)\s*calls", l)
                    if m_sms:
                        sms = int(m_sms.group(1))

                    m_enh = re.search(r"Mobile Enhanced SMS.*?(\d+)\s*calls?", l)
                    if m_enh:
                        enh = int(m_enh.group(1))

                    m_div = re.search(r"Call Diversion.*?(\d+)\s*calls", l)
                    if m_div:
                        div = int(m_div.group(1))

                    m_calls_os = re.search(r"Calls made O/S.*?(\d+)\s*calls", l)
                    if m_calls_os:
                        calls_os = int(m_calls_os.group(1))

                    m_calls_rec = re.search(r"Calls received O/S.*?(\d+)\s*calls", l)
                    if m_calls_rec:
                        calls_os_rec = int(m_calls_rec.group(1))

                    m_data_os = re.search(r"Data Usage Overseas.*?(\d+)\s*calls?", l)
                    if m_data_os:
                        os_data = int(m_data_os.group(1))

                    # Total call charges line
                    m_tot_call = re.search(
                        r"Total call charges\s*\$?\s*([\d.]+)\s*\$?\s*([\d.]+)", l
                    )
                    if m_tot_call:
                        total_call_ex = float(m_tot_call.group(1))
                        total_call_inc = float(m_tot_call.group(2))

                    # ----- Service charge summary -----
                    m_svc_tot = re.search(
                        r"Total service charges\s*\$?\s*([\d.]+)\s*\$?\s*([\d.]+)", l
                    )
                    if m_svc_tot:
                        svc_ex = float(m_svc_tot.group(1))
                        svc_inc = float(m_svc_tot.group(2))

                    # ----- WAP / mobile internet sessions -----
                    if ("telstra.wap" in l.lower() or "telstra.internet" in l.lower()):
                        m_vol = re.search(r"(\d+)\s*$", l)
                        if m_vol:
                            wap_kb += int(m_vol.group(1))

                    # ----- Overseas countries -----
                    for c in KNOWN_COUNTRIES:
                        if c.lower() in l.lower():
                            countries.add(c)

                    j += 1

                rows.append(
                    {
                        "Mobile Number": mobile,
                        "National Direct Calls": nat,
                        "SMS (Mobile Originated)": sms,
                        "Enhanced SMS": enh,
                        "Call Diversion Calls": div,
                        "Calls Made Overseas": calls_os,
                        "Calls Received Overseas": calls_os_rec,
                        "Overseas Data Sessions": os_data,
                        "Total Call Charges (Excl GST)": total_call_ex,
                        "Total Call Charges (Incl GST)": total_call_inc,
                        "Total Service Charges (Excl GST)": svc_ex,
                        "Total Service Charges (Incl GST)": svc_inc,
                        "Total WAP Volume (KB)": wap_kb,
                        "Overseas Countries": ", ".join(sorted(countries)),
                        "Total Spend per Mobile (Excl GST)": total_call_ex + svc_ex,
                        "Total Spend per Mobile (Incl GST)": total_call_inc + svc_inc,
                    }
                )

                i = j

            i += 1

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
