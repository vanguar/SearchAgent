from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.logging import logger
from app.core.time import utc_now
from app.db.models.crm import ApplicationLead
from app.services.stats_service import (
    EMPTY_STATS_WARNING,
    StatsOwnerContext,
    StatsWindow,
    build_stats_window,
    in_window,
    ratio_label_ru,
    resolve_stats_owner_context,
)


@dataclass(frozen=True, slots=True)
class FunnelStageRow:
    key: str
    label_ru: str
    count: int
    subtitle_ru: str


@dataclass(frozen=True, slots=True)
class FunnelMetric:
    key: str
    label_ru: str
    numerator: int
    denominator: int
    percent: int | None
    value_ru: str


@dataclass(frozen=True, slots=True)
class FunnelDashboardData:
    window: StatsWindow
    cohort_label_ru: str
    stages: tuple[FunnelStageRow, ...]
    metrics: tuple[FunnelMetric, ...]
    empty_state: bool
    warning_message: str | None = None


class FunnelService:
    """Lead-centric funnel for the accepted job-search workflow.

    The funnel intentionally uses one cohort only: ApplicationLead rows whose
    ``found_at`` is inside the selected window. Raw search-wide vacancy counts
    stay in the KPI dashboard so the funnel does not mix canonicals and leads.
    """

    def __init__(self, *, now_provider: Callable[[], datetime] | None = None) -> None:
        self._now_provider = now_provider or utc_now

    def build_dashboard_data(
        self,
        session: Session,
        *,
        window_key: str | None = None,
        profile_id: int | None = None,
    ) -> FunnelDashboardData:
        window = build_stats_window(window_key, now=self._now_provider())
        try:
            owner_context = resolve_stats_owner_context(session, profile_id=profile_id)
            if owner_context is None:
                return FunnelDashboardData(
                    window=window,
                    cohort_label_ru=_cohort_label(window),
                    stages=(),
                    metrics=(),
                    empty_state=True,
                    warning_message=EMPTY_STATS_WARNING,
                )

            leads = self._load_leads(session, owner_context=owner_context)
            cohort_leads = tuple(lead for lead in leads if in_window(lead.found_at, window))
            found_count = len(cohort_leads)

            viewed_count = sum(1 for lead in cohort_leads if lead.viewed_at is not None)
            opened_count = sum(1 for lead in cohort_leads if lead.opened_original_at is not None)
            saved_count = sum(1 for lead in cohort_leads if lead.saved_at is not None)
            applied_count = sum(1 for lead in cohort_leads if lead.applied_at is not None)
            reply_count = sum(1 for lead in cohort_leads if lead.reply_at is not None)
            interview_count = sum(1 for lead in cohort_leads if lead.interview_at is not None)
            rejected_count = sum(1 for lead in cohort_leads if lead.rejected_at is not None)
            archived_count = sum(1 for lead in cohort_leads if lead.archived_at is not None)

            stages = (
                FunnelStageRow("found", "Найдено", found_count, "Лиды, созданные из найденных вакансий за период"),
                FunnelStageRow("viewed", "Просмотрено", viewed_count, "Из этой же когорты лидов"),
                FunnelStageRow("opened", "Открыт оригинал", opened_count, "Из этой же когорты лидов"),
                FunnelStageRow("saved", "Сохранено", saved_count, "Из этой же когорты лидов"),
                FunnelStageRow("applied", "Отклик отправлен", applied_count, "Отклики по лидам из этой когорты"),
                FunnelStageRow("reply", "Получен ответ", reply_count, "Есть ответ работодателя"),
                FunnelStageRow("interview", "Назначено собеседование", interview_count, "Есть встреча или звонок"),
                FunnelStageRow("rejected", "Получен отказ", rejected_count, "Компания отказала"),
                FunnelStageRow("archived", "В архиве", archived_count, "Лид закрыт и убран из работы"),
            )
            metrics = (
                _ratio_metric("found_to_saved", "Найдено -> сохранено", saved_count, found_count),
                _ratio_metric("saved_to_applied", "Сохранено -> отклик", applied_count, saved_count),
                _ratio_metric("applied_to_reply", "Отклик -> ответ (response rate)", reply_count, applied_count),
                _ratio_metric(
                    "applied_to_interview",
                    "Отклик -> собеседование (interview rate)",
                    interview_count,
                    applied_count,
                ),
                _ratio_metric("applied_to_rejected", "Отклик -> отказ (rejection rate)", rejected_count, applied_count),
            )

            return FunnelDashboardData(
                window=window,
                cohort_label_ru=_cohort_label(window),
                stages=stages,
                metrics=metrics,
                empty_state=not cohort_leads,
            )
        except SQLAlchemyError:
            logger.exception("funnel_dashboard_build_failed")
            return FunnelDashboardData(
                window=window,
                cohort_label_ru=_cohort_label(window),
                stages=(),
                metrics=(),
                empty_state=True,
                warning_message="Воронка временно недоступна из-за ошибки базы данных.",
            )

    def _load_leads(
        self,
        session: Session,
        *,
        owner_context: StatsOwnerContext,
    ) -> tuple[ApplicationLead, ...]:
        query = (
            select(ApplicationLead)
            .where(ApplicationLead.user_profile_id == owner_context.user_profile_id)
            .order_by(ApplicationLead.id.asc())
        )
        if owner_context.search_profile_id is not None:
            query = query.where(ApplicationLead.search_profile_id == owner_context.search_profile_id)
        return tuple(session.execute(query).scalars())


def _ratio_metric(key: str, label_ru: str, numerator: int, denominator: int) -> FunnelMetric:
    percent = round((numerator / denominator) * 100) if denominator > 0 else None
    return FunnelMetric(
        key=key,
        label_ru=label_ru,
        numerator=numerator,
        denominator=denominator,
        percent=percent,
        value_ru=ratio_label_ru(numerator, denominator),
    )


def _cohort_label(window: StatsWindow) -> str:
    if window.key == "today":
        return "Когорта лидов, найденных сегодня"
    if window.key == "all":
        return "Когорта лидов за все время"
    return f"Когорта лидов, найденных за {window.label_ru.casefold()}"
