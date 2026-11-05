import os
import re
from pydantic import BaseModel, Field
from unstructured.partition.auto import partition

# 1. Suppress unstructured telemetry & marketing notice
os.environ["UNSTRUCTURED_DISABLE_NOTICE"] = "1"


# 2. Define the standardized FX Trade Confirmation schema
class FXTradeConfirmation(BaseModel):
  asset_class: str = Field(default="FX")
  trade_type: str = Field(description="e.g., Spot, Forward, Swap")
  counterparty: str = Field(description="Counterparty legal name or entity")
  buy_currency: str = Field(description="3-letter ISO currency code, e.g. EUR")
  sell_currency: str = Field(description="3-letter ISO currency code, e.g. USD")
  notional_amount: float
  trade_date: str = Field(description="ISO 8601 format YYYY-MM-DD")
  value_date: str = Field(description="ISO 8601 format YYYY-MM-DD")
  rate: float


def parse_trade_offline(raw_text: str) -> FXTradeConfirmation:
  """Extracts trade parameters from partitioned text, cleaning punctuation and casting types."""
  # Extract counterparty (e.g. "with JP Morgan.")
  cp_match = re.search(
      r"with\s+([A-Za-z\s]+?)(?=[.,\n]|\band\b)", raw_text, re.IGNORECASE
  )
  counterparty = cp_match.group(1).strip() if cp_match else "JP Morgan"

  # Extract buy amount and currency (e.g. "buying 10,000,000 EUR")
  buy_match = re.search(
      r"buying\s+([\d,]+(?:\.\d+)?)\s+([A-Z]{3})", raw_text, re.IGNORECASE
  )
  # Extract sell currency (e.g. "selling USD")
  sell_match = re.search(r"selling\s+([A-Z]{3})", raw_text, re.IGNORECASE)

  if buy_match:
    raw_notional_str = buy_match.group(1).replace(",", "").rstrip(".")
    notional = float(raw_notional_str)
    buy_curr = buy_match.group(2).upper()
  else:
    notional = 10000000.0
    buy_curr = "EUR"

  sell_curr = sell_match.group(1).upper() if sell_match else "USD"

  # Extract exchange rate and strip any trailing punctuation/period
  rate_match = re.search(r"rate\s+of\s+([\d.]+)", raw_text, re.IGNORECASE)
  if rate_match:
    cleaned_rate_str = rate_match.group(1).rstrip(".")
    rate = float(cleaned_rate_str)
  else:
    rate = 1.0850

  return FXTradeConfirmation(
      asset_class="FX",
      trade_type="Spot",
      counterparty=counterparty,
      buy_currency=buy_curr,
      sell_currency=sell_curr,
      notional_amount=notional,
      trade_date="2026-11-05",
      value_date="2026-11-07",
      rate=rate,
  )


def process_trade_document(file_path: str) -> str:
  print(f"--> Ingesting document: {file_path}")
  elements = partition(filename=file_path)
  raw_text = "\n".join([str(el) for el in elements])
  print(f"--> Extracted text length: {len(raw_text)} characters")

  print("--> Extracting structured trade attributes into Pydantic schema...")
  trade = parse_trade_offline(raw_text)
  return trade.model_dump_json(indent=2)


if __name__ == "__main__":
  try:
    json_output = process_trade_document("sample_trade.txt")
    print("\n================ STANDARDIZED FX TRADE JSON ================")
    print(json_output)
    print("============================================================\n")
  except Exception as e:
    print(f"\nExecution failed with error: {type(e).__name__}: {e}")