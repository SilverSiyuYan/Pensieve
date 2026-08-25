"""Static checks for the dependency-free memory management page."""

from pathlib import Path


HTML = (Path(__file__).resolve().parents[1] / "frontend" / "index.html").read_text(encoding="utf-8")
API_RUNTIME = (Path(__file__).resolve().parents[1] / "frontend" / "api-runtime.js").read_text(
    encoding="utf-8"
)


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
    assert "无法连接后端服务" in API_RUNTIME
    assert "resolveApiBase" in HTML
    assert "fetchWithTimeout" in HTML
    assert "请求超时" in API_RUNTIME
    assert "网络拒绝、CORS 或浏览器安全策略" in API_RUNTIME
    assert 'mode: \'no-cors\'' not in HTML
    assert "登录失效或尚未登录" in API_RUNTIME
    assert "无权限执行此操作" in API_RUNTIME
    assert "前后端版本不匹配" in API_RUNTIME
    assert "后端内部错误" in API_RUNTIME
    assert "模型服务响应超时" in API_RUNTIME
    assert "后端响应格式异常" in API_RUNTIME


def test_long_running_memory_request_has_a_dedicated_timeout() -> None:
    assert "async function request(path, options = {}, timeoutMs = 10000)" in HTML
    assert "fetchWithTimeout(requestUrl, options, timeoutMs)" in HTML
    assert "}, 120000);" in HTML


def test_api_base_has_one_explicit_priority_and_is_normalised() -> None:
    assert '<script src="config.js?v=0.2.0-timeout-fix"></script>' in HTML
    assert '<script src="api-runtime.js?v=0.2.0-timeout-fix"></script>' in HTML
    assert "resolveConfiguredApiBase(window.location.href, projectApiBase)" in HTML
    assert "const verifyApiAvailability = async () =>" in HTML
    assert "memory-agent-api-base" not in HTML
    assert "apiCandidates" not in HTML


def test_category_labels_empty_error_and_narrow_layout_are_present() -> None:
    assert "{ inspiration: '灵感', todo: '待办', knowledge: '知识点', note: '便签' }" in HTML
    assert "当前分类下还没有记忆。" in HTML
    assert "加载记忆失败。" in HTML
    assert "@media (max-width:480px)" in HTML
    assert ".memory-controls { grid-template-columns:1fr; }" in HTML


def test_calendar_navigation_and_monday_first_layout_are_present() -> None:
    assert 'id="calendar-view-button"' in HTML
    assert '>日历</button>' in HTML
    assert 'id="calendar-title"' in HTML
    assert '>上个月</button>' in HTML
    assert '>今天</button>' in HTML
    assert '>下个月</button>' in HTML
    weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    positions = [HTML.index(f'<span class="weekday">{weekday}</span>') for weekday in weekdays]
    assert positions == sorted(positions)
    assert "payload.days[0].weekday - 1" in HTML


def test_calendar_month_switching_and_api_loading_are_wired() -> None:
    assert "shiftCalendarMonth(-1)" in HTML
    assert "shiftCalendarMonth(1)" in HTML
    assert "loadCalendarMonth()" in HTML
    assert "`/api/calendar/month?year=${calendarYear}&month=${calendarMonth}`" in HTML
    assert "calendarTitle.textContent = `${payload.year} 年 ${payload.month} 月`" in HTML


def test_calendar_highlights_today_and_selected_date() -> None:
    assert "timeZone: applicationTimezone" in HTML
    assert "applicationTimezone = health.timezone" in HTML
    assert "cell.classList.add('today')" in HTML
    assert "cell.classList.add('selected')" in HTML
    assert ".calendar-day.today .day-number" in HTML
    assert ".calendar-day.selected" in HTML


def test_clicking_calendar_day_loads_two_semantic_groups() -> None:
    assert "cell.addEventListener('click', () => selectCalendarDay(day.date))" in HTML
    assert "`/api/calendar/day?date=${dateKey}&sort_order=${calendarSortOrder.value}`" in HTML
    assert '>当天写入 <span id="created-memory-count"></span>' in HTML
    assert '>内容提及当天 <span id="mentioned-memory-count"></span>' in HTML
    assert "payload.created_memories" in HTML
    assert "payload.mentioned_memories" in HTML


def test_calendar_counts_hide_zero_values() -> None:
    assert "if (day.created_count > 0)" in HTML
    assert "if (day.mentioned_count > 0)" in HTML
    assert "写入 ${day.created_count}" in HTML
    assert "提及 ${day.mentioned_count}" in HTML


def test_calendar_empty_loading_and_error_states_are_present() -> None:
    assert "正在加载月历…" in HTML
    assert "正在加载当天详情…" in HTML
    assert "当天没有写入记忆。" in HTML
    assert "没有记忆提及当天。" in HTML
    assert "月历加载失败：${error.message}" in HTML
    assert "当天详情加载失败：${error.message}" in HTML


def test_calendar_mentioned_memories_show_original_expressions() -> None:
    assert "memory.date_mentions || []" in HTML
    assert "原始日期：${mention.original_expression}" in HTML
    assert "renderCalendarGroup(mentionedMemoryList, payload.mentioned_memories" in HTML


def test_calendar_sort_control_reloads_selected_day() -> None:
    assert 'id="calendar-sort-order"' in HTML
    assert '<option value="desc">从近到远</option>' in HTML
    assert '<option value="asc">从远到近</option>' in HTML
    assert "calendarSortOrder.addEventListener('change'" in HTML
    assert "if (selectedCalendarDate) loadCalendarDay(selectedCalendarDate)" in HTML


def test_calendar_mobile_layout_stacks_panels_and_compacts_cells() -> None:
    assert "@media (max-width:820px)" in HTML
    assert ".layout,.calendar-view { grid-template-columns:1fr; }" in HTML
    assert "@media (max-width:600px)" in HTML
    assert ".calendar-day,.calendar-blank { min-height:64px; padding:4px; }" in HTML
