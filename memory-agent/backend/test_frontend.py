"""Static checks for the dependency-free memory management page."""

from pathlib import Path


HTML = (Path(__file__).resolve().parents[1] / "frontend" / "index.html").read_text(encoding="utf-8")


def test_memory_filter_and_sort_controls_have_expected_defaults() -> None:
    assert 'id="memory-category"' in HTML
    assert '<option value="">全部</option>' in HTML
    assert '<option value="inspiration">灵感</option>' in HTML
    assert '<option value="todo">待办</option>' in HTML
    assert '<option value="knowledge">知识点</option>' in HTML
    assert '<option value="note">便签</option>' in HTML
    assert 'id="memory-sort-order"' in HTML
    assert '<option value="desc">从近到远</option>' in HTML
    assert '<option value="asc">从远到近</option>' in HTML


def test_frontend_sends_filter_and_sort_parameters() -> None:
    assert "new URLSearchParams({ limit: '100', offset: '0', sort_order: memorySortOrder.value })" in HTML
    assert "parameters.set('category', memoryCategory.value)" in HTML
    assert "memoryCategory.addEventListener('change', loadMemories)" in HTML
    assert "memorySortOrder.addEventListener('change', loadMemories)" in HTML


def test_network_failures_show_actionable_backend_error() -> None:
    assert "无法连接后端服务" in HTML
    assert "请确认服务已启动且 API 地址正确" in HTML


def test_category_labels_empty_error_and_narrow_layout_are_present() -> None:
    assert "{ inspiration: '灵感', todo: '待办', knowledge: '知识点', note: '便签' }" in HTML
    assert "当前分类下还没有记忆。" in HTML
    assert "加载记忆失败。" in HTML
    assert "@media (max-width:480px)" in HTML
    assert ".memory-controls { grid-template-columns:1fr; }" in HTML
