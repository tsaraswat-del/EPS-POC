import os
import json
import unittest
import xml_mapping_engine as xme
import app

class TestTradeIngestionPipeline(unittest.TestCase):

    def test_swift_parsing(self):
        with open("Sample swift-MT300.txt", "r", encoding="utf-8") as f:
            raw = f.read()
        trade = app.parse_swift_mt300(raw)
        self.assertEqual(trade.asset_class, "FX")
        self.assertEqual(trade.reporting_party, "BNPAFRPPAXXX")
        self.assertEqual(trade.counterparty, "DEUTDEDDXXXX")
        self.assertEqual(trade.reporting_party_buys, "EUR")
        self.assertEqual(trade.reporting_party_sells, "USD")
        self.assertEqual(trade.notional_amount, 18000000.0)
        self.assertEqual(trade.rate, 1.0835)

    def test_email_parsing(self):
        with open("sample_trade.txt", "r", encoding="utf-8") as f:
            raw = f.read()
        trade = app.parse_unstructured_trade(raw)
        self.assertEqual(trade.asset_class, "FX")
        self.assertIn("JP Morgan", trade.counterparty)
        self.assertEqual(trade.reporting_party_buys, "EUR")
        self.assertEqual(trade.reporting_party_sells, "USD")
        self.assertEqual(trade.notional_amount, 10000000.0)
        self.assertEqual(trade.rate, 1.0850)

    def test_eml_file_parsing(self):
        with open("unstructured_trade_confirm.eml", "r", encoding="utf-8") as f:
            raw = f.read()
        trade = app.parse_unstructured_trade(raw)
        self.assertEqual(trade.asset_class, "FX")
        self.assertEqual(trade.trade_type, "FX Forward")
        self.assertIn("Goldman Sachs", trade.counterparty)
        self.assertEqual(trade.reporting_party_buys, "EUR")
        self.assertEqual(trade.reporting_party_sells, "USD")
        self.assertEqual(trade.notional_amount, 25000000.0)
        self.assertEqual(trade.rate, 1.0925)

    def test_generic_xml_parsing(self):
        with open("sample_fx_trade.xml", "r", encoding="utf-8") as f:
            xml = f.read()
        profile = xme.load_profile("generic")
        self.assertIsNotNone(profile)
        mapped_dict, trace = xme.apply_mapping_profile(xml, profile)
        trade = app.FXTradeConfirmation(**mapped_dict)
        self.assertEqual(trade.asset_class, "FX")
        self.assertEqual(trade.trade_type, "FX Forward")
        self.assertIn("BNP", trade.reporting_party)
        self.assertIn("DEUT", trade.counterparty)
        self.assertEqual(trade.account_id, "ACC-GLOBAL-7719-FX")
        self.assertEqual(trade.notional_amount, 18000000.0)
        self.assertEqual(trade.rate, 1.0835)
        self.assertEqual(trade.trade_date, "2026-11-05")
        self.assertEqual(trade.value_date, "2027-01-20")

    def test_fpml_xml_parsing(self):
        with open("sample_fpml_fx_trade.xml", "r", encoding="utf-8") as f:
            xml = f.read()
        profile = xme.load_profile("fpml")
        self.assertIsNotNone(profile)
        mapped_dict, trace = xme.apply_mapping_profile(xml, profile)
        trade = app.FXTradeConfirmation(**mapped_dict)
        self.assertEqual(trade.asset_class, "FX")
        self.assertEqual(trade.trade_type, "FX Forward")
        self.assertIn("BNP", trade.reporting_party)
        self.assertIn("GOLDMAN", trade.counterparty)
        self.assertEqual(trade.notional_amount, 25000000.0)
        self.assertEqual(trade.rate, 1.0925)

    def test_xml_auto_inference(self):
        with open("sample_fx_trade.xml", "r", encoding="utf-8") as f:
            xml = f.read()
        inferred = xme.infer_mappings_from_xml(xml)
        self.assertGreater(len(inferred), 10)
        temp_prof = xme.MappingProfile(name="Inferred", mappings=inferred)
        mapped_dict, _ = xme.apply_mapping_profile(xml, temp_prof)
        self.assertEqual(mapped_dict["reporting_party_buys"], "EUR")
        self.assertEqual(mapped_dict["notional_amount"], 18000000.0)

    def test_app_process_trade_document_all_formats(self):
        for path in ["sample_fx_trade.xml", "sample_fpml_fx_trade.xml", "Sample swift-MT300.txt", "sample_trade.txt"]:
            res = app.process_trade_document(path)
            data = json.loads(res)
            self.assertEqual(data["asset_class"], "FX")
            self.assertIn("rate", data)
            self.assertIn("notional_amount", data)

if __name__ == "__main__":
    unittest.main()
