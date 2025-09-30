import streamlit as st
import pandas as pd
import pdfplumber
import re
from io import BytesIO

st.set_page_config(page_title="Invoice Parser", layout="centered")
st.title("📑 Telstra, Optus & Vodafone Invoice Parser")
st.write("Upload a Telstra, Optus or Vodafone PDF invoice and download the extracted mobile charges as Excel or CSV.")

uploaded_file = st.file_uploader("Upload Invoice (PDF)", type=["pdf"])


# ---------------- TELSTRA ----------------
def parse_telstra(pdf):
    data = []
    for page in pdf.pages:
        text = page.extract_text()
        if not text:
            continue

        mobiles = re.findall(r"04\d{2}\s?\d{3}\s?\d{3}", text)
        plans = re.findall(r"Business Mobile Plan[^\n]*", text)
        spends = re.findall(r"\$?\d+\,?\d*\.\d{2}", text)

        for i, m in enumerate(mobiles):
            plan = plans[i] if i < len(plans) else "Unknown"
            spend_excl, spend_incl = None, None
            if len(spends) >= (2*i + 2):
                try:
                    spend_excl = float(spends[2*i].replace(",", "").replace("$", ""))
                    spend_incl = float(spends[2*i+1].replace(",", "").replace("$", ""))
                except:
                    pass
            data.append({
                "Mobile Number": m,
                "Plan Name": plan.strip(),
                "Spend Excl GST": spend_excl,
                "Spend Incl GST": spend_incl,
            })
    return pd.DataFrame(data)


# ---------------- OPTUS ----------------
def parse_optus(pdf):
    data = []
    full_text = " ".join([p.extract_text() or "" for p in pdf.pages])
    for page in pdf.pages:
        text = page.extract_text()
        if not text:
            continue

        # Pattern like "0403061668 on $60 Business Mobile Plus M2M"
        matches = re.findall(r"(04\d{8}) on \$([\d,]*\.\d{2}|\d+)\s+(.+?M2M)", text)
        for m in matches:
            number, raw_price, plan = m
            # Look for final monthly charge (after discounts)
            disc_match = re.search(rf"{number}.*?Total Monthly Charges\s+\$([\d\.]+)", full_text, re.S)
            spend = float(disc_match.group(1)) if disc_match else float(raw_price)
            data.append({
                "Mobile Number": number,
                "Plan Name": plan.strip(),
                "Spend Excl GST": round(spend/1.1, 2),
                "Spend Incl GST": spend
            })
    return pd.DataFrame(data)


# ---------------- VODAFONE ----------------
def parse_vodafone(pdf):
    data = []
    full_text = " ".join([p.extract_text() or "" for p in pdf.pages])

    for page in pdf.pages:
        text = page.extract_text()
        if not text:
            continue

        # Pattern: "04XXXXXXXX on $XX.XX <PlanName>" or "$60"
        matches = re.findall(r"(04\d{8}) on \$([\d,]*\.\d{2}|\d+)\s+(.+?)(?:\s|$)", text)
        for m in matches:
            number, amt_str, plan = m
            try:
                spend_incl = float(amt_str.replace(",", ""))
            except:
                spend_incl = None

            # Optional override: look for "Total Monthly Charges" for that number
            override = re.search(rf"{number}.*?\$([\d,]*\.\d{2})", full_text)
            if override:
                try:
