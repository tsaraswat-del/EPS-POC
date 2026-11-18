# EPS-POC: Multi-Format FX Trade Ingestion & XML XPath Mapping Engine

A financial trade confirmation ingestion engine that converts **SWIFT MT300**, **Unstructured Email/Text tickets**, and **XML Trade Confirmations (FpML 5.x / ISO 20022 / Generic XML)** into standardized perspective-aware JSON schemas. Includes an interactive **XML XPath to JSON Path Visual Mapper** UI.

---

## 🌟 Key Features

1. **Multi-Format Ingestion**:
   - **SWIFT MT300**: Extracts parties (`:82A:`, `:87A:`), accounts (`:77D:`), amounts and currencies (`:32B:`, `:33B:`), exchange rates (`:36:`), and dates (`:30T:`, `:30V:`).
   - **Conversational Emails / Text**: Parses natural language trade confirmations (`.eml`, `.msg`, `.txt`, `.pdf`, `.docx`).
   - **XML Documents**: Full support for XML trade confirmations including industry-standard **FpML 5.x** and **Generic XML Trades**.

2. **Visual XML XPath to JSON Path Mapping Studio**:
   - **Interactive Tree & XPath Explorer**: Discovers and displays all leaf nodes, attributes, and sample values in any XML document.
   - **Live XPath Query Tester**: Evaluate and test XPath expressions in real-time.
   - **Dynamic Field Mapping Table**: Map source XPath expressions to target JSON fields with type casting (`string`, `float`, `int`, `date`, `boolean`), default fallbacks, and transforms (`upper`, `lower`, `clean_amount`, `date_iso`).
   - **🔀 Multi-Field Conditional Logic Rules Builder**:
     - Define flexible business rules across two fields or literals:
       `IF condition1 [AND / OR condition2] THEN target = then_value ELSE target = else_value`
     - Example: If `trade_date == value_date` AND `asset_class == 'FX'`, set `trade_type` to `'FX Spot'`, otherwise `'FX Forward'`.
     - Supports field-to-field dynamic comparisons, field-to-literal comparisons, and raw XPath comparisons.
     - Supports full operator set (`==`, `!=`, `>`, `<`, `>=`, `<=`, `contains`, `not_contains`, `is_empty`, `is_not_empty`).
     - Real-time rule evaluation table showing individual operand resolutions, boolean outcomes, branch selection (`THEN` vs `ELSE`), and resolved target values.
   - **Auto-Inference**: Automatically analyzes XML document structures to generate accurate XPath mappings with a single click.
   - **Profile Management**: Save, load, export, and import mapping profiles (`.json`) including all field mappings and conditional logic rules. Pre-packaged with:
     - `Generic FX Trade XML`
     - `FpML 5.x FX Trade Confirmation`
   - **Live JSON Preview & Validation**: Test transformations instantly with schema validation against the standardized Pydantic `FXTradeConfirmation` model.

---

## 📁 Repository Structure

- `app.py`: CLI ingestion processor supporting XML, SWIFT, and emails.
- `ui.py`: Streamlit web interface with Document Ingestion and Visual XPath Mapping Studio tabs.
- `xml_mapping_engine.py`: Core XPath parsing, namespace normalization, mapping execution, and profile management engine.
- `mappings/`: Directory containing reusable JSON mapping profiles:
  - `generic_fx_trade_xml.json`
  - `fpml_5_x_fx_trade_confirmation.json`
- `sample_fx_trade.xml`: Sample Generic XML trade confirmation.
- `sample_fpml_fx_trade.xml`: Sample FpML 5.x trade confirmation.
- `Sample swift-MT300.txt`: Sample SWIFT MT300 message.
- `sample_trade.txt`: Sample natural language trade ticket.
- `unstructured_trade_confirm.eml`: Sample email trade confirmation.
- `test_pipeline.py`: Unit test suite covering all document formats and mapping engine workflows.

---

## 🚀 Getting Started

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Launch the Streamlit Web Application
```bash
streamlit run ui.py
```
Open `http://localhost:8501` in your browser.

- **Tab 1: 📄 Trade Ingestion & Conversion**: Upload or select SWIFT, Email, or XML documents to generate standardized JSON.
- **Tab 2: 🗺️ XML XPath to JSON Path Visual Mapper**: Configure, test, and save XPath-to-JSON mapping profiles.

### 3. Run via CLI (`app.py`)
```bash
# Ingest Generic XML Trade
python app.py sample_fx_trade.xml

# Ingest FpML 5.x XML Trade
python app.py sample_fpml_fx_trade.xml

# Ingest SWIFT MT300
python app.py "Sample swift-MT300.txt"

# Ingest Email
python app.py sample_trade.txt
```

### 4. Run Unit Tests
```bash
python -m unittest test_pipeline.py
```
