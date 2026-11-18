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

    def test_two_field_conditional_rule_spot_vs_forward(self):
        rule = xme.ConditionalRule(
            rule_id="test_spot_fwd",
            description="IF trade_date == value_date AND asset_class == 'FX' THEN 'FX Spot' ELSE 'FX Forward'",
            target_field="trade_type",
            condition1=xme.ConditionClause(
                field_or_xpath="trade_date",
                operator="==",
                value_type="field",
                value="value_date"
            ),
            logical_op="AND",
            condition2=xme.ConditionClause(
                field_or_xpath="asset_class",
                operator="==",
                value_type="literal",
                value="FX"
            ),
            then_value="FX Spot",
            else_value="FX Forward",
            data_type="string"
        )
        # Case 1: Same dates & asset FX -> FX Spot
        base_dict_1 = {"trade_date": "2026-11-05", "value_date": "2026-11-05", "asset_class": "FX"}
        res1 = xme.evaluate_conditional_rule(rule, base_dict_1)
        self.assertTrue(res1["combined_result"])
        self.assertEqual(res1["assigned_value"], "FX Spot")
        self.assertEqual(res1["branch_taken"], "THEN")

        # Case 2: Different dates & asset FX -> FX Forward
        base_dict_2 = {"trade_date": "2026-11-05", "value_date": "2027-01-20", "asset_class": "FX"}
        res2 = xme.evaluate_conditional_rule(rule, base_dict_2)
        self.assertFalse(res2["combined_result"])
        self.assertEqual(res2["assigned_value"], "FX Forward")
        self.assertEqual(res2["branch_taken"], "ELSE")

        # Case 3: Same dates but asset != FX -> FX Forward
        base_dict_3 = {"trade_date": "2026-11-05", "value_date": "2026-11-05", "asset_class": "EQUITY"}
        res3 = xme.evaluate_conditional_rule(rule, base_dict_3)
        self.assertFalse(res3["combined_result"])
        self.assertEqual(res3["assigned_value"], "FX Forward")
        self.assertEqual(res3["branch_taken"], "ELSE")

    def test_conditional_rule_numeric_and_logical_or(self):
        rule = xme.ConditionalRule(
            rule_id="test_tier_rule",
            description="IF notional_amount >= 10000000 OR counterparty contains 'DEUT' THEN 'PRIORITY' ELSE 'STANDARD'",
            target_field="tier",
            condition1=xme.ConditionClause(
                field_or_xpath="notional_amount",
                operator=">=",
                value_type="literal",
                value="10000000"
            ),
            logical_op="OR",
            condition2=xme.ConditionClause(
                field_or_xpath="counterparty",
                operator="contains",
                value_type="literal",
                value="DEUT"
            ),
            then_value="PRIORITY",
            else_value="STANDARD",
            data_type="string"
        )
        # Meets first condition
        res_1 = xme.evaluate_conditional_rule(rule, {"notional_amount": 15000000, "counterparty": "CITI"})
        self.assertTrue(res_1["combined_result"])
        self.assertEqual(res_1["assigned_value"], "PRIORITY")

        # Meets second condition
        res_2 = xme.evaluate_conditional_rule(rule, {"notional_amount": 500000, "counterparty": "DEUTDEDDXXXX"})
        self.assertTrue(res_2["combined_result"])
        self.assertEqual(res_2["assigned_value"], "PRIORITY")

        # Meets neither condition
        res_3 = xme.evaluate_conditional_rule(rule, {"notional_amount": 500000, "counterparty": "CITI"})
        self.assertFalse(res_3["combined_result"])
        self.assertEqual(res_3["assigned_value"], "STANDARD")

    def test_app_process_trade_document_all_formats(self):
        for path in ["sample_fx_trade.xml", "sample_fpml_fx_trade.xml", "Sample swift-MT300.txt", "sample_trade.txt"]:
            res = app.process_trade_document(path)
            data = json.loads(res)
            self.assertEqual(data["asset_class"], "FX")
            self.assertIn("rate", data)
            self.assertIn("notional_amount", data)

if __name__ == "__main__":
    unittest.main()
