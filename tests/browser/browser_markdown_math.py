"""Real bundled Markdown + KaTeX; fixture-only API/media, external requests blocked."""
import io
import unittest
import wave
from playwright.sync_api import expect
from tests.browser import browser_learning_feedback as feedback

ARTICLE = r'''## 线性代数：矩阵与方程组

1. **从方程到矩阵。** 线性方程组可以写成 \(Ax=b\)，其中 \(n\) 表示未知数的个数。[00:02]
2. **按列理解乘法。** 矩阵的每一列乘以对应的系数 $x_i$，再相加得到向量 $b$。[00:12]
3. **解的含义。** 寻找一组系数，使这些列向量的线性组合恰好等于目标向量。[01:00]

### 矩阵形式
\[
\begin{bmatrix}1 & 2 \\ 3 & 4\end{bmatrix}
\begin{bmatrix}x_1 \\ x_2\end{bmatrix}
=
\begin{bmatrix}b_1 \\ b_2\end{bmatrix}
\]

### 线性组合与求和
$$
A x = x_1 a_1 + x_2 a_2 = \sum_{i=1}^{n} x_i a_i
$$

- **分式：** \(x=\frac{-b\pm\sqrt{b^2-4ac}}{2a}\)。
- **上下标：** $a_{ij}$ 表示第 $i$ 行、第 $j$ 列的元素。

> 公式用于说明概念；时间戳仍可点击并跳回对应视频片段。
'''


class MarkdownMathBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.Fixture = feedback.fixture_type()
        cls.Fixture.setUpClass()
        audio = io.BytesIO()
        with wave.open(audio, 'wb') as clip:
            clip.setnchannels(1); clip.setsampwidth(2); clip.setframerate(8000)
            clip.writeframes(b'\0\0' * 8000 * 70)
        cls.Fixture.audio = audio.getvalue()

    @classmethod
    def tearDownClass(cls):
        cls.Fixture.tearDownClass()

    def setUp(self):
        self.f = self.Fixture('test_settings_cross_tab_updates')
        self.f.setUp(); self.page = self.f.page
        self.f.title = '线性代数 · 合成测试课程'
        media = self.f.media
        self.f.media = lambda: {**media(), 'duration': 70}
        self.record = {**self.f.records[0], 'template': 'summary', 'template_name': '内容摘要', 'text': ARTICLE}
        self.f.records = [self.record]
        self.progress = {'position': 0, 'duration': 70, 'revision': 0}
        def progress(route):
            if route.request.method == 'POST':
                body = route.request.post_data_json
                self.assertEqual(body['resource'], self.f.resource)
                self.progress = {**body, 'revision': body['expected_revision'] + 1}
            route.fulfill(json=self.progress)
        self.page.route('**/api/workspace/projects/*/study/progress', progress)
        # Scrub-bar thumbnails: answered locally so a hover never waits on ffmpeg.
        self.page.route('**/preview-frame*', lambda route: route.fulfill(status=200, content_type='image/svg+xml',
            body=b'<svg xmlns="http://www.w3.org/2000/svg" width="160" height="90"/>'))
        self.network = []
        self.page.on('response', lambda response: self.network.append((response.url, response.status)))

    def tearDown(self):
        try:
            self.assertFalse(any(method == 'POST' and (path.endswith(('/analyze-task', '/analysis-task', '/ask-task')) or path.startswith('/ai/'))
                                 for method, path, _ in self.f.requests), 'No generation or model-setting writes')
        finally:
            self.f.tearDown()

    def open(self, text=ARTICLE, learning=False):
        self.record['text'] = text
        self.f.parse()
        if learning:
            self.page.get_by_role('button', name='学习视图', exact=True).click()
        else:
            self.page.get_by_role('tab', name='AI 分析', exact=True).click()
        self.md = self.page.locator('.analysis-markdown')
        expect(self.md).to_be_visible()

    def test_all_four_delimiters_and_multiline_matrix(self):
        self.open(r'行内 \(Ax=b\)，美元式 $x^2$。' + '\n\n' + r'\[' + '\n' +
                  r'\begin{bmatrix}1&2\\3&4\end{bmatrix}' + '\n' + r'\]' + '\n\n' + r'$$\frac{a}{b}$$')
        expect(self.md.locator('.katex')).to_have_count(4)
        expect(self.md.locator('.katex-display')).to_have_count(2)
        expect(self.md.locator('math')).to_have_count(4)
        expect(self.md.locator('.mtable')).to_have_count(1)
        expect(self.md.locator('.md-math-fallback')).to_have_count(0)
        self.assertNotIn('\uE000', self.md.inner_text())

    def test_math_is_protected_from_markdown_and_time_recognition(self):
        self.open(r'''**粗体 \(x*y\)** 和 *斜体 $a_b$*。

1. 系数 \(x_1\) 与比例 \(12:34\)。[00:12]
2. 另一个条目。

| 含竖线的公式 | 说明 |
| --- | --- |
| $|x|$ | 绝对值 |
''')
        expect(self.md.locator('.katex')).to_have_count(5)
        expect(self.md.locator('strong .katex')).to_have_count(1)
        expect(self.md.locator('em .katex')).to_have_count(1)
        expect(self.md.locator('ol > li')).to_have_count(2)
        expect(self.md.locator('tbody td')).to_have_count(2)
        expect(self.md.locator('.md-time')).to_have_count(1)
        self.md.locator('.md-time').click()
        expect(self.page.get_by_role('heading', name='学习视图', exact=True)).to_be_visible()
        self.page.wait_for_function('document.querySelector("video").currentTime >= 12')
        self.page.locator('video').evaluate('(v) => v.pause()')
        self.assertLess(self.page.locator('video').evaluate('(v) => v.currentTime'), 13)

    def test_display_math_inside_lists_and_quotes(self):
        self.open(r'''### 特征值 \(\lambda\)

1. 矩阵：
   \[
   \begin{bmatrix}1&0\\0&1\end{bmatrix}
   \]
2. 第二项。

> \[
> x+y=z
> \]
''')
        expect(self.md.locator('h5 .katex')).to_have_count(1)
        expect(self.md.locator('ol > li')).to_have_count(2)
        expect(self.md.locator('li .katex-display')).to_have_count(1)
        expect(self.md.locator('blockquote .katex-display')).to_have_count(1)
        expect(self.md.locator('blockquote annotation')).to_have_text('x+y=z')
        expect(self.md.locator('.md-math-fallback')).to_have_count(0)

    def test_code_currency_and_links_stay_literal(self):
        self.open(r'''代码 `\(x\)` 和 `$y$`；价格 $20 和 $30；转义 \$5；正常 $z$。

```latex
\[x^2\]
$$x+y$$
```

[费用](https://example.invalid/$20) 与 [公式](https://example.invalid/$x$)。
''')
        expect(self.md.locator('.katex')).to_have_count(1)
        expect(self.md.locator('pre code')).to_have_text('\\[x^2\\]\n$$x+y$$')
        expect(self.md).to_contain_text('价格 $20 和 $30')
        expect(self.md.locator('a').last).to_have_attribute('href', 'https://example.invalid/$x$')
        self.assertNotIn('\uE000', self.md.inner_text())

    def test_invalid_unfinished_and_oversized_formulas_fall_back(self):
        source = r'损坏 \(\frac{1}{\)；超长 \(' + 'x' * 16385 + r'\)' + '\n\n' + r'未闭合 \[x+y'
        self.open(source)
        expect(self.md.locator('.md-math-fallback')).to_have_count(2)
        expect(self.md).to_contain_text(r'未闭合 \[x+y')
        self.assertEqual(self.record['text'], source)
        self.record['text'] = r'更新后 \[x+y\]'
        self.page.reload()
        expect(self.md.locator('.katex-display')).to_have_count(1)
        expect(self.md.locator('.md-math-fallback')).to_have_count(0)

    def test_untrusted_html_and_tex_cannot_create_active_content(self):
        self.open(r'''<img src=x onerror="window.__mathXss=1"><script>window.__mathXss=2</script>

\(\href{javascript:alert(1)}{x}\)

\(\htmlClass{evil}{x}\)

\(\includegraphics{https://evil.invalid/math.png}\)
''')
        expect(self.md.locator('img, script, a, .evil')).to_have_count(0)
        self.assertIsNone(self.page.evaluate('window.__mathXss'))
        self.assertFalse(any('evil.invalid' in url for url, _ in self.network))

    def test_macros_do_not_leak_and_expansion_is_bounded(self):
        self.open(r'\(\gdef\zz{7}\zz\) 与 \(\zz\)；递归 \(\def\aa{\aa}\aa\)')
        expect(self.md.locator('.katex')).to_have_count(1)
        expect(self.md.locator('.md-math-fallback')).to_have_count(2)

    def test_saved_learning_material_fonts_themes_and_screenshots(self):
        self.open(learning=True)
        expect(self.md.locator('.katex')).to_have_count(10)
        self.page.evaluate('document.fonts.ready')
        self.assertTrue(any('KaTeX_' in url and status == 200 for url, status in self.network))
        self.assertTrue(self.page.evaluate("Array.from(document.fonts).some(f => f.family === 'KaTeX_Main' && f.status === 'loaded')"))
        self.page.locator('.learning-materials').screenshot(path=str(self.f.output / 'math-light.png'), animations='disabled')
        self.page.get_by_role('button', name='切换到深色主题', exact=True).click()
        expect(self.page.locator('body')).to_have_css('background-color', 'rgb(10, 10, 10)')
        self.page.locator('.learning-materials').screenshot(path=str(self.f.output / 'math-dark.png'), animations='disabled')
        self.page.screenshot(path=str(self.f.output / 'math-workspace.png'), full_page=True, animations='disabled')
        self.page.set_viewport_size({'width': 390, 'height': 1000})
        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        self.page.locator('.learning-materials').screenshot(path=str(self.f.output / 'math-mobile.png'), animations='disabled')
        self.page.reload()
        expect(self.md.locator('.katex')).to_have_count(10)
        self.assertEqual(self.record['text'], ARTICLE)

    def test_long_display_and_inline_formulas_scroll_without_page_overflow(self):
        formula = '+'.join('x_{' + str(i) + '}' for i in range(80))
        self.open('\\[' + formula + '\\]\n\n行内 $' + formula + '$。', learning=True)
        self.page.set_viewport_size({'width': 390, 'height': 1000})
        expect(self.md.locator('.katex')).to_have_count(2)
        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        for item in self.md.locator('.md-math').all():
            self.assertTrue(item.evaluate('(e) => e.scrollWidth > e.clientWidth'))
            self.assertGreater(item.evaluate('(e) => {e.scrollLeft=50;return e.scrollLeft}'), 0)

    def test_saved_chat_question_and_answer_use_shared_renderer(self):
        self.f.sessions = [{'id': 'f' * 32, 'title': '公式讨论', 'video_resource': self.f.resource,
            'origin_record': None, 'agent': {'engine': 'api', 'model': '', 'effort': ''},
            'messages': [{'id': '1', 'question': r'如何理解 \(Ax=b\)？', 'answer': r'\[x=\frac{b}{a}\]',
                          'position': 0, 'created_at': ''}], 'created_at': '', 'updated_at': ''}]
        self.open(learning=True)
        self.page.get_by_role('tab', name='AI 对话', exact=True).click()
        expect(self.page.locator('.ai-chat-question .katex')).to_have_count(1)
        expect(self.page.locator('.ai-chat-answer .katex-display')).to_have_count(1)


if __name__ == '__main__':
    unittest.main()
