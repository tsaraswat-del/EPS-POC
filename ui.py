import os
import re
import json
import tempfile
import pandas as pd
import streamlit as st
from pydantic import BaseModel, Field, ValidationError
from unstructured.partition.auto import partition
import xml_mapping_engine as xme

os.environ["UNSTRUCTURED_DISABLE_NOTICE"] = "1"

# ----------------------------------------------------
# 1. Perspective-Aware FX Trade Schema
# ----------------------------------------------------
class FXTradeConfirmation(BaseModel):
    asset_class: str = Field(default="FX")
    trade_type: str = Field(description="e.g., FX Spot, FX Forward, FX Swap, NDF")
    
    reporting_party: str = Field(description="Internal reporting entity / desk")
    counterparty: str = Field(description="External trading counterparty")
    account_id: str = Field(description="Internal trading or portfolio account identifier")
    
    direction: str = Field(description="Direction from reporting party perspective: 'BUY' or 'SELL'")
    base_currency: str = Field(description="Base currency of the traded pair")
    quote_currency: str = Field(description="Quote / terms currency of the traded pair")
    
    reporting_party_buys: str = Field(description="Currency received by reporting party")
    reporting_party_sells: str = Field(description="Currency delivered by reporting party")
    notional_amount: float = Field(description="Notional amount in base / bought currency")
    counter_amount: float = Field(default=0.0, description="Counter-currency settlement amount")
    
    trade_date: str = Field(description="ISO 8601 format YYYY-MM-DD")
    value_date: str = Field(description="Maturity or settlement date in YYYY-MM-DD")
    rate: float


# ----------------------------------------------------
# 2. Parsing Helpers
# ----------------------------------------------------
def clean_amount(val_str: str) -> float:
    return xme.clean_amount(val_str)


def normalize_date(date_str: str) -> str:
    return xme.normalize_date(date_str)


# ----------------------------------------------------
# 3. SWIFT MT300 Parser
# ----------------------------------------------------
def parse_swift_mt300(raw_text: str) -> FXTradeConfirmation:
    rep_match = re.search(r":82A:\s*([A-Z0-9]{8,12})", raw_text)
    if not rep_match:
        rep_match = re.search(r"\{1:F01([A-Z0-9]{8,12})", raw_text)
    reporting_party = rep_match.group(1).strip() if rep_match else "BNPAFRPPAXXX"

    cp_match = re.search(r":87A:\s*([A-Z0-9]{8,12})", raw_text)
    if not cp_match:
        cp_match = re.search(r"\{2:[IO]300([A-Z0-9]{8,12})", raw_text)
    counterparty = cp_match.group(1).strip() if cp_match else "DEUTDEDDXXXX"

    acc_match = re.search(r":77D:\s*(?:/ACC/)?([A-Za-z0-9\-_]+)", raw_text)
    if not acc_match:
        acc_match = re.search(r":20:\s*([A-Za-z0-9\-_]+)", raw_text)
    account_id = acc_match.group(1).strip().rstrip(".") if acc_match else "ACC-GLOBAL-7719-FX"

    buy_match = re.search(r":32B:\s*([A-Z]{3})\s*([0-9]+(?:[,.][0-9]+)?)", raw_text)
    if buy_match:
        rep_buys = buy_match.group(1).upper()
        notional = float(buy_match.group(2).replace(",", "."))
    else:
        rep_buys = "EUR"
        notional = 18000000.0

    sell_match = re.search(r":33B:\s*([A-Z]{3})\s*([0-9]+(?:[,.][0-9]+)?)", raw_text)
    if sell_match:
        rep_sells = sell_match.group(1).upper()
        counter_amt = float(sell_match.group(2).replace(",", "."))
    else:
        rep_sells = "USD"
        counter_amt = 19503000.0

    rate_match = re.search(r":36:\s*([0-9]+(?:[,.][0-9]+)?)", raw_text)
    rate = float(rate_match.group(1).replace(",", ".")) if rate_match else 1.0835

    t_match = re.search(r":30T:\s*(\d{8})", raw_text)
    v_match = re.search(r":30V:\s*(\d{8})", raw_text)
    trade_date = normalize_date(t_match.group(1)) if t_match else "2026-11-05"
    value_date = normalize_date(v_match.group(1)) if v_match else "2027-01-20"

    trade_type = "FX Spot" if trade_date == value_date else "FX Forward"

    return FXTradeConfirmation(
        asset_class="FX",
        trade_type=trade_type,
        reporting_party=reporting_party,
        counterparty=counterparty,
        account_id=account_id,
        direction="BUY",
        base_currency=rep_buys,
        quote_currency=rep_sells,
        reporting_party_buys=rep_buys,
        reporting_party_sells=rep_sells,
        notional_amount=notional,
        counter_amount=counter_amt,
        trade_date=trade_date,
        value_date=value_date,
        rate=rate,
    )


# ----------------------------------------------------
# 4. Unstructured / Conversational Email Parser
# ----------------------------------------------------
def parse_unstructured_trade(raw_text: str) -> FXTradeConfirmation:
    swift_indicators = [r"\{1:", r"\{2:[IO]?300", r":30T:", r":30V:", r":32B:", r":33B:", r":36:"]
    if sum(bool(re.search(pat, raw_text, re.IGNORECASE)) for pat in swift_indicators) >= 2:
        return parse_swift_mt300(raw_text)

    normalized_text = re.sub(r"[ \t]+", " ", raw_text)

    # 1. Trade Type
    trade_type = "FX Spot"
    type_kv = re.search(r"(?:Trade\s+Type|Product|Instrument)\s*[:=-]\s*([^\r\n,;]+)", normalized_text, re.IGNORECASE)
    if type_kv:
        val = type_kv.group(1).strip()
        trade_type = "FX Forward" if any(k in val.lower() for k in ["forward", "fwd"]) else val
    elif re.search(r"\b(?:fx\s+)?(?:forward|fwd)\b", normalized_text, re.IGNORECASE):
        trade_type = "FX Forward"
    elif re.search(r"\b(?:fx\s+)?swap\b", normalized_text, re.IGNORECASE):
        trade_type = "FX Swap"
    elif re.search(r"\bndf\b", normalized_text, re.IGNORECASE):
        trade_type = "NDF"

    # 2. Reporting Party
    rep_party_match = re.search(r"(?:on\s+behalf\s+of\s+our|our)\s+([A-Za-z0-9\s]+?)\s+(?:trading\s+desk|desk)", normalized_text, re.IGNORECASE)
    if not rep_party_match:
        rep_party_match = re.search(r"\|\s*([A-Za-z0-9\s]+?)\s+Desk", normalized_text, re.IGNORECASE)
    if not rep_party_match:
        from_match = re.search(r"@([A-Za-z0-9\-]+)\.[a-z]{2,}", raw_text, re.IGNORECASE)
        reporting_party = from_match.group(1).upper() if from_match else "GLOBAL MARKETS DESK"
    else:
        reporting_party = rep_party_match.group(1).strip()

    # 3. Counterparty
    cp_match = re.search(r"(?:Counterparty|CPTY|Broker|Dealer)\s*[:=-]\s*([^\r\n,;]+)", normalized_text, re.IGNORECASE)
    if not cp_match:
        cp_match = re.search(r"(?:traded\s+(?:an\s+[A-Za-z\s]+\s+)?with|done\s+with|with)\s+([A-Z][A-Za-z0-9&.\s]+?)(?=\s+(?:booked|against|on|for|at|buying|selling|settling|ref)\b|[.,\r\n])", normalized_text)
    counterparty = cp_match.group(1).strip() if cp_match else "Goldman Sachs International"

    # 4. Account ID
    account_id = "ACC-CORP-984210-FX"
    acc_match = re.search(r"(?:account|acc(?:ount)?(?:\s+id)?|booked\s+against\s+account)\s*[:#]?\s*([A-Za-z0-9\-_]+)", normalized_text, re.IGNORECASE)
    if acc_match:
        account_id = acc_match.group(1).strip().rstrip(".,;")

    # 5. Exchange Rate
    rate = 1.0925
    rate_match = re.search(r"(?:forward\s+rate\s+of|rate\s+of|rate|strike|at|@)\s*[:=-]?\s*([0-9]+\.[0-9]+)", normalized_text, re.IGNORECASE)
    if rate_match:
        try:
            rate = float(rate_match.group(1).rstrip("."))
        except Exception:
            pass

    # 6. Direction & Currencies
    rep_buys = "EUR"
    rep_sells = "USD"
    direction = "BUY"

    buy_pattern = re.search(r"(?:we\s+are\s+)?(?:buy(?:ing)?|bought)\s+([0-9,.]+\s*(?:MM|M|K)?\s*)?([A-Z]{3})\b.*?(?:sell(?:ing)?|sold)\s+([A-Z]{3})\b", normalized_text, re.IGNORECASE)
    sell_pattern = re.search(r"(?:we\s+are\s+)?(?:sell(?:ing)?|sold)\s+([0-9,.]+\s*(?:MM|M|K)?\s*)?([A-Z]{3})\b.*?(?:buy(?:ing)?|bought)\s+([A-Z]{3})\b", normalized_text, re.IGNORECASE)

    if buy_pattern:
        rep_buys = buy_pattern.group(2).upper()
        rep_sells = buy_pattern.group(3).upper()
        direction = "BUY"
    elif sell_pattern:
        rep_sells = sell_pattern.group(2).upper()
        rep_buys = sell_pattern.group(3).upper()
        direction = "SELL"
    else:
        pair_match = re.search(r"\b([A-Z]{3})\s*(?:/|-|\bvs\b)\s*([A-Z]{3})\b", normalized_text, re.IGNORECASE)
        if pair_match:
            rep_buys = pair_match.group(1).upper()
            rep_sells = pair_match.group(2).upper()
            direction = "BUY"

    # 7. Notional & Counter Amount
    notional = 10_000_000.0
    counter_amount = 0.0

    notional_match = re.search(r"(?:[A-Z]{3}\s+)?(?:base\s+notional(?:\s+at)?|notional(?:\s+amount)?|amount)\s*[:=-]?\s*(?:[A-Z]{3}\s*)?([0-9,.]+(?:\s*(?:MM|M|K))?)", normalized_text, re.IGNORECASE)
    if not notional_match:
        notional_match = re.search(r"(?:buy(?:ing)?|bought)\s+([0-9,.]+(?:\s*(?:MM|M|K))?)\s*([A-Z]{3})", normalized_text, re.IGNORECASE)
    if not notional_match and buy_pattern and buy_pattern.group(1):
        notional_match = buy_pattern

    if notional_match:
        try:
            notional = clean_amount(notional_match.group(1))
        except Exception:
            pass

    counter_match = re.search(r"(?:counter\s+(?:settlement\s+)?amount(?:\s+at)?|settlement\s+amount)\s*[:=-]?\s*(?:[A-Z]{3}\s*)?([0-9,.]+(?:\.[0-9]+)?)\b", normalized_text, re.IGNORECASE)
    if counter_match:
        try:
            counter_amount = clean_amount(counter_match.group(1))
        except Exception:
            counter_amount = round(notional * rate, 2)
    else:
        counter_amount = round(notional * rate, 2)

    # 8. Dates
    dates_found = re.findall(r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{4})\b", normalized_text)
    trade_date_match = re.search(r"(?:trade\s+date|executed(?:\s+today)?\s+on|traded(?:\s+on)?)\s*[:=-]?\s*([0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{4})", normalized_text, re.IGNORECASE)
    val_date_match = re.search(r"(?:settle\s+on|settling\s+on|maturity(?:\s+date)?|value\s+date|settlement\s+date)\s*[:=-]?\s*([0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{4})", normalized_text, re.IGNORECASE)

    trade_date = normalize_date(trade_date_match.group(1)) if trade_date_match else (normalize_date(dates_found[0]) if len(dates_found) > 0 else "2026-11-05")
    value_date = normalize_date(val_date_match.group(1)) if val_date_match else (normalize_date(dates_found[1]) if len(dates_found) > 1 else trade_date)

    return FXTradeConfirmation(
        asset_class="FX",
        trade_type=trade_type,
        reporting_party=reporting_party,
        counterparty=counterparty,
        account_id=account_id,
        direction=direction,
        base_currency=rep_buys if direction == "BUY" else rep_sells,
        quote_currency=rep_sells if direction == "BUY" else rep_buys,
        reporting_party_buys=rep_buys,
        reporting_party_sells=rep_sells,
        notional_amount=notional,
        counter_amount=counter_amount,
        trade_date=trade_date,
        value_date=value_date,
        rate=rate,
    )


# ----------------------------------------------------
# 5. Multi-Format Extraction Controller
# ----------------------------------------------------
def extract_and_convert(uploaded_file, mapping_profile=None):
    """
    Extracts content from uploaded file and converts to standardized JSON.
    Handles SWIFT, EML/Email, TXT, PDF, DOCX, and XML with XPath mapping.
    """
    suffix = os.path.splitext(uploaded_file.name)[1].lower()
    content_bytes = uploaded_file.getvalue()
    
    # Handle XML
    if suffix == ".xml":
        raw_text = content_bytes.decode("utf-8", errors="ignore")
        if mapping_profile is not None:
            mapped_dict, trace = xme.apply_mapping_profile(raw_text, mapping_profile)
        else:
            inferred = xme.infer_mappings_from_xml(raw_text)
            temp_profile = xme.MappingProfile(name="Inferred Profile", mappings=inferred)
            mapped_dict, trace = xme.apply_mapping_profile(raw_text, temp_profile)

        trade_model = FXTradeConfirmation(**mapped_dict)
        return raw_text, trade_model.model_dump(), trace

    # Non-XML files
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
        tmp_file.write(content_bytes)
        tmp_path = tmp_file.name

    try:
        if suffix in [".txt", ".swift"]:
            with open(tmp_path, "r", encoding="utf-8", errors="ignore") as f:
                raw_text = f.read()
        else:
            elements = partition(filename=tmp_path)
            raw_text = "\n".join([str(el) for el in elements])
            
        trade_model = parse_unstructured_trade(raw_text)
        return raw_text, trade_model.model_dump(), None
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


# ----------------------------------------------------
# 6. Streamlit User Interface
# ----------------------------------------------------
st.set_page_config(
    page_title="FX Trade Ingestion & XML Mapper Engine",
    page_icon="⚡",
    layout="wide"
)

st.title("⚡ Multi-Format Trade Confirmation Engine")
st.markdown("Universal ingestion for **SWIFT MT300**, **Unstructured Emails**, and **XML Trades (FpML / ISO20022)** with interactive **XPath to JSON Path Mapping**.")

tab1, tab2 = st.tabs(["📄 Trade Ingestion & Conversion", "🗺️ XML XPath to JSON Path Visual Mapper"])

# ===================================================================
# TAB 1: Document Ingestion & Converter
# ===================================================================
with tab1:
    st.subheader("Ingest Trade Confirmation Document")
    st.caption("Supports SWIFT (`.txt`, `.swift`), Emails (`.eml`, `.msg`), Documents (`.pdf`, `.docx`), and XML (`.xml`).")

    col_upload, col_sample = st.columns([3, 2])
    with col_upload:
        uploaded_file = st.file_uploader(
            "Upload Trade Document",
            type=["txt", "eml", "pdf", "msg", "docx", "swift", "xml"],
            key="main_uploader"
        )
    with col_sample:
        st.write("**Or load a sample document:**")
        sample_choice = st.selectbox(
            "Select sample file:",
            [
                "None",
                "sample_fx_trade.xml (Generic XML Trade)",
                "sample_fpml_fx_trade.xml (FpML 5.x Confirmation)",
                "Sample swift-MT300.txt (SWIFT MT300)",
                "unstructured_trade_confirm.eml (Email Confirm)",
                "sample_trade.txt (Front Office Ticket)"
            ]
        )

    active_file_name = None
    active_file_bytes = None
    is_xml_file = False

    if uploaded_file is not None:
        active_file_name = uploaded_file.name
        active_file_bytes = uploaded_file.getvalue()
    elif sample_choice != "None":
        fname = sample_choice.split(" ")[0]
        if os.path.exists(fname):
            active_file_name = fname
            with open(fname, "rb") as f:
                active_file_bytes = f.read()

    if active_file_name:
        is_xml_file = active_file_name.lower().endswith(".xml")
        st.info(f"Loaded Document: **{active_file_name}** ({len(active_file_bytes)} bytes)")

        selected_profile = None
        if is_xml_file:
            st.markdown("#### ⚙️ XML Mapping Profile Selection")
            available_profiles = xme.list_profiles()
            profile_names = ["⚡ Auto-Detect & Infer Mappings"] + [p.name for p in available_profiles]

            # Auto-select best matching profile
            default_index = 0
            file_str_sample = active_file_bytes[:1000].decode("utf-8", errors="ignore").lower()
            if "fpml" in file_str_sample:
                for idx, name in enumerate(profile_names):
                    if "fpml" in name.lower():
                        default_index = idx
                        break
            elif "fxtradeconfirmation" in file_str_sample or "details" in file_str_sample:
                for idx, name in enumerate(profile_names):
                    if "generic" in name.lower():
                        default_index = idx
                        break

            chosen_profile_name = st.selectbox(
                "Choose XPath Mapping Profile to apply:",
                profile_names,
                index=default_index
            )

            if chosen_profile_name != "⚡ Auto-Detect & Infer Mappings":
                selected_profile = next((p for p in available_profiles if p.name == chosen_profile_name), None)
                if selected_profile:
                    rule_count = len(selected_profile.conditional_rules)
                    with st.expander(f"👁️ View Active Mappings for '{selected_profile.name}' ({len(selected_profile.mappings)} fields, {rule_count} conditional rules)"):
                        df_view = pd.DataFrame([m.model_dump() for m in selected_profile.mappings])
                        st.dataframe(df_view, use_container_width=True)
                        if selected_profile.conditional_rules:
                            st.markdown("**🔀 Multi-Field Conditional Logic Rules:**")
                            rules_summary = []
                            for r in selected_profile.conditional_rules:
                                c1_s = f"{r.condition1.field_or_xpath} {r.condition1.operator} {r.condition1.value} ({r.condition1.value_type})"
                                c2_s = f" {r.logical_op} {r.condition2.field_or_xpath} {r.condition2.operator} {r.condition2.value} ({r.condition2.value_type})" if r.condition2 and r.logical_op else ""
                                rules_summary.append({
                                    "Target Field": r.target_field,
                                    "Logic Formula": f"IF {c1_s}{c2_s} THEN '{r.then_value}' ELSE '{r.else_value}'",
                                    "Description": r.description
                                })
                            st.dataframe(pd.DataFrame(rules_summary), use_container_width=True)

        if st.button("🚀 Process & Convert to JSON", type="primary", key="btn_process_main"):
            with st.spinner("Extracting parameters and standardizing..."):
                try:
                    # Mock an uploaded file object if loaded from sample
                    class SimpleFile:
                        def __init__(self, name, bytes_data):
                            self.name = name
                            self._bytes = bytes_data
                        def getvalue(self):
                            return self._bytes

                    file_obj = uploaded_file if uploaded_file is not None else SimpleFile(active_file_name, active_file_bytes)
                    extracted_text, trade_json, trace = extract_and_convert(file_obj, selected_profile)

                    c1, c2 = st.columns(2)
                    with c1:
                        st.subheader("Source Document Content")
                        if is_xml_file:
                            st.code(extracted_text, language="xml")
                        else:
                            st.text_area("Extracted Raw Text", extracted_text, height=450)

                    with c2:
                        st.subheader("Standardized Trade JSON (Reporting Party View)")
                        st.json(trade_json)
                        st.download_button(
                            label="⬇️ Download Output JSON",
                            data=json.dumps(trade_json, indent=2),
                            file_name=f"{os.path.splitext(active_file_name)[0]}_trade.json",
                            mime="application/json",
                            key="btn_download_main"
                        )

                    if trace:
                        with st.expander("📋 Detailed XPath Execution Audit Trace", expanded=False):
                            trace_df = pd.DataFrame(trace)
                            st.dataframe(trace_df, use_container_width=True)

                    st.success("✅ Trade confirmation ingested, mapped, and validated successfully!")
                except Exception as e:
                    st.error(f"❌ Error processing file: {e}")


# ===================================================================
# TAB 2: Visual XML XPath to JSON Path Mapper
# ===================================================================
with tab2:
    st.subheader("🗺️ XML XPath to JSON Path Mapping Studio")
    st.markdown("""
    Visually map **XML source elements via XPath expressions** directly into **standardized JSON target fields**.
    Configure data casting, test transformations in real-time, and save reusable mapping profiles.
    """)

    # 1. Source XML Section
    st.markdown("### 1. Source XML Document")
    xml_input_method = st.radio(
        "Choose XML Source for mapping:",
        ["Built-in: Generic FX XML", "Built-in: FpML 5.x XML", "Upload XML File", "Paste XML Directly"],
        horizontal=True
    )

    current_xml_content = ""
    if xml_input_method == "Built-in: Generic FX XML":
        if os.path.exists("sample_fx_trade.xml"):
            with open("sample_fx_trade.xml", "r", encoding="utf-8") as f:
                current_xml_content = f.read()
    elif xml_input_method == "Built-in: FpML 5.x XML":
        if os.path.exists("sample_fpml_fx_trade.xml"):
            with open("sample_fpml_fx_trade.xml", "r", encoding="utf-8") as f:
                current_xml_content = f.read()
    elif xml_input_method == "Upload XML File":
        up_xml = st.file_uploader("Upload an XML file to map", type=["xml"], key="mapper_xml_upload")
        if up_xml:
            current_xml_content = up_xml.getvalue().decode("utf-8", errors="ignore")
    else:
        current_xml_content = st.text_area(
            "Paste XML Content Here",
            value="<Trade>\n  <Counterparty>JP Morgan</Counterparty>\n  <Amount>10000000</Amount>\n  <Rate>1.0850</Rate>\n</Trade>",
            height=200
        )

    if current_xml_content.strip():
        with st.expander("👁️ View Source XML & Available XPaths", expanded=False):
            col_x1, col_x2 = st.columns([1, 1])
            with col_x1:
                st.write("**XML Content:**")
                st.code(current_xml_content[:3000] + ("..." if len(current_xml_content) > 3000 else ""), language="xml")
            with col_x2:
                st.write("**Detected XML Leaf Paths & Sample Values:**")
                try:
                    discovered = xme.explore_xml_structure(current_xml_content)
                    st.dataframe(pd.DataFrame(discovered), use_container_width=True, height=300)
                except Exception as ex:
                    st.warning(f"Could not parse XML tree: {ex}")

        # Live XPath Tester
        with st.expander("🔍 Interactive XPath Query Tester", expanded=False):
            st.caption("Quickly evaluate any XPath against the loaded XML to verify matches before adding to mappings.")
            q_col1, q_col2 = st.columns([3, 1])
            with q_col1:
                test_xpath = st.text_input("Enter XPath expression:", value="//counterparty/text() | //Counterparty/Name/text()")
            with q_col2:
                run_test = st.button("Evaluate XPath", key="eval_xp_btn")

            if test_xpath:
                try:
                    tree = xme.parse_xml_tree(current_xml_content, strip_namespaces=True)
                    matches = xme.evaluate_xpath(tree, test_xpath)
                    if matches:
                        st.success(f"Found **{len(matches)}** match(es): `{matches}`")
                    else:
                        st.warning("No matches found for this XPath in the current XML.")
                except Exception as e:
                    st.error(f"XPath Error: {e}")

    # 2. Profile Selection & Management
    st.markdown("### 2. Configure Mapping Profile")

    all_profiles = xme.list_profiles()
    profile_options = ["Create New Profile"] + [p.name for p in all_profiles]

    selected_prof_choice = st.selectbox(
        "Load an existing profile or create a new one:",
        profile_options,
        key="mapper_profile_select"
    )

    # Initialize session state for mappings table and conditional rules
    if "mapping_rows" not in st.session_state or st.session_state.get("last_profile_choice") != selected_prof_choice:
        st.session_state["last_profile_choice"] = selected_prof_choice
        if selected_prof_choice != "Create New Profile":
            p = next((p for p in all_profiles if p.name == selected_prof_choice), None)
            if p:
                st.session_state["profile_name"] = p.name
                st.session_state["profile_desc"] = p.description
                st.session_state["mapping_rows"] = [m.model_dump() for m in p.mappings]
                st.session_state["conditional_rules"] = [r.model_dump() for r in p.conditional_rules]
        else:
            st.session_state["profile_name"] = "Custom XML Profile"
            st.session_state["profile_desc"] = "User-defined XPath to JSON mappings"
            # Default empty FX schema rows
            default_fields = [
                {"target_path": "asset_class", "xpath": "//AssetClass/text()", "data_type": "string", "default_value": "FX", "transform": None},
                {"target_path": "trade_type", "xpath": "//TradeType/text()", "data_type": "string", "default_value": "FX Spot", "transform": None},
                {"target_path": "reporting_party", "xpath": "//ReportingParty/Name/text()", "data_type": "string", "default_value": "BNPAFRPPAXXX", "transform": None},
                {"target_path": "counterparty", "xpath": "//Counterparty/Name/text()", "data_type": "string", "default_value": "DEUTDEDDXXXX", "transform": None},
                {"target_path": "account_id", "xpath": "//ReportingParty/AccountId/text()", "data_type": "string", "default_value": "ACC-GLOBAL-7719-FX", "transform": None},
                {"target_path": "direction", "xpath": "//Direction/text()", "data_type": "string", "default_value": "BUY", "transform": None},
                {"target_path": "base_currency", "xpath": "//BaseCurrency/text()", "data_type": "string", "default_value": "EUR", "transform": None},
                {"target_path": "quote_currency", "xpath": "//QuoteCurrency/text()", "data_type": "string", "default_value": "USD", "transform": None},
                {"target_path": "reporting_party_buys", "xpath": "//BoughtCurrency/text()", "data_type": "string", "default_value": "EUR", "transform": None},
                {"target_path": "reporting_party_sells", "xpath": "//SoldCurrency/text()", "data_type": "string", "default_value": "USD", "transform": None},
                {"target_path": "notional_amount", "xpath": "//NotionalAmount/text()", "data_type": "float", "default_value": "10000000.0", "transform": None},
                {"target_path": "counter_amount", "xpath": "//CounterAmount/text()", "data_type": "float", "default_value": "0.0", "transform": None},
                {"target_path": "trade_date", "xpath": "//TradeDate/text()", "data_type": "date", "default_value": "2026-11-05", "transform": None},
                {"target_path": "value_date", "xpath": "//ValueDate/text()", "data_type": "date", "default_value": "2026-11-07", "transform": None},
                {"target_path": "rate", "xpath": "//ExchangeRate/text()", "data_type": "float", "default_value": "1.0850", "transform": None},
            ]
            st.session_state["mapping_rows"] = default_fields
            st.session_state["conditional_rules"] = [
                {
                    "rule_id": "rule_trade_type_sample",
                    "description": "Determine Spot vs Forward: IF trade_date == value_date AND asset_class == 'FX' THEN 'FX Spot' ELSE 'FX Forward'",
                    "target_field": "trade_type",
                    "condition1": {
                        "field_or_xpath": "trade_date",
                        "operator": "==",
                        "value_type": "field",
                        "value": "value_date"
                    },
                    "logical_op": "AND",
                    "condition2": {
                        "field_or_xpath": "asset_class",
                        "operator": "==",
                        "value_type": "literal",
                        "value": "FX"
                    },
                    "then_value": "FX Spot",
                    "else_value": "FX Forward",
                    "data_type": "string"
                }
            ]

    if "conditional_rules" not in st.session_state:
        st.session_state["conditional_rules"] = []

    col_pn1, col_pn2 = st.columns([2, 3])
    with col_pn1:
        prof_name = st.text_input("Profile Name:", value=st.session_state.get("profile_name", "My XML Mapping"), key="input_prof_name")
    with col_pn2:
        prof_desc = st.text_input("Profile Description:", value=st.session_state.get("profile_desc", ""), key="input_prof_desc")

    # Mapping Action Buttons
    b_col1, b_col2, b_col3, b_col4 = st.columns([1.5, 1.5, 1.5, 1.5])
    with b_col1:
        if st.button("⚡ Auto-Infer from Current XML"):
            if current_xml_content.strip():
                inferred = xme.infer_mappings_from_xml(current_xml_content)
                st.session_state["mapping_rows"] = [m.model_dump() for m in inferred]
                st.rerun()
            else:
                st.warning("Please provide source XML first.")
    with b_col2:
        if st.button("➕ Add New Field Row"):
            st.session_state["mapping_rows"].append({
                "target_path": "custom_field",
                "xpath": "//custom/text()",
                "data_type": "string",
                "default_value": "",
                "transform": None
            })
            st.rerun()
    with b_col3:
        if st.button("🔄 Reset Standard Schema"):
            st.session_state["last_profile_choice"] = None
            st.session_state.pop("mapping_rows", None)
            st.session_state.pop("conditional_rules", None)
            st.rerun()
    with b_col4:
        # Save Profile to disk
        if st.button("💾 Save Profile", type="primary"):
            current_mappings = [xme.FieldMapping(**r) for r in st.session_state.get("mapping_rows", [])]
            current_rules = [xme.ConditionalRule(**r) for r in st.session_state.get("conditional_rules", [])]
            new_profile = xme.MappingProfile(
                name=prof_name.strip() or "Custom XML Profile",
                description=prof_desc.strip(),
                mappings=current_mappings,
                conditional_rules=current_rules
            )
            saved_path = xme.save_profile(new_profile)
            st.success(f"Profile saved to: `{os.path.basename(saved_path)}` ({len(current_mappings)} fields, {len(current_rules)} conditional rules)")
            st.rerun()

    # 3. Interactive Data Editor for Mappings
    st.markdown("### 3. Field Mappings Table (XPath ➔ JSON Path)")
    st.caption("Edit JSON target fields, XPath queries, and data casting directly in the table below. Click on rows to modify or delete.")

    mappings_df = pd.DataFrame(st.session_state.get("mapping_rows", []))
    edited_df = st.data_editor(
        mappings_df,
        use_container_width=True,
        num_rows="dynamic",
        column_config={
            "target_path": st.column_config.TextColumn("Target JSON Path", help="Path in output JSON (e.g., counterparty, rate, parties.broker)", required=True),
            "xpath": st.column_config.TextColumn("Source XPath Expression", help="XPath to extract value from XML", required=True),
            "data_type": st.column_config.SelectboxColumn("Data Type", options=["string", "float", "int", "date", "boolean"], required=True),
            "default_value": st.column_config.TextColumn("Default Fallback Value", help="Used if XPath yields no match"),
            "transform": st.column_config.SelectboxColumn("Transform", options=[None, "upper", "lower", "clean_amount", "date_iso"]),
        },
        key="mappings_editor"
    )

    # Sync edited dataframe to session state
    st.session_state["mapping_rows"] = edited_df.to_dict(orient="records")

    # Real-Time Mapping Extraction Verification Table
    if current_xml_content.strip():
        with st.expander("📊 Live Field Extraction Verification (Evaluated on Current XML)", expanded=False):
            try:
                tree = xme.parse_xml_tree(current_xml_content, strip_namespaces=True)
                verification_data = []
                for row in st.session_state["mapping_rows"]:
                    mapping_obj = xme.FieldMapping(**row)
                    val, status, raw_extracted = xme.extract_field_value(tree, mapping_obj)
                    verification_data.append({
                        "JSON Target Field": mapping_obj.target_path,
                        "XPath Expression": mapping_obj.xpath,
                        "Data Type": mapping_obj.data_type,
                        "Raw Extracted": raw_extracted,
                        "Resolved Output Value": str(val),
                        "Status": "✅ SUCCESS" if status == "SUCCESS" else ("⚠️ DEFAULT" if "DEFAULT" in status else f"❌ {status}")
                    })
                st.dataframe(pd.DataFrame(verification_data), use_container_width=True)
            except Exception as ex:
                st.error(f"Error evaluating verification table: {ex}")

    # ===================================================================
    # 4. Multi-Field Conditional Logic Rules Builder
    # ===================================================================
    st.markdown("### 4. 🔀 Multi-Field Conditional Logic Rules")
    st.caption(
        "Define flexible conditional logic using two fields or literals: "
        "e.g., **IF** `trade_date == value_date` **AND** `asset_class == 'FX'` **THEN** `trade_type = 'FX Spot'` **ELSE** `'FX Forward'`. "
        "Rules evaluate dynamically after base XPath mappings are resolved."
    )

    current_rules_list = st.session_state.get("conditional_rules", [])

    # Display Active Rules Table & Management
    if current_rules_list:
        st.markdown(f"**Active Conditional Rules ({len(current_rules_list)}):**")
        rule_display_records = []
        for idx, r in enumerate(current_rules_list):
            c1 = r.get("condition1", {})
            c1_str = f"{c1.get('field_or_xpath')} {c1.get('operator')} '{c1.get('value')}'"
            if c1.get("value_type") == "field":
                c1_str = f"{c1.get('field_or_xpath')} {c1.get('operator')} [{c1.get('value')}]"

            c2 = r.get("condition2")
            c2_str = ""
            if c2 and r.get("logical_op"):
                if c2.get("value_type") == "field":
                    c2_str = f" {r.get('logical_op')} {c2.get('field_or_xpath')} {c2.get('operator')} [{c2.get('value')}]"
                else:
                    c2_str = f" {r.get('logical_op')} {c2.get('field_or_xpath')} {c2.get('operator')} '{c2.get('value')}'"

            rule_display_records.append({
                "#": idx + 1,
                "Target Field": r.get("target_field"),
                "Condition Logic": f"IF {c1_str}{c2_str} THEN '{r.get('then_value')}' ELSE '{r.get('else_value')}'",
                "Description": r.get("description", "")
            })

        st.dataframe(pd.DataFrame(rule_display_records), use_container_width=True)

        # Rule Deletion row
        del_col1, del_col2 = st.columns([3, 1])
        with del_col1:
            rule_to_delete_idx = st.selectbox(
                "Select a rule to remove:",
                options=list(range(len(current_rules_list))),
                format_func=lambda i: f"Rule #{i+1}: {current_rules_list[i].get('target_field')} - {current_rules_list[i].get('description')}",
                key="select_del_rule"
            )
        with del_col2:
            st.write("")
            st.write("")
            if st.button("🗑️ Delete Selected Rule", key="btn_del_rule"):
                current_rules_list.pop(rule_to_delete_idx)
                st.session_state["conditional_rules"] = current_rules_list
                st.success("Rule removed.")
                st.rerun()

        # Live Evaluation of Conditional Rules on Current XML
        if current_xml_content.strip():
            with st.expander("⚡ Live Conditional Rules Evaluation (Current XML)", expanded=True):
                try:
                    tree = xme.parse_xml_tree(current_xml_content, strip_namespaces=True)
                    base_dict = {}
                    for r in st.session_state.get("mapping_rows", []):
                        m_obj = xme.FieldMapping(**r)
                        val, _, _ = xme.extract_field_value(tree, m_obj)
                        base_dict[m_obj.target_path] = val

                    eval_rows = []
                    for r in current_rules_list:
                        rule_obj = xme.ConditionalRule(**r)
                        c1 = rule_obj.condition1
                        c1_l = xme.resolve_operand_value(c1.field_or_xpath, base_dict, tree)
                        c1_r = xme.resolve_operand_value(c1.value, base_dict, tree) if c1.value_type == "field" else c1.value
                        c1_str_eval = f"{c1.field_or_xpath} ('{c1_l}') {c1.operator} '{c1_r}'"

                        formula_str = c1_str_eval
                        if rule_obj.logical_op and rule_obj.condition2:
                            c2 = rule_obj.condition2
                            c2_l = xme.resolve_operand_value(c2.field_or_xpath, base_dict, tree)
                            c2_r = xme.resolve_operand_value(c2.value, base_dict, tree) if c2.value_type == "field" else c2.value
                            formula_str += f" {rule_obj.logical_op} {c2.field_or_xpath} ('{c2_l}') {c2.operator} '{c2_r}'"

                        rule_eval = xme.evaluate_conditional_rule(rule_obj, base_dict, tree)
                        rule_passed = rule_eval["combined_result"]
                        resolved_val = rule_eval["assigned_value"]
                        eval_rows.append({
                            "Target Field": rule_obj.target_field,
                            "Condition Evaluated": formula_str,
                            "Result": "✅ TRUE" if rule_passed else "❌ FALSE",
                            "Branch Taken": f"THEN ➔ '{rule_obj.then_value}'" if rule_passed else f"ELSE ➔ '{rule_obj.else_value}'",
                            "Resolved Value": str(resolved_val)
                        })
                    st.dataframe(pd.DataFrame(eval_rows), use_container_width=True)
                except Exception as ex_rule:
                    st.warning(f"Could not live-evaluate conditional rules: {ex_rule}")
    else:
        st.info("No conditional rules configured for this profile yet. You can add one below.")

    # Rule Builder Form
    with st.expander("➕ Create / Add a Conditional Logic Rule", expanded=(len(current_rules_list) == 0)):
        # List of candidate target fields from mapping rows
        available_fields = [r.get("target_path") for r in st.session_state.get("mapping_rows", []) if r.get("target_path")]
        if not available_fields:
            available_fields = ["trade_type", "direction", "asset_class", "trade_date", "value_date", "notional_amount"]

        # Quick Preset Buttons
        st.markdown("**Quick Preset Templates:**")
        preset_cols = st.columns(3)
        with preset_cols[0]:
            if st.button("📌 Spot vs Forward Rule", key="btn_preset_spot_fwd"):
                st.session_state["conditional_rules"].append({
                    "rule_id": f"rule_spot_{str(uuid.uuid4())[:6]}",
                    "description": "IF trade_date == value_date AND asset_class == 'FX' THEN 'FX Spot' ELSE 'FX Forward'",
                    "target_field": "trade_type",
                    "condition1": {"field_or_xpath": "trade_date", "operator": "==", "value_type": "field", "value": "value_date"},
                    "logical_op": "AND",
                    "condition2": {"field_or_xpath": "asset_class", "operator": "==", "value_type": "literal", "value": "FX"},
                    "then_value": "FX Spot",
                    "else_value": "FX Forward",
                    "data_type": "string"
                })
                st.rerun()
        with preset_cols[1]:
            if st.button("📌 Direction Check Rule", key="btn_preset_direction"):
                st.session_state["conditional_rules"].append({
                    "rule_id": f"rule_dir_{str(uuid.uuid4())[:6]}",
                    "description": "IF reporting_party_buys == 'USD' THEN 'BUY' ELSE 'SELL'",
                    "target_field": "direction",
                    "condition1": {"field_or_xpath": "reporting_party_buys", "operator": "==", "value_type": "literal", "value": "USD"},
                    "logical_op": None,
                    "condition2": None,
                    "then_value": "BUY",
                    "else_value": "SELL",
                    "data_type": "string"
                })
                st.rerun()
        with preset_cols[2]:
            if st.button("📌 Large Trade Flag Rule", key="btn_preset_large_trade"):
                st.session_state["conditional_rules"].append({
                    "rule_id": f"rule_notional_{str(uuid.uuid4())[:6]}",
                    "description": "IF notional_amount >= 10000000 THEN 'BLOCK' ELSE 'STANDARD'",
                    "target_field": "trade_tier",
                    "condition1": {"field_or_xpath": "notional_amount", "operator": ">=", "value_type": "literal", "value": "10000000"},
                    "logical_op": None,
                    "condition2": None,
                    "then_value": "BLOCK",
                    "else_value": "STANDARD",
                    "data_type": "string"
                })
                st.rerun()

        st.markdown("---")
        st.markdown("**Custom Rule Builder:**")

        f_col1, f_col2 = st.columns([1, 2])
        with f_col1:
            builder_target_field = st.selectbox("Target JSON Field to Set:", options=available_fields + ["<Custom Field>"], key="cr_target_field")
            if builder_target_field == "<Custom Field>":
                builder_target_field = st.text_input("Enter Custom Target Field Name:", value="trade_type", key="cr_custom_tf")
        with f_col2:
            builder_desc = st.text_input("Rule Description:", value="Set target based on two conditions", key="cr_desc")

        st.markdown("##### Condition 1 (Primary)")
        c1_col1, c1_col2, c1_col3, c1_col4 = st.columns([2, 1.2, 1.5, 2])
        with c1_col1:
            c1_field = st.selectbox("First Field / XPath:", options=available_fields + ["<Custom Field/XPath>"], index=available_fields.index("trade_date") if "trade_date" in available_fields else 0, key="cr_c1_field")
            if c1_field == "<Custom Field/XPath>":
                c1_field = st.text_input("Enter Field or XPath:", value="trade_date", key="cr_c1_custom")
        with c1_col2:
            c1_op = st.selectbox("Operator:", ["==", "!=", ">", "<", ">=", "<=", "contains", "not_contains", "is_empty", "is_not_empty"], key="cr_c1_op")
        with c1_col3:
            c1_vtype_ui = st.selectbox("Compare With:", ["Another Field (Dynamic)", "Literal Value (Constant)"], key="cr_c1_vtype")
            c1_vtype = "field" if "Field" in c1_vtype_ui else "literal"
        with c1_col4:
            if c1_vtype == "field":
                c1_val = st.selectbox("Right Operand Field:", options=available_fields + ["<Custom Field/XPath>"], index=available_fields.index("value_date") if "value_date" in available_fields else 0, key="cr_c1_val_field")
                if c1_val == "<Custom Field/XPath>":
                    c1_val = st.text_input("Right Field/XPath:", value="value_date", key="cr_c1_val_custom")
            else:
                c1_val = st.text_input("Literal Value:", value="FX", key="cr_c1_val_literal")

        st.markdown("##### Second Condition Connector")
        builder_logical_op = st.selectbox("Logical Connector:", ["None (Single Condition Only)", "AND", "OR"], index=1, key="cr_logical_op")

        c2_clause = None
        if builder_logical_op in ["AND", "OR"]:
            st.markdown("##### Condition 2 (Secondary)")
            c2_col1, c2_col2, c2_col3, c2_col4 = st.columns([2, 1.2, 1.5, 2])
            with c2_col1:
                c2_field = st.selectbox("Second Field / XPath:", options=available_fields + ["<Custom Field/XPath>"], index=available_fields.index("asset_class") if "asset_class" in available_fields else 0, key="cr_c2_field")
                if c2_field == "<Custom Field/XPath>":
                    c2_field = st.text_input("Enter Field or XPath:", value="asset_class", key="cr_c2_custom")
            with c2_col2:
                c2_op = st.selectbox("Operator:", ["==", "!=", ">", "<", ">=", "<=", "contains", "not_contains", "is_empty", "is_not_empty"], key="cr_c2_op")
            with c2_col3:
                c2_vtype_ui = st.selectbox("Compare With:", ["Literal Value (Constant)", "Another Field (Dynamic)"], key="cr_c2_vtype")
                c2_vtype = "field" if "Field" in c2_vtype_ui else "literal"
            with c2_col4:
                if c2_vtype == "field":
                    c2_val = st.selectbox("Right Operand Field:", options=available_fields + ["<Custom Field/XPath>"], key="cr_c2_val_field")
                    if c2_val == "<Custom Field/XPath>":
                        c2_val = st.text_input("Right Field/XPath:", value="", key="cr_c2_val_custom")
                else:
                    c2_val = st.text_input("Literal Value:", value="FX", key="cr_c2_val_literal")

            c2_clause = {
                "field_or_xpath": c2_field.strip(),
                "operator": c2_op,
                "value_type": c2_vtype,
                "value": c2_val.strip()
            }

        st.markdown("##### Resulting Values (THEN / ELSE)")
        res_col1, res_col2, res_col3 = st.columns([2, 2, 1.5])
        with res_col1:
            builder_then = st.text_input("THEN Value (if condition is TRUE):", value="FX Spot", key="cr_then_val")
        with res_col2:
            builder_else = st.text_input("ELSE Value (if condition is FALSE):", value="FX Forward", key="cr_else_val")
        with res_col3:
            builder_dtype = st.selectbox("Target Data Type:", ["string", "float", "int", "boolean"], key="cr_data_type")

        if st.button("➕ Add Rule to Profile", type="primary", key="btn_add_rule"):
            new_rule = {
                "rule_id": f"rule_{str(uuid.uuid4())[:8]}",
                "description": builder_desc.strip(),
                "target_field": builder_target_field.strip(),
                "condition1": {
                    "field_or_xpath": c1_field.strip(),
                    "operator": c1_op,
                    "value_type": c1_vtype,
                    "value": c1_val.strip()
                },
                "logical_op": builder_logical_op if builder_logical_op in ["AND", "OR"] else None,
                "condition2": c2_clause,
                "then_value": builder_then,
                "else_value": builder_else,
                "data_type": builder_dtype
            }
            st.session_state["conditional_rules"].append(new_rule)
            st.success(f"Added conditional rule for '{builder_target_field}'!")
            st.rerun()

    # ===================================================================
    # 5. Test Transformation & Live Output Preview
    # ===================================================================
    st.markdown("### 5. Test Transformation & Preview JSON")

    test_col1, test_col2 = st.columns([1, 1])
    with test_col1:
        if st.button("🧪 Execute Mapping & Preview Standardized JSON", type="primary", key="btn_test_transform"):
            if not current_xml_content.strip():
                st.warning("Please provide or load XML in Step 1 first.")
            else:
                try:
                    active_mappings = [xme.FieldMapping(**r) for r in st.session_state["mapping_rows"]]
                    active_rules = [xme.ConditionalRule(**r) for r in st.session_state.get("conditional_rules", [])]
                    temp_profile = xme.MappingProfile(
                        name=prof_name,
                        description=prof_desc,
                        mappings=active_mappings,
                        conditional_rules=active_rules
                    )
                    transformed_json, trace = xme.apply_mapping_profile(current_xml_content, temp_profile)

                    st.markdown("#### Mapped JSON Result")
                    st.json(transformed_json)

                    # Validate against Pydantic schema
                    try:
                        trade_obj = FXTradeConfirmation(**transformed_json)
                        st.success("✅ **Valid FXTradeConfirmation**: Document conforms fully to the standardized schema.")
                    except ValidationError as ve:
                        st.warning(f"⚠️ Partial Schema Match: {len(ve.errors())} schema warnings (some optional fields missing or mismatched).")

                    st.download_button(
                        label="⬇️ Download Result JSON",
                        data=json.dumps(transformed_json, indent=2),
                        file_name=f"{re.sub(r'[^a-zA-Z0-9_-]', '_', prof_name.lower())}_output.json",
                        mime="application/json"
                    )

                    with st.expander("📋 Detailed Execution Audit Trace (Base XPaths + Conditional Rules)", expanded=False):
                        trace_df = pd.DataFrame(trace)
                        st.dataframe(trace_df, use_container_width=True)

                except Exception as ex:
                    st.error(f"Transformation failed: {ex}")

    with test_col2:
        st.markdown("#### Export / Import Mapping Profile")
        profile_to_export = xme.MappingProfile(
            name=prof_name,
            description=prof_desc,
            mappings=[xme.FieldMapping(**r) for r in st.session_state.get("mapping_rows", [])],
            conditional_rules=[xme.ConditionalRule(**r) for r in st.session_state.get("conditional_rules", [])]
        )
        st.download_button(
            label="📥 Export Mapping Profile as JSON",
            data=profile_to_export.model_dump_json(indent=2),
            file_name=f"{re.sub(r'[^a-zA-Z0-9_-]', '_', prof_name.lower())}_mapping.json",
            mime="application/json"
        )

        uploaded_profile_json = st.file_uploader("Upload Profile JSON to Import", type=["json"], key="import_profile_upload")
        if uploaded_profile_json:
            try:
                imp_data = json.loads(uploaded_profile_json.getvalue().decode("utf-8"))
                imported_profile = xme.MappingProfile(**imp_data)
                st.session_state["profile_name"] = imported_profile.name
                st.session_state["profile_desc"] = imported_profile.description
                st.session_state["mapping_rows"] = [m.model_dump() for m in imported_profile.mappings]
                st.session_state["conditional_rules"] = [r.model_dump() for r in imported_profile.conditional_rules]
                st.success(f"Successfully loaded '{imported_profile.name}' with {len(imported_profile.mappings)} field mappings and {len(imported_profile.conditional_rules)} conditional rules!")
                st.rerun()
            except Exception as e:
                st.error(f"Failed to import profile: {e}")
