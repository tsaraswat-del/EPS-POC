"""
XML Mapping Engine for FX Trade Confirmations.
Enables parsing XML documents, evaluating XPath queries, mapping XPath to JSON Paths,
and converting XML trade documents to standardized JSON models.
"""

import os
import re
import json
from typing import Any, Dict, List, Optional, Tuple, Union
from pydantic import BaseModel, Field
import lxml.etree as ET

MAPPINGS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mappings")
os.makedirs(MAPPINGS_DIR, exist_ok=True)


class FieldMapping(BaseModel):
    target_path: str = Field(description="Target JSON path or field name, e.g. 'counterparty' or 'parties.buyer'")
    xpath: str = Field(description="XPath query to extract the value from XML, e.g. '//Trade/Counterparty/Name/text()'")
    data_type: str = Field(default="string", description="Type casting: string, float, int, date, boolean")
    default_value: Optional[str] = Field(default=None, description="Fallback value if XPath yields nothing")
    transform: Optional[str] = Field(default=None, description="Optional transformation: upper, lower, clean_amount, date_iso")


class MappingProfile(BaseModel):
    name: str = Field(description="Name of the mapping profile")
    description: str = Field(default="", description="Description of the profile or format")
    mappings: List[FieldMapping] = Field(default_factory=list)


def clean_amount(val_str: Any) -> float:
    """Cleans currency strings, removes symbols, handles K/M/MM suffixes, and detects comma/dot formatting."""
    if val_str is None:
        return 0.0
    if isinstance(val_str, (int, float)):
        return float(val_str)
    s = str(val_str).strip().upper().replace("$", "").replace("€", "").replace("£", "")
    
    multiplier = 1.0
    if s.endswith("MM"):
        multiplier = 1_000_000.0
        s = s[:-2].strip()
    elif s.endswith("M"):
        multiplier = 1_000_000.0
        s = s[:-1].strip()
    elif s.endswith("K"):
        multiplier = 1_000.0
        s = s[:-1].strip()

    if "," in s and "." in s:
        if s.rfind(".") > s.rfind(","):
            s = s.replace(",", "")
        else:
            s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        if s.count(",") > 1:
            s = s.replace(",", "")
        else:
            parts = s.split(",")
            if len(parts[1]) in (1, 2):
                s = s.replace(",", ".")
            else:
                s = s.replace(",", "")
    try:
        return float(s.rstrip(".")) * multiplier
    except Exception:
        return 0.0


def normalize_date(date_str: Any) -> str:
    """Normalizes various date formats into ISO 8601 YYYY-MM-DD."""
    if not date_str:
        return ""
    s = str(date_str).strip().rstrip(".,;")
    # Strip time component if ISO timestamp (e.g. 2026-11-05T14:30:00Z)
    if "T" in s:
        s = s.split("T")[0]
    if re.match(r"^\d{8}$", s):
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$", s)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    return s


def parse_xml_tree(xml_content: Union[str, bytes], strip_namespaces: bool = True) -> ET._Element:
    """
    Parses an XML string/bytes into an lxml Element.
    Optionally strips XML namespaces so standard simplified XPath queries
    (e.g., //trade/fxSingleLeg) work without tedious prefix declarations.
    """
    if isinstance(xml_content, str):
        xml_bytes = xml_content.encode("utf-8")
    else:
        xml_bytes = xml_content

    parser = ET.XMLParser(recover=True, remove_blank_text=True)
    tree = ET.fromstring(xml_bytes, parser=parser)

    if strip_namespaces:
        for elem in tree.iter():
            if isinstance(elem.tag, str) and "}" in elem.tag:
                elem.tag = elem.tag.split("}", 1)[1]
    return tree


def evaluate_xpath(tree: ET._Element, xpath_expr: str) -> List[Any]:
    """
    Evaluates an XPath expression against an lxml element tree.
    Returns a list of extracted string values or raw matches.
    """
    if not xpath_expr or not xpath_expr.strip():
        return []

    expr = xpath_expr.strip()
    try:
        results = tree.xpath(expr)
    except ET.XPathEvalError as e:
        # Fallback: if user specified an element path without /text(), try evaluating as element
        raise ValueError(f"Invalid XPath expression '{expr}': {e}")

    extracted = []
    if isinstance(results, list):
        for item in results:
            if isinstance(item, ET._Element):
                # If element has text, extract it, or join text content
                text = "".join(item.itertext()).strip()
                extracted.append(text)
            elif isinstance(item, (str, bytes)):
                val = item.decode("utf-8") if isinstance(item, bytes) else str(item)
                if val.strip():
                    extracted.append(val.strip())
            else:
                extracted.append(str(item).strip())
    elif isinstance(results, (str, int, float, bool)):
        extracted.append(str(results).strip())
    
    return extracted


def set_json_path(target_dict: dict, json_path: str, value: Any):
    """
    Sets a value in a nested dictionary/JSON structure given a dot-delimited path.
    Supports paths like 'counterparty', 'parties.reporting_party', 'trade.details[0].id'.
    """
    parts = re.split(r"\.(?![^\[]*\])", json_path.strip())
    curr = target_dict

    for i, part in enumerate(parts[:-1]):
        # Check for array index notation: field[0]
        array_match = re.match(r"^(\w+)\[(\d+)\]$", part)
        if array_match:
            key, idx = array_match.group(1), int(array_match.group(2))
            if key not in curr or not isinstance(curr[key], list):
                curr[key] = []
            while len(curr[key]) <= idx:
                curr[key].append({})
            curr = curr[key][idx]
        else:
            if part not in curr or not isinstance(curr[part], dict):
                curr[part] = {}
            curr = curr[part]

    last_part = parts[-1]
    array_match = re.match(r"^(\w+)\[(\d+)\]$", last_part)
    if array_match:
        key, idx = array_match.group(1), int(array_match.group(2))
        if key not in curr or not isinstance(curr[key], list):
            curr[key] = []
        while len(curr[key]) <= idx:
            curr[key].append(None)
        curr[key][idx] = value
    else:
        curr[last_part] = value


def cast_value(val: Any, data_type: str, transform: Optional[str] = None) -> Any:
    """Applies type casting and optional transforms to an extracted string value."""
    if val is None:
        return None

    str_val = str(val).strip()
    if transform == "upper":
        str_val = str_val.upper()
    elif transform == "lower":
        str_val = str_val.lower()

    dtype = data_type.lower()
    if dtype == "float" or transform == "clean_amount":
        return clean_amount(str_val)
    elif dtype in ["int", "integer"]:
        try:
            return int(round(clean_amount(str_val)))
        except Exception:
            return 0
    elif dtype in ["date", "date_iso"] or transform == "date_iso":
        return normalize_date(str_val)
    elif dtype in ["bool", "boolean"]:
        return str_val.lower() in ("true", "1", "yes", "y")
    else:
        return str_val


def extract_field_value(tree: ET._Element, mapping: FieldMapping) -> Tuple[Any, str, str]:
    """
    Extracts, transforms, and casts a single field mapping from an XML tree.
    Returns: (final_value, status, raw_extracted_str)
    """
    raw_str = ""
    status = "SUCCESS"

    try:
        matches = evaluate_xpath(tree, mapping.xpath)
        if matches:
            raw_str = matches[0]
            val = cast_value(raw_str, mapping.data_type, mapping.transform)
        else:
            if mapping.default_value is not None and mapping.default_value != "":
                raw_str = mapping.default_value
                val = cast_value(raw_str, mapping.data_type, mapping.transform)
                status = "DEFAULT_USED"
            else:
                val = "" if mapping.data_type == "string" else (0.0 if mapping.data_type == "float" else None)
                status = "NOT_FOUND"
    except Exception as e:
        if mapping.default_value is not None and mapping.default_value != "":
            raw_str = mapping.default_value
            val = cast_value(raw_str, mapping.data_type, mapping.transform)
            status = f"ERROR_FALLBACK ({e})"
        else:
            val = None
            status = f"ERROR ({e})"

    return val, status, raw_str


def apply_mapping_profile(xml_content: Union[str, bytes], profile: MappingProfile) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """
    Applies a MappingProfile against an XML document.
    Returns:
      (result_dict, execution_trace)
    """
    tree = parse_xml_tree(xml_content, strip_namespaces=True)
    result_dict: Dict[str, Any] = {}
    trace: List[Dict[str, Any]] = []

    for mapping in profile.mappings:
        val, status, raw_extracted = extract_field_value(tree, mapping)
        set_json_path(result_dict, mapping.target_path, val)
        trace.append({
            "target_path": mapping.target_path,
            "xpath": mapping.xpath,
            "data_type": mapping.data_type,
            "extracted_raw": raw_extracted,
            "final_value": val,
            "status": status
        })

    # Ensure direction & currencies alignment if standard FX fields exist
    if "reporting_party_buys" in result_dict and "base_currency" not in result_dict:
        result_dict["base_currency"] = result_dict["reporting_party_buys"]
    if "reporting_party_sells" in result_dict and "quote_currency" not in result_dict:
        result_dict["quote_currency"] = result_dict["reporting_party_sells"]
    if "counter_amount" in result_dict and (result_dict["counter_amount"] == 0.0 or not result_dict["counter_amount"]):
        if result_dict.get("notional_amount") and result_dict.get("rate"):
            result_dict["counter_amount"] = round(float(result_dict["notional_amount"]) * float(result_dict["rate"]), 2)

    return result_dict, trace


def explore_xml_structure(xml_content: Union[str, bytes]) -> List[Dict[str, str]]:
    """
    Inspects an XML document and returns all leaf element XPaths, attributes, and their sample values.
    Used by the UI to help users choose or click XPaths effortlessly.
    """
    tree = parse_xml_tree(xml_content, strip_namespaces=True)
    discovered: List[Dict[str, str]] = []
    seen_paths = set()

    def _traverse(elem: ET._Element, current_path: str):
        tag = elem.tag
        path = f"{current_path}/{tag}" if current_path else f"/{tag}"

        # Attributes on this element
        for attr_k, attr_v in elem.attrib.items():
            attr_xpath = f"{path}/@{attr_k}"
            if attr_xpath not in seen_paths:
                seen_paths.add(attr_xpath)
                discovered.append({
                    "xpath": attr_xpath,
                    "sample_value": str(attr_v),
                    "type": "attribute"
                })

        children = list(elem)
        if not children:
            # Leaf element
            text_val = (elem.text or "").strip()
            leaf_xpath = f"{path}/text()"
            if leaf_xpath not in seen_paths:
                seen_paths.add(leaf_xpath)
                discovered.append({
                    "xpath": leaf_xpath,
                    "sample_value": text_val,
                    "type": "element_text"
                })
        else:
            for child in children:
                _traverse(child, path)

    _traverse(tree, "")
    return discovered


def infer_mappings_from_xml(xml_content: Union[str, bytes]) -> List[FieldMapping]:
    """
    Automatically infers mappings from an XML document by matching XML tags against known FX trade fields.
    """
    elements = explore_xml_structure(xml_content)
    tag_map = {item["xpath"]: item["sample_value"] for item in elements}

    STANDARD_FIELDS = [
        {"field": "asset_class", "type": "string", "default": "FX", "patterns": [r"assetclass", r"asset_class"]},
        {"field": "trade_type", "type": "string", "default": "FX Spot", "patterns": [r"tradetype", r"trade_type", r"producttype", r"instrument"]},
        {"field": "reporting_party", "type": "string", "default": "BNPAFRPPAXXX", "patterns": [r"reportingparty", r"party1", r"sentby", r"reporting_party", r"buyer"]},
        {"field": "counterparty", "type": "string", "default": "DEUTDEDDXXXX", "patterns": [r"counterparty", r"party2", r"sendto", r"dealer", r"broker"]},
        {"field": "account_id", "type": "string", "default": "ACC-GLOBAL-7719-FX", "patterns": [r"accountid", r"account_id", r"account"]},
        {"field": "direction", "type": "string", "default": "BUY", "patterns": [r"direction", r"side"]},
        {"field": "base_currency", "type": "string", "default": "EUR", "patterns": [r"basecurrency", r"boughtcurrency", r"currency1"]},
        {"field": "quote_currency", "type": "string", "default": "USD", "patterns": [r"quotecurrency", r"soldcurrency", r"currency2"]},
        {"field": "reporting_party_buys", "type": "string", "default": "EUR", "patterns": [r"boughtcurrency", r"currency1", r"buys"]},
        {"field": "reporting_party_sells", "type": "string", "default": "USD", "patterns": [r"soldcurrency", r"currency2", r"sells"]},
        {"field": "notional_amount", "type": "float", "default": "1000000.0", "patterns": [r"notionalamount", r"notional", r"amount"]},
        {"field": "counter_amount", "type": "float", "default": "0.0", "patterns": [r"counteramount", r"settlementamount"]},
        {"field": "trade_date", "type": "date", "default": "2026-11-05", "patterns": [r"tradedate", r"trade_date", r"executiondate"]},
        {"field": "value_date", "type": "date", "default": "2026-11-07", "patterns": [r"valuedate", r"value_date", r"settlementdate", r"maturitydate"]},
        {"field": "rate", "type": "float", "default": "1.0850", "patterns": [r"exchangerate", r"rate", r"strike", r"price"]},
    ]

    generated_mappings: List[FieldMapping] = []
    used_xpaths = set()

    for sf in STANDARD_FIELDS:
        matched_xpath = None
        for pattern in sf["patterns"]:
            for item in elements:
                clean_xp = re.sub(r"[/@_]", "", item["xpath"].lower())
                if re.search(pattern, clean_xp) and item["xpath"] not in used_xpaths:
                    matched_xpath = item["xpath"]
                    used_xpaths.add(matched_xpath)
                    break
            if matched_xpath:
                break

        if matched_xpath:
            generated_mappings.append(FieldMapping(
                target_path=sf["field"],
                xpath=matched_xpath,
                data_type=sf["type"],
                default_value=sf["default"]
            ))
        else:
            generated_mappings.append(FieldMapping(
                target_path=sf["field"],
                xpath=f"//{sf['field']}/text()",
                data_type=sf["type"],
                default_value=sf["default"]
            ))

    return generated_mappings


# -------------------------------------------------------------------
# Profile Storage & Defaults
# -------------------------------------------------------------------
def save_profile(profile: MappingProfile) -> str:
    """Saves a MappingProfile to disk as a JSON file."""
    filename = f"{re.sub(r'[^a-zA-Z0-9_-]', '_', profile.name.lower())}.json"
    filepath = os.path.join(MAPPINGS_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(profile.model_dump_json(indent=2))
    return filepath


def load_profile(filepath_or_name: str) -> Optional[MappingProfile]:
    """Loads a MappingProfile by file name, path, or profile name."""
    # Check if exact path or with .json exists
    candidates = [
        filepath_or_name,
        f"{filepath_or_name}.json",
        os.path.join(MAPPINGS_DIR, filepath_or_name),
        os.path.join(MAPPINGS_DIR, f"{filepath_or_name}.json"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            with open(c, "r", encoding="utf-8") as f:
                return MappingProfile(**json.load(f))

    # Search existing profiles in MAPPINGS_DIR
    clean_search = re.sub(r"[^a-zA-Z0-9]", "", filepath_or_name.lower())
    for f in os.listdir(MAPPINGS_DIR):
        if f.endswith(".json"):
            f_clean = re.sub(r"[^a-zA-Z0-9]", "", f[:-5].lower())
            if clean_search in f_clean or f_clean in clean_search:
                with open(os.path.join(MAPPINGS_DIR, f), "r", encoding="utf-8") as fp:
                    return MappingProfile(**json.load(fp))

    return None


def list_profiles() -> List[MappingProfile]:
    """Lists all available mapping profiles in the mappings directory."""
    _ensure_default_profiles()
    profiles = []
    for f in os.listdir(MAPPINGS_DIR):
        if f.endswith(".json"):
            try:
                p = load_profile(os.path.join(MAPPINGS_DIR, f))
                if p:
                    profiles.append(p)
            except Exception:
                pass
    return profiles


def _ensure_default_profiles():
    """Generates initial default profiles if they do not exist."""
    # 1. Generic FX Trade XML Profile
    generic_path = os.path.join(MAPPINGS_DIR, "generic_fx_trade.json")
    if not os.path.exists(generic_path):
        p1 = MappingProfile(
            name="Generic FX Trade XML",
            description="Mapping for standard FX trade XML structures with Trade, Parties, Details",
            mappings=[
                FieldMapping(target_path="asset_class", xpath="//Trade/AssetClass/text()", data_type="string", default_value="FX"),
                FieldMapping(target_path="trade_type", xpath="//Trade/TradeType/text()", data_type="string", default_value="FX Spot"),
                FieldMapping(target_path="reporting_party", xpath="//ReportingParty/Name/text() | //ReportingParty/PartyId/text()", data_type="string", default_value="BNPAFRPPAXXX"),
                FieldMapping(target_path="counterparty", xpath="//Counterparty/Name/text() | //Counterparty/PartyId/text()", data_type="string", default_value="DEUTDEDDXXXX"),
                FieldMapping(target_path="account_id", xpath="//ReportingParty/AccountId/text()", data_type="string", default_value="ACC-GLOBAL-7719-FX"),
                FieldMapping(target_path="direction", xpath="//Details/Direction/text()", data_type="string", default_value="BUY"),
                FieldMapping(target_path="base_currency", xpath="//Details/BaseCurrency/text()", data_type="string", default_value="EUR"),
                FieldMapping(target_path="quote_currency", xpath="//Details/QuoteCurrency/text()", data_type="string", default_value="USD"),
                FieldMapping(target_path="reporting_party_buys", xpath="//Details/BoughtCurrency/text()", data_type="string", default_value="EUR"),
                FieldMapping(target_path="reporting_party_sells", xpath="//Details/SoldCurrency/text()", data_type="string", default_value="USD"),
                FieldMapping(target_path="notional_amount", xpath="//Details/NotionalAmount/text()", data_type="float", default_value="18000000.00"),
                FieldMapping(target_path="counter_amount", xpath="//Details/CounterAmount/text()", data_type="float", default_value="19503000.00"),
                FieldMapping(target_path="trade_date", xpath="//Details/TradeDate/text()", data_type="date", default_value="2026-11-05"),
                FieldMapping(target_path="value_date", xpath="//Details/ValueDate/text()", data_type="date", default_value="2027-01-20"),
                FieldMapping(target_path="rate", xpath="//Details/ExchangeRate/text()", data_type="float", default_value="1.0835"),
            ]
        )
        save_profile(p1)

    # 2. FpML 5.x Confirmation Profile
    fpml_path = os.path.join(MAPPINGS_DIR, "fpml_5_x_fx_trade_confirmation.json")
    if not os.path.exists(fpml_path):
        p2 = MappingProfile(
            name="FpML 5.x FX Trade Confirmation",
            description="Industry standard FpML confirmation for FX Spot / Forward / NDF trades",
            mappings=[
                FieldMapping(target_path="asset_class", xpath="//assetClass/text()", data_type="string", default_value="FX"),
                FieldMapping(target_path="trade_type", xpath="//fxSingleLeg/productType/text()", data_type="string", default_value="FX Forward"),
                FieldMapping(target_path="reporting_party", xpath="//party[@id='party1']/partyName/text() | //party[@id='party1']/partyId/text()", data_type="string", default_value="BNP Paribas FX Desk"),
                FieldMapping(target_path="counterparty", xpath="//party[@id='party2']/partyName/text() | //party[@id='party2']/partyId/text()", data_type="string", default_value="Goldman Sachs International"),
                FieldMapping(target_path="account_id", xpath="//party[@id='party1']//accountId/text()", data_type="string", default_value="ACC-CORP-984210-FX"),
                FieldMapping(target_path="direction", xpath="//direction/text()", data_type="string", default_value="BUY", transform="upper"),
                FieldMapping(target_path="base_currency", xpath="//exchangedCurrency1/paymentAmount/currency/text()", data_type="string", default_value="EUR"),
                FieldMapping(target_path="quote_currency", xpath="//exchangedCurrency2/paymentAmount/currency/text()", data_type="string", default_value="USD"),
                FieldMapping(target_path="reporting_party_buys", xpath="//exchangedCurrency1/paymentAmount/currency/text()", data_type="string", default_value="EUR"),
                FieldMapping(target_path="reporting_party_sells", xpath="//exchangedCurrency2/paymentAmount/currency/text()", data_type="string", default_value="USD"),
                FieldMapping(target_path="notional_amount", xpath="//exchangedCurrency1/paymentAmount/amount/text()", data_type="float", default_value="25000000.00"),
                FieldMapping(target_path="counter_amount", xpath="//exchangedCurrency2/paymentAmount/amount/text()", data_type="float", default_value="27312500.00"),
                FieldMapping(target_path="trade_date", xpath="//tradeHeader/tradeDate/text()", data_type="date", default_value="2026-11-05"),
                FieldMapping(target_path="value_date", xpath="//fxSingleLeg/valueDate/text()", data_type="date", default_value="2027-02-15"),
                FieldMapping(target_path="rate", xpath="//exchangeRate/rate/text()", data_type="float", default_value="1.0925"),
            ]
        )
        save_profile(p2)

# Initialize defaults on import
_ensure_default_profiles()
