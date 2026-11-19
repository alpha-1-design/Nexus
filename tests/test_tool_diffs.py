"""Tests for unified diff emission in write/edit tool results.

The TUI renders these diffs as colored blocks inside tool-call cards; the
model also sees them as tool-result context. Diffs are capped so huge
rewrites don't flood either consumer.
"""

import pytest

from nexus.tools.core import (
    DIFF_CAP_LINES,
    EditTool,
    WriteTool,
    _append_diff,
    _capped_unified_diff,
)


class TestCappedUnifiedDiff:
    def test_identical_text_yields_empty_diff(self):
        assert _capped_unified_diff("same\n", "same\n", "f.txt") == ""

    def test_diff_uses_git_style_headers(self):
        diff = _capped_unified_diff("a\n", "b\n", "src/x.py")
        assert "--- a/src/x.py" in diff
        assert "+++ b/src/x.py" in diff
        assert "@@" in diff

    def test_capped_with_marker(self):
        before = ""
        after = "".join(f"line{i}\n" for i in range(200))
        diff = _capped_unified_diff(before, after, "big.txt", cap=80)
        lines = diff.splitlines()
        assert len(lines) == DIFF_CAP_LINES + 1  # capped lines + marker
        assert lines[-1] == f"... {200 + 3 - DIFF_CAP_LINES} more lines"

    def test_append_diff_noop_on_empty(self):
        assert _append_diff("done", "") == "done"

    def test_append_diff_joins_with_blank_line(self):
        out = _append_diff("done", "@@\n+x")
        assert out == "done\n\n@@\n+x"


@pytest.mark.asyncio
class TestWriteToolDiff:
    async def test_new_file_includes_full_addition_diff(self, tmp_path):
        tool = WriteTool()
        path = str(tmp_path / "new.txt")
        result = await tool.execute(path=path, content="alpha\nbeta\n")
        assert result.success
        assert result.content.startswith(f"File written successfully: {path}")
        assert "+++ b/" in result.content
        assert "+alpha" in result.content
        diff = result.metadata["diff"]
        assert diff.endswith("+alpha\n+beta\n")
        # the same diff text is embedded in the result string
        assert diff in result.content

    async def test_overwrite_shows_removed_and_added_lines(self, tmp_path):
        path = tmp_path / "f.txt"
        path.write_text("keep\nold\n")
        tool = WriteTool()
        result = await tool.execute(path=str(path), content="keep\nnew\n")
        diff = result.metadata["diff"]
        assert "-old" in diff
        assert "+new" in diff
        assert "-keep" not in diff  # unchanged context stays plain

    async def test_metadata_includes_diff_and_path(self, tmp_path):
        tool = WriteTool()
        path = str(tmp_path / "m.txt")
        result = await tool.execute(path=path, content="x\n")
        assert result.metadata["path"] == path
        assert result.metadata["diff"]
        assert result.metadata["bytes_written"] == 2


@pytest.mark.asyncio
class TestEditToolDiff:
    async def test_edit_result_contains_hunk(self, tmp_path):
        path = tmp_path / "code.py"
        path.write_text("def a():\n    return 1\n")
        tool = EditTool()
        result = await tool.execute(
            path=str(path), old_string="return 1", new_string="return 2"
        )
        assert result.success
        assert result.content.startswith(f"Edit applied successfully to {path}")
        diff = result.metadata["diff"]
        assert "-    return 1" in diff
        assert "+    return 2" in diff
        assert " def a():" in diff  # context line

    async def test_error_result_has_no_diff(self, tmp_path):
        tool = EditTool()
        result = await tool.execute(
            path=str(tmp_path / "nope.txt"), old_string="a", new_string="b"
        )
        assert not result.success
        assert result.metadata.get("diff") is None
