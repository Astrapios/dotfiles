"""Tests for Markdown→Telegram-HTML rendering used by focus messages."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from astra import content, telegram


class TestMdToHtml:
    def test_bold_italic_code_strike(self):
        out = content.md_to_telegram_html(
            "a **b** c *i* d `k` e ~~s~~")
        assert out == "a <b>b</b> c <i>i</i> d <code>k</code> e <s>s</s>"

    def test_heading_to_bold(self):
        assert content.md_to_telegram_html("### Title here") == "<b>Title here</b>"

    def test_list_bullets(self):
        assert content.md_to_telegram_html("- one\n* two\n+ three") == "• one\n• two\n• three"

    def test_link(self):
        assert content.md_to_telegram_html("[t](https://e.com/a_b)") == \
            '<a href="https://e.com/a_b">t</a>'

    def test_html_special_chars_escaped(self):
        assert content.md_to_telegram_html("x < y & z > w") == "x &lt; y &amp; z &gt; w"

    def test_inline_code_contents_escaped_not_formatted(self):
        # markdown/HTML inside a code span is literal
        assert content.md_to_telegram_html("`a<b **no**`") == "<code>a&lt;b **no**</code>"

    def test_fenced_code_block(self):
        out = content.md_to_telegram_html("```\nx = a < b & c\n```")
        assert out == "<pre>x = a &lt; b &amp; c</pre>"

    def test_fenced_with_language_marker(self):
        out = content.md_to_telegram_html("```python\nprint(1)\n```")
        assert out == '<pre><code class="language-python">print(1)</code></pre>' 

    def test_tool_header_styled(self):
        assert content.md_to_telegram_html("🔧 Bash(ls | grep x)") == \
            "<pre>💻 Bash(ls | grep x)</pre>"

    def test_tool_header_no_args(self):
        assert content.md_to_telegram_html("🔧 AskUserQuestion()") == "<pre>❓ AskUserQuestion</pre>"

    def test_per_tool_icons(self):
        def icon(line):
            html = content.md_to_telegram_html(line)
            return html.removeprefix("<pre>").split(" ", 1)[0]
        assert icon("🔧 Bash(x)") == "💻"
        assert icon("🔧 Read(x)") == "📖"
        assert icon("🔧 Edit(x)") == "✏️"
        assert icon("🔧 WebFetch(x)") == "🌐"
        assert icon("🔧 mcp__srv__grep(x)") == "🔎"   # MCP suffix mapped
        assert icon("🔧 Frobnicate(x)") == "🔧"       # unknown → default wrench

    def test_tool_line_with_result_tail_stays_in_block(self):
        # a header with trailing text (e.g. "⎿ result") must not escape to prose
        out = content.md_to_telegram_html("🔧 Update(foo.py) ⎿  6 lines")
        assert out == "<pre>✏️ Update(foo.py) ⎿  6 lines</pre>"

    def test_truncated_tool_line_stays_in_block(self):
        # a wrapped/truncated header (no closing paren) still renders in-block
        out = content.md_to_telegram_html("🔧 Bash(cd /foo &&")
        assert out == "<pre>💻 Bash(cd /foo &amp;&amp;</pre>"

    def test_stray_markers_literal(self):
        # unpaired * / _ stay literal (don't produce dangling tags)
        out = content.md_to_telegram_html("2 * 3 = 6 and a_variable_name")
        assert "<i>" not in out and "<b>" not in out
        assert out == "2 * 3 = 6 and a_variable_name"

    def test_output_tags_balanced(self):
        # whatever we emit, every open tag has a close (valid Telegram HTML)
        import re
        for t in ["**a** *b*", "# h", "`c`", "```\nx\n```", "[l](u)", "~~s~~",
                  "🔧 Read(x)", "**unclosed", "weird ** ** stuff"]:
            out = content.md_to_telegram_html(t)
            opens = re.findall(r"<(\w+)", out)
            closes = re.findall(r"</(\w+)", out)
            # <a href> counts once; sort-compare open/close tag names
            opens = [o for o in opens]
            assert sorted(opens) == sorted(closes), f"unbalanced for {t!r}: {out!r}"


class TestChunkHtml:
    def test_short_single_chunk(self):
        assert telegram._chunk_html("hello world", 100) == ["hello world"]

    def test_splits_by_line(self):
        body = "\n".join(f"line{i}" for i in range(10))
        chunks = telegram._chunk_html(body, 25)
        assert len(chunks) > 1
        assert "\n".join(chunks).replace("\n", "") == body.replace("\n", "")

    def test_pre_block_kept_atomic(self):
        body = "before\n<pre>a\nb\nc\nd</pre>\nafter"
        chunks = telegram._chunk_html(body, 20)
        # the <pre> block is never split across chunks
        pre_chunks = [c for c in chunks if "<pre>" in c]
        assert len(pre_chunks) == 1
        assert "<pre>a\nb\nc\nd</pre>" in pre_chunks[0]


class TestStripHtml:
    def test_strip(self):
        assert telegram._strip_html_tags("<b>hi</b> &amp; <code>x&lt;y</code>") == "hi & x<y"
