from __future__ import annotations

from dataclasses import dataclass

from app.services.funnel_service import FunnelDashboardData
from app.services.stats_service import SourceQualityRow, StatsDashboardData


@dataclass(frozen=True, slots=True)
class StatsDigestData:
    headline_ru: str
    highlights_ru: tuple[str, ...]
    next_action_ru: str


class DigestService:
    """Rule-based Russian digest for the local PHASE 9 dashboard."""

    def build_digest(
        self,
        *,
        stats_dashboard: StatsDashboardData,
        funnel_dashboard: FunnelDashboardData,
    ) -> StatsDigestData:
        if stats_dashboard.empty_state and funnel_dashboard.empty_state:
            return StatsDigestData(
                headline_ru="Статистика появится после первых запусков поиска и первых сохраненных лидов.",
                highlights_ru=(
                    "Пока нет данных по источникам, воронке и активности.",
                    "Сначала нужен хотя бы один реальный поиск и один лид в CRM.",
                ),
                next_action_ru="Следующий шаг: запустить поиск вакансий и сохранить в отклики хотя бы одну подходящую карточку.",
            )

        kpi = stats_dashboard.kpi
        headline = (
            f"{stats_dashboard.window.label_ru}: найдено {kpi.vacancies_found_total}, "
            f"сохранено {kpi.saved_count}, откликов {kpi.applications_sent}, "
            f"ответов {kpi.replies_received}, собеседований {kpi.interviews_count}."
        )
        highlights: list[str] = [
            self._progress_line(stats_dashboard=stats_dashboard),
            self._source_line(source_rows=stats_dashboard.source_rows),
            self._leak_line(funnel_dashboard=funnel_dashboard),
            self._attention_line(stats_dashboard=stats_dashboard),
        ]

        return StatsDigestData(
            headline_ru=headline,
            highlights_ru=tuple(line for line in highlights if line),
            next_action_ru=self._next_action_line(stats_dashboard=stats_dashboard),
        )

    def _progress_line(self, *, stats_dashboard: StatsDashboardData) -> str:
        kpi = stats_dashboard.kpi
        if kpi.applications_sent > 0 or kpi.replies_received > 0 or kpi.interviews_count > 0:
            return (
                f"Движение за период есть: откликов {kpi.applications_sent}, "
                f"ответов {kpi.replies_received}, собеседований {kpi.interviews_count}."
            )
        if kpi.vacancies_found_total > 0:
            return (
                f"Поиск приносит материал: найдено {kpi.vacancies_found_total}, "
                f"но активность после сохранения пока слабая."
            )
        return "Новых вакансий в выбранном окне пока нет."

    def _source_line(self, *, source_rows: tuple[SourceQualityRow, ...]) -> str:
        strongest = self._pick_strongest_source(source_rows)
        if strongest is None:
            return "Источники пока не с чем сравнивать: история еще слишком короткая."

        if strongest.reply_count > 0 or strongest.interview_count > 0:
            return (
                f"Сильнее всего сейчас выглядит {strongest.source_name}: "
                f"откликов {strongest.applied_count}, ответов {strongest.reply_count}, "
                f"собеседований {strongest.interview_count}."
            )
        return (
            f"По количеству полезных находок лидирует {strongest.source_name}: "
            f"найдено {strongest.found_count}, hot {strongest.hot_count}, лидов {strongest.lead_count}."
        )

    def _leak_line(self, *, funnel_dashboard: FunnelDashboardData) -> str:
        stage_map = {stage.key: stage.count for stage in funnel_dashboard.stages}
        found_count = stage_map.get("found", 0)
        saved_count = stage_map.get("saved", 0)
        applied_count = stage_map.get("applied", 0)
        reply_count = stage_map.get("reply", 0)
        interview_count = stage_map.get("interview", 0)
        transitions = [
            ("found_to_saved", found_count, saved_count),
            ("saved_to_applied", saved_count, applied_count),
            ("applied_to_reply", applied_count, reply_count),
            ("reply_to_interview", reply_count, interview_count),
        ]
        weakest = None
        weakest_ratio = 1.0
        for key, denominator, numerator in transitions:
            if denominator <= 0:
                continue
            ratio = numerator / denominator
            if ratio < weakest_ratio:
                weakest_ratio = ratio
                weakest = key

        if weakest == "found_to_saved":
            return "Главная утечка сейчас до CRM: вакансии находятся, но слишком мало из них доходит до сохранения."
        if weakest == "saved_to_applied":
            return "Главная утечка сейчас между сохранением и откликом: лиды есть, но отклики по ним уходят не у всех."
        if weakest == "applied_to_reply":
            return "После отклика воронка заметно проседает: ответов пока меньше, чем хотелось бы."
        if weakest == "reply_to_interview":
            return "Ответы уже появляются, но до собеседований эта когорта доходит слабо."
        return "По воронке нет критического провала: этапы двигаются без явной блокировки."

    def _attention_line(self, *, stats_dashboard: StatsDashboardData) -> str:
        overdue = stats_dashboard.kpi.overdue_next_actions
        follow_ups = stats_dashboard.kpi.due_follow_ups
        if overdue == 0 and follow_ups == 0:
            return "Просроченных действий и фоллоу-апов сейчас нет."
        return (
            f"Нужно внимание сейчас: просроченных действий {overdue}, "
            f"фоллоу-апов к сроку {follow_ups}."
        )

    def _next_action_line(self, *, stats_dashboard: StatsDashboardData) -> str:
        if not stats_dashboard.attention_items:
            return "Следующий шаг: открыть статистику после ближайшего нового поиска или отклика."
        top_item = stats_dashboard.attention_items[0]
        due_prefix = "просрочено" if top_item.overdue else "срок скоро"
        return (
            f"Следующий очевидный шаг: {top_item.title_ru} ({top_item.subtitle_ru.lower()}, {due_prefix})."
        )

    def _pick_strongest_source(self, source_rows: tuple[SourceQualityRow, ...]) -> SourceQualityRow | None:
        if not source_rows:
            return None
        return max(
            source_rows,
            key=lambda row: (
                row.reply_count,
                row.interview_count,
                row.applied_count,
                row.hot_count,
                row.lead_count,
                row.found_count,
            ),
        )
