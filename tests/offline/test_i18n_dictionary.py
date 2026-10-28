"""English interface dictionary: well-formed entries, coverage of frontend text, parity with translator.ts."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'frontend' / 'src'
DICTIONARY = SOURCE / 'lib' / 'i18n-en.json'
TRANSLATOR = SOURCE / 'lib' / 'translator.ts'

# Template literals that are not interface copy, or whose pieces are joined before display.
NOT_UI_TEMPLATES = {
    ('features/analysis/AnalysisPanel.tsx', '`## 追问：${…}\\n\\n${…}`'),  # Markdown copied to the clipboard.
    ('features/analysis/AnalysisPanel.tsx', '`预计发送约 ${…} tokens`'),  # Joined with the next template; the whole
    ('features/analysis/AnalysisPanel.tsx', '`（${…}），输出约 ${…}`'),  # estimate is matched by one pattern.
    ('components/markdownTimeRange.ts', r'`([（(]?)\s*(\[?${…}\]?)\s*[-–—~～至]\s*(\[?${…}\]?)\s*([）)]?)`'),  # Time-range parser regex.
}
COMPOSED = [
    '预计发送约 1.2 万 tokens（字幕约 3,000 · 画面 8 张），输出约 1,500', '模型分析 · ', '画面 00:05（已选）',
    '只用字幕（平台字幕） · 8 张画面 · qwen3-vl · 用量 1.2 万 + 1,500 tokens', '平台字幕 · 12 条 · 到 00:20', '自动（1 张）',
    '视频已校验归档。 临时副本清理失败，可在缓存管理中查看。', '跳到 00:05：核心概念', '运行任务 1 项，打开任务中心',
    'library、models、Cookies、原始音视频、outputs/transcriptions、可复用字幕缓存、任务记录',
    '本次下载 3 集，已有可用 2 集，未完成 1 集。 CLI 异常退出，请核对未完成分集。', 'Video Parser · 视频工作台',
]
USER_CONTENT = ['我的进度', '线性代数（第一讲）', '画面 很美', '第一讲 · 导论', '这是用户自己写的笔记内容', '期末复习结果']

# ---- Python mirror of frontend/src/lib/translator.ts (kept in sync by test_python_mirror_matches_translator_ts)
CHINESE = re.compile('[\u3000-\u303f\u3400-\u9fff\uf900-\ufaff\uff00-\uffef]')
HAN = re.compile('[\u3400-\u9fff\uf900-\ufaff]')
PLACEHOLDER = re.compile(r'\{(\d)\|(raw|wan)\}|\{(\d):([^|{}]*)\|([^{}]*)\}|\$(\d)')
SENTENCES = re.compile('[^。！？…]+[。！？…]+|[^。！？…]+$')
TAIL = re.compile('^(.+?)([：:…。])$')
MAX_DEPTH = 6


ESCAPE = re.compile(r'\\(u\{[0-9a-fA-F]+\}|u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|[\s\S])')
SIMPLE_ESCAPES = {'n': '\n', 't': '\t', 'r': '\r', 'b': '\b', 'f': '\f', 'v': '\v', '0': '\0'}


def normalize(text):
    return re.sub(r'\s+', ' ', text).strip()


def cook(text):
    """Decode JS string escapes (\\n, \\u00a0, \\u{...}, \\x41, \\', ...)."""
    def replace(match):
        body = match.group(1)
        if body[0] in 'ux' and len(body) > 1:
            return chr(int(body.strip('u{}x'), 16))
        return SIMPLE_ESCAPES.get(body, body)
    return ESCAPE.sub(replace, text)


class Translator:
    """Same lookup order as translator.ts: exact, anchored patterns (strict unless 'lenient'), then safe splits."""

    def __init__(self, dictionary):
        self.exact = {normalize(key): value for key, value in dictionary['exact'].items()}
        self.patterns = [(re.compile(f'^(?:{entry[0]})$'), entry[1], len(entry) > 2 and entry[2] == 'lenient')
                         for entry in dictionary['patterns']]

    def lookup(self, core, depth=0):
        if core in self.exact:
            return self.exact[core]
        if depth > MAX_DEPTH:
            return None
        for pattern, target, lenient in self.patterns:
            match = pattern.match(core)
            result = self.fill(target, match, depth, lenient) if match else None
            if result is not None:
                return result
        return self.split(core, depth)

    def fill(self, target, match, depth, lenient):
        complete = [True]

        def replace(placeholder):
            raw_index, kind, plural_index, one, many, index = placeholder.groups()
            if raw_index:
                value = match.group(int(raw_index)) or ''
                return value if kind == 'raw' else f'{round(float(value) * 100) / 10:g}k'
            if plural_index:
                try:
                    return one if float(match.group(int(plural_index)) or 'nan') == 1 else many
                except ValueError:
                    return many
            value = self.part(match.group(int(index)) or '', depth + 1)
            if HAN.search(value):
                complete[0] = False
            return value
        result = PLACEHOLDER.sub(replace, target)
        return result if complete[0] or lenient else None

    def part(self, source, depth):
        if not CHINESE.search(source):
            return source
        found = self.lookup(normalize(source), depth)
        return source if found is None else found

    def translate_all(self, pieces, depth):
        out = []
        for piece in pieces:
            core = normalize(piece)
            if not CHINESE.search(core):
                out.append(core)
                continue
            found = self.lookup(core, depth + 1)
            if found is None:
                return None
            out.append(found.strip())
        return out

    def split(self, core, depth):
        if ' · ' in core:
            parts = self.translate_all(core.split(' · '), depth)
            if parts is not None:
                return ' · '.join(parts)
        if '、' in core:
            parts = self.translate_all(core.split('、'), depth)
            if parts is not None:
                return ', '.join(parts)
        edge = re.match(r'^(.+) ·$', core)
        if edge:
            parts = self.translate_all([edge.group(1)], depth)
            if parts is not None:
                return parts[0] + ' · '
        edge = re.match(r'^· (.+)$', core)
        if edge:
            parts = self.translate_all([edge.group(1)], depth)
            if parts is not None:
                return ' · ' + parts[0]
        bracket = re.match('^([^（）]*)（([^（）]+)）$', core)
        if bracket:
            parts = self.translate_all([bracket.group(1), bracket.group(2)], depth)
            if parts is not None:
                return f'{parts[0]} ({parts[1]})' if parts[0] else f' ({parts[1]})'
        sentences = SENTENCES.findall(core)
        if len(sentences) > 1:
            parts = self.translate_all(sentences, depth)
            if parts is not None:
                return ' '.join(parts)
        tail = TAIL.match(core)
        if tail:
            parts = self.translate_all([tail.group(1)], depth)
            if parts is not None:
                return parts[0] + {'：': ': ', ':': ': ', '…': '…', '。': '.'}[tail.group(2)]
        return None

    def translate(self, text):
        return self.part(text, 0) if CHINESE.search(text) else text

    def covers(self, text):
        return not HAN.search(self.translate(text))

    @staticmethod
    def sample(template):
        """Template literal source -> representative text (interpolations become 3)."""
        body = template[1:-1] if template.startswith('`') else template
        return cook(re.sub(r'\$\{…\}', '3', body))

    def covers_template(self, template):
        return self.covers(self.sample(template))


REGEX_AFTER = set('(,=:[!&|?{;+-*%~^')
REGEX_KEYWORDS = ('return', 'typeof', 'case', 'in', 'of', 'delete', 'void', 'throw', 'new', 'else', 'do')


def scan_source(source):
    """Yield ('string' | 'template' | 'jsx', text) for literals holding Chinese text.

    Strings, template literals, comments and regex literals are consumed first; what remains with
    Chinese text can only be JSX text, because identifiers are ASCII. Template interpolations become
    '${…}', and literals nested inside them are not reported (they are part of the template)."""
    found = []
    residue = []
    i, n = 0, len(source)

    def previous_token():
        text = ''.join(residue[-12:]).rstrip()
        return text

    def read_string(start):
        quote, j = source[start], start + 1
        while j < n and source[j] != quote and source[j] != '\n':
            j += 2 if source[j] == '\\' else 1
        return j + 1, source[start + 1:j]

    def skip_expression(start):
        depth, j = 0, start
        while j < n:
            c = source[j]
            if c in '\'"':
                j, text = read_string(j)
                if CHINESE.search(text):
                    found.append(('string', cook(text)))
                continue
            if c == '`':
                j, text = read_template(j)
                if CHINESE.search(text):
                    found.append(('template', text) if '${…}' in text else ('string', cook(text[1:-1])))
                continue
            if source.startswith('//', j):
                end = source.find('\n', j); j = n if end < 0 else end; continue
            if source.startswith('/*', j):
                end = source.find('*/', j + 2); j = n if end < 0 else end + 2; continue
            if c == '{':
                depth += 1
            elif c == '}':
                if depth == 0:
                    return j + 1
                depth -= 1
            j += 1
        return n

    def read_template(start):
        j, parts = start + 1, ['`']
        while j < n:
            c = source[j]
            if c == '\\':
                parts.append(source[j:j + 2]); j += 2; continue
            if c == '`':
                parts.append('`')
                return j + 1, ''.join(parts)
            if source.startswith('${', j):
                parts.append('${…}')
                j = skip_expression(j + 2); continue
            parts.append(c); j += 1
        return n, ''.join(parts)

    def regex_allowed():
        text = previous_token()
        if not text:
            return True
        last = text[-1]
        if last in REGEX_AFTER:
            return True
        if last == '>' and text.endswith('=>'):
            return True
        word = re.search(r'([A-Za-z_$][\w$]*)$', text)
        return bool(word and word.group(1) in REGEX_KEYWORDS)

    def read_regex(start):
        j, in_class = start + 1, False
        while j < n and source[j] != '\n':
            c = source[j]
            if c == '\\':
                j += 2; continue
            if c == '[':
                in_class = True
            elif c == ']':
                in_class = False
            elif c == '/' and not in_class:
                j += 1
                while j < n and source[j].isalpha():
                    j += 1
                return j
            j += 1
        return None

    while i < n:
        c = source[i]
        if source.startswith('//', i):
            end = source.find('\n', i); i = n if end < 0 else end; continue
        if source.startswith('/*', i):
            end = source.find('*/', i + 2); i = n if end < 0 else end + 2; continue
        if c in '\'"':
            i, text = read_string(i)
            if CHINESE.search(text):
                found.append(('string', cook(text)))
            residue.append('\x00'); continue
        if c == '`':
            i, text = read_template(i)
            if CHINESE.search(text):
                found.append(('template', text) if '${…}' in text else ('string', cook(text[1:-1])))
            residue.append('\x00'); continue
        if c == '/' and regex_allowed():
            end = read_regex(i)
            if end is not None:
                i = end; residue.append('\x00'); continue
        residue.append(c); i += 1
    for match in re.finditer('[^<>{}\x00]+', ''.join(residue)):
        text = match.group(0)
        if CHINESE.search(text):
            found.append(('jsx', text.strip()))
    return found


NODE_SCRIPT = """import { readFileSync } from 'node:fs';
const [translator, dictionary, inputs] = process.argv.slice(1);
const { createTranslator } = await import(translator);
const translate = createTranslator(JSON.parse(readFileSync(dictionary, 'utf8')));
process.stdout.write(JSON.stringify(JSON.parse(readFileSync(inputs, 'utf8')).map(text => translate(text))));"""


def frontend_literals():
    for path in sorted(SOURCE.rglob('*')):
        if path.suffix not in ('.ts', '.tsx') or path.name.startswith(('i18n', 'translator')):
            continue
        rel = path.relative_to(SOURCE).as_posix()
        for kind, text in scan_source(path.read_text(encoding='utf-8')):
            yield rel, kind, text


class InterfaceDictionaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dictionary = json.loads(DICTIONARY.read_text(encoding='utf-8'))
        cls.translator = Translator(cls.dictionary)
        cls.literals = sorted(set(frontend_literals()))

    def test_entries_are_normalized_and_free_of_chinese(self):
        for key, value in self.dictionary['exact'].items():
            with self.subTest(key=key):
                self.assertEqual(key, normalize(key))
                self.assertTrue(value.strip())
                self.assertIsNone(CHINESE.search(value), value)
        for entry in self.dictionary['patterns']:
            with self.subTest(pattern=entry[0]):
                self.assertIn(len(entry), (2, 3))
                self.assertEqual(entry[2:], ['lenient'] if len(entry) == 3 else [])
                self.assertIsNone(CHINESE.search(entry[1]), entry[1])
                groups = re.compile(entry[0]).groups
                for match in PLACEHOLDER.finditer(entry[1]):
                    self.assertLessEqual(int(match.group(1) or match.group(3) or match.group(6)), groups)

    def test_static_frontend_text_is_translated(self):
        self.assertGreater(len(self.literals), 500)
        missing = sorted({(rel, text) for rel, kind, text in self.literals
                          if kind != 'template' and HAN.search(text) and not self.translator.covers(text)})
        self.assertEqual(missing, [], 'add English for these strings to frontend/src/lib/i18n-en.json')

    def test_template_literals_have_patterns(self):
        missing = sorted({(rel, text) for rel, kind, text in self.literals if kind == 'template' and HAN.search(text)
                          and (rel, text) not in NOT_UI_TEMPLATES and not self.translator.covers_template(text)})
        self.assertEqual(missing, [], 'add a pattern for these template literals')

    def test_composed_messages_translate_and_user_content_is_kept(self):
        translate = self.translator.translate
        self.assertEqual(translate('笔记 & AI 对话'), 'Notes & AI chat')
        self.assertEqual(translate('运行任务 3 项，打开任务中心'), '3 tasks running, open Task center')
        self.assertEqual(translate(COMPOSED[0]), 'About 12k tokens to send (subtitles ~3,000 · 8 frames), about 1,500 output')
        self.assertEqual(translate('跳到 00:05：核心概念'), 'Jump to 00:05: 核心概念')  # The AI chapter title stays as written.
        for text in COMPOSED:
            with self.subTest(text=text):
                self.assertIsNone(HAN.search(translate(text).replace('核心概念', '')), translate(text))
        for text in USER_CONTENT:
            with self.subTest(text=text):
                self.assertEqual(translate(text), text)

    def test_python_mirror_matches_translator_ts(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('node is not installed')
        inputs = sorted({text for _, kind, text in self.literals if kind != 'template'}) + COMPOSED + USER_CONTENT
        with tempfile.TemporaryDirectory(prefix='i18n-parity-') as directory:
            data = Path(directory) / 'inputs.json'
            data.write_text(json.dumps(inputs, ensure_ascii=False), encoding='utf-8')
            result = subprocess.run([node, '--input-type=module', '-e', NODE_SCRIPT, TRANSLATOR.as_uri(), str(DICTIONARY), str(data)],
                                    capture_output=True, text=True, encoding='utf-8', timeout=60)
        if result.returncode and 'ERR_UNKNOWN_FILE_EXTENSION' in result.stderr:
            self.skipTest('this node cannot strip TypeScript types (needs Node 22.18+)')
        self.assertEqual(result.returncode, 0, result.stderr)
        outputs = json.loads(result.stdout)
        drift = [(text, ts, self.translator.translate(text)) for text, ts in zip(inputs, outputs)
                 if normalize(ts) != normalize(self.translator.translate(text))]
        self.assertEqual(drift, [])


if __name__ == '__main__':
    unittest.main()
