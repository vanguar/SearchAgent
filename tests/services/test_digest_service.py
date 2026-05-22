from __future__ import annotations

from datetime import UTC, datetime

from app.services.digest_service import DigestService
from app.services.funnel_service import FunnelDashboardData, FunnelMetric, FunnelStageRow
from app.services.stats_service import (
    AttentionItem,
    RecentActivityItem,
    SourceQualityRow,
    StatsDashboardData,
    StatsKpiSummary,
    StatsWindow,
    StatsWindowLink,
    SummaryCard,
)


def _dashboard() -> StatsDashboardData:
    window = StatsWindow(
        key="7d",
        label_ru="Последние 7 дней",
        start_at=datetime(2026, 4, 11, 0, 0, tzinfo=UTC),
        end_at=datetime(2026, 4, 17, 12, 0, tzinfo=UTC),
        timeline_granularity="day",
        timeline_mode_label_ru="по дням",
    )
    return StatsDashboardData(
        window=window,
        window_links=(StatsWindowLink(key="7d", label_ru="7 дней", url="/stats?window=7d", is_active=True),),
        owner_label_ru="Основной поиск",
        generated_at=datetime(2026, 4, 17, 12, 0, tzinfo=UTC),
        kpi=StatsKpiSummary(
            vacancies_found_total=8,
            hot_count=3,
            maybe_count=2,
            rejected_count=1,
            viewed_count=4,
            opened_original_count=3,
            saved_count=3,
            applications_sent=2,
            replies_received=1,
            rejections_count=0,
            interviews_count=1,
            archived_count=0,
            due_follow_ups=1,
            overdue_next_actions=2,
        ),
        summary_cards=(SummaryCard("Найдено", 8, "Последние 7 дней"),),
        source_rows=(
            SourceQualityRow(
                source_name="BA",
                found_count=5,
                hot_count=3,
                lead_count=3,
                applied_count=2,
                reply_count=1,
                interview_count=1,
                lead_conversion_label_ru="3 из 5 (60%)",
                application_conversion_label_ru="2 из 3 (67%)",
                response_rate_label_ru="1 из 2 (50%)",
                interview_rate_label_ru="1 из 2 (50%)",
            ),
            SourceQualityRow(
                source_name="Careerjet",
                found_count=3,
                hot_count=0,
                lead_count=1,
                applied_count=0,
                reply_count=0,
                interview_count=0,
                lead_conversion_label_ru="1 из 3 (33%)",
                application_conversion_label_ru="—",
                response_rate_label_ru="—",
                interview_rate_label_ru="—",
            ),
        ),
        timeline_rows=(),
        attention_items=(
            AttentionItem(
                title_ru="Сотрудник склада",
                subtitle_ru="Следующее действие · BA",
                due_at=datetime(2026, 4, 17, 9, 0, tzinfo=UTC),
                overdue=True,
                action_url="/leads/1",
            ),
        ),
        recent_activities=(
            RecentActivityItem(
                title_ru="Сотрудник склада",
                subtitle_ru="Отклик отправлен",
                event_label_ru="Отклик отправлен",
                occurred_at=datetime(2026, 4, 16, 10, 0, tzinfo=UTC),
                action_url="/leads/1",
            ),
        ),
        timeline_mode_label_ru="по дням",
        empty_state=False,
        warning_message=None,
    )


def _funnel() -> FunnelDashboardData:
    window = StatsWindow(
        key="7d",
        label_ru="Последние 7 дней",
        start_at=datetime(2026, 4, 11, 0, 0, tzinfo=UTC),
        end_at=datetime(2026, 4, 17, 12, 0, tzinfo=UTC),
        timeline_granularity="day",
        timeline_mode_label_ru="по дням",
    )
    return FunnelDashboardData(
        window=window,
        cohort_label_ru="Когорта найденных за последние 7 дней",
        stages=(
            FunnelStageRow("found", "Найдено", 8, ""),
            FunnelStageRow("saved", "Сохранено", 3, ""),
            FunnelStageRow("applied", "Отклик", 2, ""),
            FunnelStageRow("reply", "Ответ", 1, ""),
            FunnelStageRow("interview", "Собеседование", 1, ""),
        ),
        metrics=(
            FunnelMetric("found_to_saved", "Найдено -> сохранено", 3, 8, 38, "3 из 8 (38%)"),
            FunnelMetric("saved_to_applied", "Сохранено -> отклик", 2, 3, 67, "2 из 3 (67%)"),
            FunnelMetric("applied_to_reply", "Отклик -> ответ", 1, 2, 50, "1 из 2 (50%)"),
        ),
        empty_state=False,
        warning_message=None,
    )


def test_digest_service_builds_rule_based_russian_summary() -> None:
    digest = DigestService().build_digest(
        stats_dashboard=_dashboard(),
        funnel_dashboard=_funnel(),
    )

    assert "найдено 8" in digest.headline_ru
    assert any("BA" in line for line in digest.highlights_ru)
    assert any("утечка" in line for line in digest.highlights_ru)
    assert any("просроченных действий 2" in line for line in digest.highlights_ru)
    assert digest.next_action_ru.startswith("Следующий очевидный шаг:")


def test_digest_service_handles_empty_state() -> None:
    window = StatsWindow(
        key="7d",
        label_ru="Последние 7 дней",
        start_at=datetime(2026, 4, 11, 0, 0, tzinfo=UTC),
        end_at=datetime(2026, 4, 17, 12, 0, tzinfo=UTC),
        timeline_granularity="day",
        timeline_mode_label_ru="по дням",
    )
    empty_dashboard = StatsDashboardData(
        window=window,
        window_links=(),
        owner_label_ru=None,
        generated_at=datetime(2026, 4, 17, 12, 0, tzinfo=UTC),
        kpi=StatsKpiSummary(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
        summary_cards=(),
        source_rows=(),
        timeline_rows=(),
        attention_items=(),
        recent_activities=(),
        timeline_mode_label_ru="по дням",
        empty_state=True,
        warning_message=None,
    )
    empty_funnel = FunnelDashboardData(
        window=window,
        cohort_label_ru="Когорта найденных за последние 7 дней",
        stages=(),
        metrics=(),
        empty_state=True,
        warning_message=None,
    )

    digest = DigestService().build_digest(stats_dashboard=empty_dashboard, funnel_dashboard=empty_funnel)

    assert "Статистика появится" in digest.headline_ru
    assert digest.next_action_ru.startswith("Следующий шаг:")
