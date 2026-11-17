"""Provider-aware usage pricing without network access."""
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from video_parser.services import usage_report


class UsageProviderPricingTests(unittest.TestCase):
    def setUp(self):
        self.events = [
            {'at': datetime.now(timezone.utc), 'kind': 'analysis', 'model': 'same-model',
             'provider': '供应商甲', 'engine': 'api', 'prompt_tokens': 100,
             'completion_tokens': 50, 'project_id': 'one', 'project_title': '甲'},
            {'at': datetime.now(timezone.utc), 'kind': 'analysis', 'model': 'same-model',
             'provider': '供应商乙', 'engine': 'api', 'prompt_tokens': 100,
             'completion_tokens': 50, 'project_id': 'two', 'project_title': '乙'},
        ]
        self.current = {'price_input': 1.0, 'price_output': 2.0}

    def collect(self, providers):
        with patch.object(usage_report, '_events', return_value=iter(self.events)), \
             patch.object(usage_report.ai_settings, 'current', return_value=self.current), \
             patch.object(usage_report.ai_settings, 'public', return_value={'providers': providers}):
            return usage_report.collect(0)

    def test_same_model_uses_each_supplier_price(self):
        report = self.collect([
            {'name': '供应商甲', 'models': ['same-model'], 'price_input': 1.0, 'price_output': 2.0},
            {'name': '供应商乙', 'models': ['same-model'], 'price_input': 3.0, 'price_output': 4.0},
        ])
        self.assertAlmostEqual(report['totals']['cost'], 0.0007, places=8)
        self.assertEqual(report['totals']['unpriced_calls'], 0)

    def test_ambiguous_supplier_name_is_unknown(self):
        report = self.collect([
            {'name': '供应商甲', 'models': ['same-model'], 'price_input': 1.0, 'price_output': 2.0},
            {'name': '供应商甲', 'models': ['same-model'], 'price_input': 3.0, 'price_output': 4.0},
            {'name': '供应商乙', 'models': ['same-model'], 'price_input': 3.0, 'price_output': 4.0},
        ])
        self.assertIsNone(report['totals']['cost'])
        self.assertEqual(report['totals']['unpriced_calls'], 1)

    def test_no_provider_catalog_keeps_legacy_current_prices(self):
        report = self.collect([])
        self.assertAlmostEqual(report['totals']['cost'], 0.0004, places=8)
        self.assertEqual(report['totals']['unpriced_calls'], 0)
        self.assertTrue(report['prices']['configured'])


if __name__ == '__main__':
    unittest.main()
