"""AI frame planning rejects unsafe output but must say what the model actually returned."""
import unittest

from video_parser.services import frame_planning


class ValidateTimesTests(unittest.TestCase):
    def test_plain_and_fenced_json_both_parse(self):
        self.assertEqual(frame_planning.validate_times('{"times": [30, 10, 20]}', 100), [10, 20, 30])
        self.assertEqual(frame_planning.validate_times('```json\n[5, 1]\n```', 100), [1, 5])

    def test_duplicates_collapse_and_near_end_stays_inside(self):
        self.assertEqual(frame_planning.validate_times('[10, 10.0, 99.9999]', 100), [10, 99.999])

    def test_empty_and_oversized_lists_are_rejected_with_the_model_text(self):
        with self.assertRaises(ValueError) as empty:
            frame_planning.validate_times('{"times": []}', 100)
        self.assertIn('模型返回', str(empty.exception))
        self.assertIn('{"times": []}', str(empty.exception))

        with self.assertRaises(ValueError):
            frame_planning.validate_times(str(list(range(17))), 100)

    def test_out_of_range_and_non_numeric_values_are_rejected(self):
        for bad in ('[100]', '[-1]', '["00:30"]', '[true]', '[NaN]'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                frame_planning.validate_times(bad, 100)

    def test_prose_around_json_is_rejected_and_quoted_back(self):
        prose = '抱歉，这段字幕太长，我建议你自己挑选时间点。'
        with self.assertRaises(ValueError) as error:
            frame_planning.validate_times(prose, 100)
        self.assertIn('抱歉，这段字幕太长', str(error.exception))

    def test_preview_is_truncated(self):
        long = '说' * 400
        with self.assertRaises(ValueError) as error:
            frame_planning.validate_times(long, 100)
        self.assertLessEqual(len(str(error.exception)), 260)

    def test_empty_reply_is_named_as_such(self):
        with self.assertRaises(ValueError) as error:
            frame_planning.validate_times('   ', 100)
        self.assertIn('模型没有返回内容', str(error.exception))


if __name__ == '__main__':
    unittest.main()
