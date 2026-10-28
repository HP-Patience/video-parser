"""Structured quizzes and note/question-bank exports (Markdown, CSV, Anki)."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from video_parser.services import ai_analysis, anki_export, library, quiz, study
from video_parser.web import study_routes

QUIZ_TEXT = '''```json
{"title": "线性代数自测", "questions": [
  {"question": "矩阵 A 的秩是什么？", "options": ["A. 行数", "B. 线性无关行向量的最大个数", "C. 元素个数"],
   "answer": "B", "explanation": "秩等于极大线性无关组的向量个数。", "time": 65},
  {"question": "单位矩阵的行列式为 1。", "options": ["正确", "错误"], "answer": 0, "explanation": "", "time": null}
]}
```'''


class QuizParsingTests(unittest.TestCase):
    def test_fenced_json_is_parsed_and_option_letters_are_stripped(self):
        parsed = quiz.parse(QUIZ_TEXT)
        self.assertEqual(parsed['title'], '线性代数自测')
        self.assertEqual(len(parsed['questions']), 2)
        first = parsed['questions'][0]
        self.assertEqual(first['options'], ['行数', '线性无关行向量的最大个数', '元素个数'])
        self.assertEqual((first['answer'], first['answer_text']), (1, '线性无关行向量的最大个数'))
        self.assertEqual(first['time'], 65.0)
        self.assertIsNone(parsed['questions'][1]['time'])
        self.assertEqual(parsed['questions'][1]['answer'], 0)

    def test_invalid_questions_are_dropped_instead_of_shown(self):
        self.assertIsNone(quiz.parse('普通 Markdown，没有题目'))
        self.assertIsNone(quiz.parse('```json\n{"questions": []}\n```'))
        broken = '{"questions":[{"question":"","options":["A","B"],"answer":"A"},' \
                 '{"question":"只有一个选项","options":["A"],"answer":"A"},' \
                 '{"question":"答案不存在","options":["A","B"],"answer":"D"}]}'
        self.assertIsNone(quiz.parse(broken))
        partial = '{"questions":[{"question":"有效题","options":["A","B"],"answer":"B"},' \
                  '{"question":"答案不存在","options":["A","B"],"answer":"D"}]}'
        self.assertEqual(len(quiz.parse(partial)['questions']), 1)

    def test_answer_accepts_letter_text_or_index(self):
        base = '{"questions":[{"question":"q","options":["甲","乙"],"answer":%s}]}'
        for value in ('"B"', '"乙"', '"B. 乙"', '1'):
            self.assertEqual(quiz.parse(base % value)['questions'][0]['answer'], 1, value)
        for value in ('true', '5', '"C"', 'null'):
            self.assertIsNone(quiz.parse(base % value), value)

    def test_markdown_and_cards_are_human_readable(self):
        parsed = quiz.parse(QUIZ_TEXT)
        markdown = quiz.markdown(parsed)
        self.assertIn('### 1. 矩阵 A 的秩是什么？ [01:05]', markdown)
        self.assertIn('**答案：B. 线性无关行向量的最大个数**', markdown)
        cards = quiz.cards(parsed, '线性代数', 'https://www.bilibili.com/video/BV1TaqYBcEJc')
        self.assertEqual(len(cards), 2)
        self.assertIn('B. 线性无关行向量的最大个数', cards[0]['front'])
        self.assertIn('答案：B.', cards[0]['back'])
        self.assertEqual(cards[0]['tags'], 'video-parser::quiz')
        data = anki_export.cards_csv(cards).decode('utf-8')
        self.assertTrue(data.startswith('#separator:Comma\n#html:true\n#tags column:3\n'))
        self.assertIn('video-parser::quiz', data)


class ExportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        mock = patch.object(library, 'LIBRARY_ROOT', self.root / 'library')
        mock.start()
        self.addCleanup(mock.stop)
        self.project = library.ensure({'platform': 'bilibili', 'video_id': 'BV1TaqYBcEJc', 'title': '线性代数',
                                       'source_url': 'https://www.bilibili.com/video/BV1TaqYBcEJc?p=1'})
        self.pid = self.project['id']
        source = self.root / 'clip'
        source.write_bytes(b'isolated media fixture')
        self.video = 'video/test.mp4'
        library.import_file(self.pid, source, self.video, 'video', {'media': {'format': {'duration': '120'}}})
        self.note = 'a' * 32

    def test_quiz_record_exposes_structure_and_exports_readable_markdown(self):
        name = library.save_text(self.pid, 'analysis', QUIZ_TEXT, {'template': 'quiz', 'source': 'transcript',
                                                                  'video_resource': self.video, 'model': 'fixture'})
        record = ai_analysis.record(self.pid, Path(name).stem)
        self.assertEqual(record['template_name'], '复习自测题')
        self.assertEqual(len(record['quiz']['questions']), 2)
        markdown, filename = ai_analysis.export_markdown(self.pid, Path(name).stem)
        self.assertIn('线性代数自测', markdown)
        self.assertNotIn('```json', markdown)
        self.assertIn('线性代数', filename)
        content, _ = ai_analysis.export_anki(self.pid, Path(name).stem)
        self.assertIn('#separator:Comma'.encode(), content)
        self.assertIn('线性无关行向量'.encode(), content)
        csv_content, _ = ai_analysis.export_quiz_csv(self.pid, Path(name).stem)
        self.assertTrue(csv_content.startswith(b'\xef\xbb\xbf'))
        self.assertIn('题号,题干,选项,答案,解析,时间点'.encode(), csv_content)

    def test_anki_export_refuses_records_without_a_quiz(self):
        name = library.save_text(self.pid, 'analysis', '## 摘要\n- 内容', {'template': 'summary', 'video_resource': self.video})
        with self.assertRaises(ValueError):
            ai_analysis.export_anki(self.pid, Path(name).stem)
        with self.assertRaises(ValueError):
            ai_analysis.export_quiz_csv(self.pid, Path(name).stem)

    def test_note_exports_and_http_routes(self):
        study.save_note(self.pid, self.video, self.note, 5, '自己的理解', 0)
        markdown, filename = study.export_markdown(self.pid, self.video)
        self.assertIn('## [00:05]', markdown)
        self.assertIn('自己的理解', markdown)
        self.assertIn('笔记', filename)
        cards = study.note_cards(self.pid, self.video)
        self.assertEqual(cards[0]['tags'], 'video-parser::notes')
        self.assertIn('自己的理解', cards[0]['front'])
        self.assertIn('00:05', cards[0]['back'])
        self.assertIn('时间点,笔记,创建时间'.encode(), study.notes_csv(self.pid, self.video))

        app = FastAPI()
        app.include_router(study_routes.router)
        client = TestClient(app, base_url='http://127.0.0.1:15001', client=('127.0.0.1', 12345))
        base = f'/api/workspace/projects/{self.pid}/study/notes/export'
        for fmt, marker in (('md', b'## [00:05]'), ('csv', b'\xef\xbb\xbf'), ('anki', b'#separator:Comma')):
            response = client.get(base, params={'resource': self.video, 'format': fmt})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn(marker, response.content)
            self.assertIn("filename*=UTF-8''", response.headers['content-disposition'])
            self.assertIn('no-store', response.headers['cache-control'])
        self.assertEqual(client.get(base, params={'resource': self.video, 'format': 'pdf'}).status_code, 422)
        self.assertEqual(client.get(base, params={'resource': 'video/unknown.mp4'}).status_code, 400)


if __name__ == '__main__':
    unittest.main()
