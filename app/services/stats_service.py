from __future__ import annotations
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.logging import logger
from app.core.time import utc_now
from app.db.models.crm import ApplicationLead, LeadEvent, ReminderTask
from app.db.models.profiles import SearchProfile, UserProfile
from app.db.models.search import SearchRun, VacancyScore
from app.db.models.vacancies import VacancyCanonical, VacancySourceRecord

WindowKey = Literal["today", "7d", "30d", "all"]
TimelineGranularity = Literal["day", "week"]

GERMANY_TZ = ZoneInfo("Europe/Berlin")
VALID_WINDOW_KEYS: tuple[WindowKey, ...] = ("today", "7d", "30d", "all")
EMPTY_STATS_WARNING = "Сначала сохраните профиль и выполните поиск, чтобы статистика стала полезной."
DB_STATS_WARNING = "База статистики сейчас недоступна. Поиск и CRM продолжают работать, но аналитика временно пуста."


@dataclass(frozen=True, slots=True)
class StatsOwnerContext:
    user_profile_id: int
    search_profile_id: int | None
    profile_label: str


@dataclass(frozen=True, slots=True)
class SearchAnalyticsScope:
    canonicals: tuple[VacancyCanonical, ...]
    source_records: tuple[VacancySourceRecord, ...]
    latest_scores: dict[int, VacancyScore]


@dataclass(frozen=True, slots=True)
class StatsWindow:
    key: WindowKey
    label_ru: str
    start_at: datetime | None
    end_at: datetime
    timeline_granularity: TimelineGranularity
    timeline_mode_label_ru: str


@dataclass(frozen=True, slots=True)
class StatsWindowLink:
    key: WindowKey
    label_ru: str
    url: str
    is_active: bool


@dataclass(frozen=True, slots=True)
class SummaryCard:
    title_ru: str
    value: int
    subtitle_ru: str


@dataclass(frozen=True, slots=True)
class SourceQualityRow:
    source_name: str
    found_count: int
    hot_count: int
    lead_count: int
    applied_count: int
    reply_count: int
    interview_count: int
    lead_conversion_label_ru: str
    application_conversion_label_ru: str
    response_rate_label_ru: str
    interview_rate_label_ru: str


@dataclass(frozen=True, slots=True)
class ActivityTimelineRow:
    label_ru: str
    found_count: int
    applications_count: int
    replies_count: int
    interviews_count: int


@dataclass(frozen=True, slots=True)
class AttentionItem:
    title_ru: str
    subtitle_ru: str
    due_at: datetime
    overdue: bool
    action_url: str


@dataclass(frozen=True, slots=True)
class RecentActivityItem:
    title_ru: str
    subtitle_ru: str
    event_label_ru: str
    occurred_at: datetime
    action_url: str


@dataclass(frozen=True, slots=True)
class StatsKpiSummary:
    vacancies_found_total: int
    hot_count: int
    maybe_count: int
    rejected_count: int
    viewed_count: int
    opened_original_count: int
    saved_count: int
    applications_sent: int
    replies_received: int
    rejections_count: int
    interviews_count: int
    archived_count: int
    due_follow_ups: int
    overdue_next_actions: int


@dataclass(frozen=True, slots=True)
class StatsDashboardData:
    window: StatsWindow
    window_links: tuple[StatsWindowLink, ...]
    owner_label_ru: str | None
    generated_at: datetime
    kpi: StatsKpiSummary
    summary_cards: tuple[SummaryCard, ...]
    source_rows: tuple[SourceQualityRow, ...]
    timeline_rows: tuple[ActivityTimelineRow, ...]
    attention_items: tuple[AttentionItem, ...]
    recent_activities: tuple[RecentActivityItem, ...]
    timeline_mode_label_ru: str
    empty_state: bool
    warning_message: str | None = None


def build_stats_window(window_key: str | None, *, now: datetime) -> StatsWindow:
    resolved_key: WindowKey = window_key if window_key in VALID_WINDOW_KEYS else "7d"
    local_now = now.astimezone(GERMANY_TZ)
    local_today_start = datetime.combine(local_now.date(), time.min, tzinfo=GERMANY_TZ)

    if resolved_key == "today":
        return StatsWindow(
            key="today",
            label_ru="Сегодня",
            start_at=local_today_start.astimezone(UTC),
            end_at=now,
            timeline_granularity="day",
            timeline_mode_label_ru="по дням",
        )
    if resolved_key == "7d":
        return StatsWindow(
            key="7d",
            label_ru="Последние 7 дней",
            start_at=(local_today_start - timedelta(days=6)).astimezone(UTC),
            end_at=now,
            timeline_granularity="day",
            timeline_mode_label_ru="по дням",
        )
    if resolved_key == "30d":
        return StatsWindow(
            key="30d",
            label_ru="Последние 30 дней",
            start_at=(local_today_start - timedelta(days=29)).astimezone(UTC),
            end_at=now,
            timeline_granularity="week",
            timeline_mode_label_ru="по неделям",
        )
    return StatsWindow(
        key="all",
        label_ru="Все время",
        start_at=None,
        end_at=now,
        timeline_granularity="week",
        timeline_mode_label_ru="по неделям",
    )


def build_window_links(*, active_key: WindowKey) -> tuple[StatsWindowLink, ...]:
    labels = {
        "today": "Сегодня",
        "7d": "7 дней",
        "30d": "30 дней",
        "all": "Все время",
    }
    return tuple(
        StatsWindowLink(
            key=key,
            label_ru=labels[key],
            url=f"/stats?window={key}",
            is_active=key == active_key,
        )
        for key in VALID_WINDOW_KEYS
    )


def resolve_stats_owner_context(
    session: Session, *, profile_id: int | None = None
) -> StatsOwnerContext | None:
    if profile_id is not None:
        search_profile = session.get(SearchProfile, profile_id)
    else:
        search_profile = session.execute(
            select(SearchProfile)
            .order_by(SearchProfile.is_default.desc(), SearchProfile.is_active.desc(), SearchProfile.id.asc())
        ).scalars().first()
    if search_profile is not None:
        user_profile = session.get(UserProfile, search_profile.user_profile_id)
        if user_profile is not None:
            return StatsOwnerContext(
                user_profile_id=user_profile.id,
                search_profile_id=search_profile.id,
                profile_label=search_profile.name or user_profile.display_name,
            )

    user_profile = session.execute(select(UserProfile).order_by(UserProfile.id.asc())).scalars().first()
    if user_profile is None:
        return None

    return StatsOwnerContext(
        user_profile_id=user_profile.id,
        search_profile_id=None,
        profile_label=user_profile.display_name,
    )


def ratio_label_ru(numerator: int, denominator: int) -> str:
    if denominator <= 0:
        return "—"
    percent = round((numerator / denominator) * 100)
    return f"{numerator} из {denominator} ({percent}%)"


def is_due_follow_up(lead: ApplicationLead, *, now: datetime) -> bool:
    follow_up_due_at = normalize_datetime(lead.follow_up_due_at)
    follow_up_sent_at = normalize_datetime(lead.follow_up_sent_at)
    current_time = normalize_datetime(now)
    if follow_up_due_at is None or current_time is None:
        return False
    if follow_up_sent_at is not None and follow_up_sent_at >= follow_up_due_at:
        return False
    return follow_up_due_at <= current_time


class StatsService:
    """Build the PHASE 9 dashboard read model from search and CRM entities."""

    def __init__(self, *, now_provider: Callable[[], datetime] | None = None) -> None:
        self._now_provider = now_provider or utc_now

    def build_dashboard_data(
        self,
        session: Session,
        *,
        window_key: str | None = None,
        profile_id: int | None = None,
    ) -> StatsDashboardData:
        generated_at = self._now_provider()
        window = build_stats_window(window_key, now=generated_at)
        window_links = build_window_links(active_key=window.key)

        try:
            owner_context = resolve_stats_owner_context(session, profile_id=profile_id)
            if owner_context is None:
                return self._empty_dashboard(
                    window=window,
                    window_links=window_links,
                    generated_at=generated_at,
                    warning_message=EMPTY_STATS_WARNING,
                )

            leads = self._load_leads(session, owner_context=owner_context)
            lead_ids = tuple(lead.id for lead in leads)
            reminders = self._load_reminders(session, owner_context=owner_context, lead_ids=lead_ids)
            lead_events = self._load_lead_events(session, lead_ids=lead_ids)
            search_runs = self._load_search_runs(session, search_profile_id=owner_context.search_profile_id)
            search_scope = self._load_search_scope(session, search_profile_id=owner_context.search_profile_id)

            kpi = self._build_kpi(
                window=window,
                now=generated_at,
                leads=leads,
                lead_events=lead_events,
                canonicals=search_scope.canonicals,
                latest_scores=search_scope.latest_scores,
                reminders=reminders,
            )
            source_rows = self._build_source_rows(
                window=window,
                leads=leads,
                source_records=search_scope.source_records,
                canonicals=search_scope.canonicals,
                latest_scores=search_scope.latest_scores,
            )
            timeline_rows = self._build_timeline_rows(
                window=window,
                leads=leads,
                canonicals=search_scope.canonicals,
            )
            attention_items = self._build_attention_items(
                now=generated_at,
                leads=leads,
                reminders=reminders,
            )
            recent_activities = self._build_recent_activity(
                search_runs=search_runs,
                lead_events=lead_events,
                leads=leads,
            )

            summary_cards = (
                SummaryCard("Найдено вакансий", kpi.vacancies_found_total, window.label_ru),
                SummaryCard("Hot", kpi.hot_count, "Сильные совпадения"),
                SummaryCard("Maybe", kpi.maybe_count, "Нужна ручная проверка"),
                SummaryCard("Rejected", kpi.rejected_count, "Отсечено правилами"),
                SummaryCard("Просмотрено", kpi.viewed_count, "Лиды с отмеченным просмотром"),
                SummaryCard("Открыт оригинал", kpi.opened_original_count, "Переходы к исходной вакансии"),
                SummaryCard("Сохранено", kpi.saved_count, "Лиды в CRM"),
                SummaryCard("Отклики", kpi.applications_sent, "Отправлено за период"),
                SummaryCard("Ответы", kpi.replies_received, "Получено за период"),
                SummaryCard("Собеседования", kpi.interviews_count, "Назначено за период"),
                SummaryCard("Отказы", kpi.rejections_count, "Получено за период"),
                SummaryCard("В архиве", kpi.archived_count, "Архивировано за период"),
                SummaryCard("Фоллоу-ап просрочен", kpi.due_follow_ups, "Актуально сейчас"),
                SummaryCard("Просрочено действий", kpi.overdue_next_actions, "Актуально сейчас"),
            )

            return StatsDashboardData(
                window=window,
                window_links=window_links,
                owner_label_ru=owner_context.profile_label,
                generated_at=generated_at,
                kpi=kpi,
                summary_cards=summary_cards,
                source_rows=source_rows,
                timeline_rows=timeline_rows,
                attention_items=attention_items,
                recent_activities=recent_activities,
                timeline_mode_label_ru=window.timeline_mode_label_ru,
                empty_state=self._is_empty(kpi=kpi, source_rows=source_rows, recent_activities=recent_activities),
            )
        except SQLAlchemyError:
            logger.exception("stats_dashboard_build_failed")
            return self._empty_dashboard(
                window=window,
                window_links=window_links,
                generated_at=generated_at,
                warning_message=DB_STATS_WARNING,
            )

    def _empty_dashboard(
        self,
        *,
        window: StatsWindow,
        window_links: tuple[StatsWindowLink, ...],
        generated_at: datetime,
        warning_message: str,
    ) -> StatsDashboardData:
        empty_kpi = StatsKpiSummary(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        empty_cards = (
            SummaryCard("Найдено вакансий", 0, window.label_ru),
            SummaryCard("Отклики", 0, "Пока пусто"),
            SummaryCard("Ответы", 0, "Пока пусто"),
        )
        return StatsDashboardData(
            window=window,
            window_links=window_links,
            owner_label_ru=None,
            generated_at=generated_at,
            kpi=empty_kpi,
            summary_cards=empty_cards,
            source_rows=(),
            timeline_rows=(),
            attention_items=(),
            recent_activities=(),
            timeline_mode_label_ru=window.timeline_mode_label_ru,
            empty_state=True,
            warning_message=warning_message,
        )

    def _build_kpi(
        self,
        *,
        window: StatsWindow,
        now: datetime,
        leads: tuple[ApplicationLead, ...],
        lead_events: tuple[LeadEvent, ...],
        canonicals: tuple[VacancyCanonical, ...],
        latest_scores: dict[int, VacancyScore],
        reminders: tuple[ReminderTask, ...],
    ) -> StatsKpiSummary:
        lead_found_count = sum(1 for lead in leads if in_window(lead.found_at, window))
        canonical_found = tuple(canonical for canonical in canonicals if in_window(canonical.first_seen_at, window))
        vacancies_found_total = len(canonical_found) if canonical_found else lead_found_count

        bucket_counter: Counter[str] = Counter()
        for canonical in canonical_found:
            score = latest_scores.get(canonical.id)
            if score is not None and score.bucket:
                bucket_counter[score.bucket] += 1

        if not bucket_counter:
            for event in lead_events:
                if event.event_type != "found" or not in_window(event.event_at, window):
                    continue
                if not isinstance(event.payload, dict):
                    continue
                bucket = str(event.payload.get("bucket", "")).strip()
                if bucket in {"hot", "maybe", "rejected"}:
                    bucket_counter[bucket] += 1

        overdue_reminders = sum(
            1
            for reminder in reminders
            if not reminder.is_done
            and normalize_datetime(reminder.due_at) is not None
            and normalize_datetime(reminder.due_at) <= normalize_datetime(now)
        )
        overdue_leads = sum(
            1
            for lead in leads
            if normalize_datetime(lead.next_action_at) is not None
            and normalize_datetime(lead.next_action_at) <= normalize_datetime(now)
            and lead.archived_at is None
        )

        return StatsKpiSummary(
            vacancies_found_total=vacancies_found_total,
            hot_count=bucket_counter.get("hot", 0),
            maybe_count=bucket_counter.get("maybe", 0),
            rejected_count=bucket_counter.get("rejected", 0),
            viewed_count=sum(1 for lead in leads if in_window(lead.viewed_at, window)),
            opened_original_count=sum(1 for lead in leads if in_window(lead.opened_original_at, window)),
            saved_count=sum(1 for lead in leads if in_window(lead.saved_at, window)),
            applications_sent=sum(1 for lead in leads if in_window(lead.applied_at, window)),
            replies_received=sum(1 for lead in leads if in_window(lead.reply_at, window)),
            rejections_count=sum(1 for lead in leads if in_window(lead.rejected_at, window)),
            interviews_count=sum(1 for lead in leads if in_window(lead.interview_at, window)),
            archived_count=sum(1 for lead in leads if in_window(lead.archived_at, window)),
            due_follow_ups=sum(1 for lead in leads if is_due_follow_up(lead, now=now)),
            overdue_next_actions=overdue_leads + overdue_reminders,
        )

    def _build_source_rows(
        self,
        *,
        window: StatsWindow,
        leads: tuple[ApplicationLead, ...],
        source_records: tuple[VacancySourceRecord, ...],
        canonicals: tuple[VacancyCanonical, ...],
        latest_scores: dict[int, VacancyScore],
    ) -> tuple[SourceQualityRow, ...]:
        found_counter: Counter[str] = Counter()
        hot_counter: Counter[str] = Counter()
        lead_counter: Counter[str] = Counter()
        applied_counter: Counter[str] = Counter()
        reply_counter: Counter[str] = Counter()
        interview_counter: Counter[str] = Counter()

        linked_canonical_ids = {canonical.id for canonical in canonicals}
        source_names_by_canonical: dict[int, set[str]] = defaultdict(set)

        for source_record in source_records:
            source_name = source_record.source_name.strip()
            if source_record.canonical_id is not None and source_record.canonical_id in linked_canonical_ids:
                source_names_by_canonical[source_record.canonical_id].add(source_name)
            if in_window(source_record.first_seen_at, window):
                found_counter[source_name] += 1

        for canonical_id, source_names in source_names_by_canonical.items():
            score = latest_scores.get(canonical_id)
            if score is None or score.bucket != "hot":
                continue
            canonical = next((item for item in canonicals if item.id == canonical_id), None)
            if canonical is None or not in_window(canonical.first_seen_at, window):
                continue
            for source_name in source_names:
                hot_counter[source_name] += 1

        for lead in leads:
            source_name = (lead.source_name or "Без источника").strip()
            if in_window(lead.found_at, window):
                lead_counter[source_name] += 1
            if in_window(lead.applied_at, window):
                applied_counter[source_name] += 1
            if in_window(lead.reply_at, window):
                reply_counter[source_name] += 1
            if in_window(lead.interview_at, window):
                interview_counter[source_name] += 1

        source_names = sorted(
            {
                *found_counter.keys(),
                *hot_counter.keys(),
                *lead_counter.keys(),
                *applied_counter.keys(),
                *reply_counter.keys(),
                *interview_counter.keys(),
            }
        )
        rows = [
            SourceQualityRow(
                source_name=source_name,
                found_count=found_counter[source_name],
                hot_count=hot_counter[source_name],
                lead_count=lead_counter[source_name],
                applied_count=applied_counter[source_name],
                reply_count=reply_counter[source_name],
                interview_count=interview_counter[source_name],
                lead_conversion_label_ru=ratio_label_ru(lead_counter[source_name], found_counter[source_name]),
                application_conversion_label_ru=ratio_label_ru(applied_counter[source_name], lead_counter[source_name]),
                response_rate_label_ru=ratio_label_ru(reply_counter[source_name], applied_counter[source_name]),
                interview_rate_label_ru=ratio_label_ru(interview_counter[source_name], applied_counter[source_name]),
            )
            for source_name in source_names
        ]
        return tuple(
            sorted(
                rows,
                key=lambda row: (
                    -row.reply_count,
                    -row.interview_count,
                    -row.applied_count,
                    -row.hot_count,
                    -row.found_count,
                    row.source_name,
                ),
            )
        )

    def _build_timeline_rows(
        self,
        *,
        window: StatsWindow,
        leads: tuple[ApplicationLead, ...],
        canonicals: tuple[VacancyCanonical, ...],
    ) -> tuple[ActivityTimelineRow, ...]:
        counters: dict[str, Counter[datetime]] = {
            "found": Counter(),
            "applications": Counter(),
            "replies": Counter(),
            "interviews": Counter(),
        }

        found_timestamps = [canonical.first_seen_at for canonical in canonicals if canonical.first_seen_at is not None]
        if not found_timestamps:
            found_timestamps = [lead.found_at for lead in leads if lead.found_at is not None]
        for found_at in found_timestamps:
            if in_window(found_at, window):
                counters["found"][period_start(found_at, granularity=window.timeline_granularity)] += 1
        for lead in leads:
            if in_window(lead.applied_at, window):
                counters["applications"][period_start(lead.applied_at, granularity=window.timeline_granularity)] += 1
            if in_window(lead.reply_at, window):
                counters["replies"][period_start(lead.reply_at, granularity=window.timeline_granularity)] += 1
            if in_window(lead.interview_at, window):
                counters["interviews"][period_start(lead.interview_at, granularity=window.timeline_granularity)] += 1

        ordered_periods = timeline_periods(window=window, counters=tuple(counters.values()))
        return tuple(
            ActivityTimelineRow(
                label_ru=format_period_label(period, granularity=window.timeline_granularity),
                found_count=counters["found"][period],
                applications_count=counters["applications"][period],
                replies_count=counters["replies"][period],
                interviews_count=counters["interviews"][period],
            )
            for period in ordered_periods
        )

    def _build_attention_items(
        self,
        *,
        now: datetime,
        leads: tuple[ApplicationLead, ...],
        reminders: tuple[ReminderTask, ...],
    ) -> tuple[AttentionItem, ...]:
        items: list[AttentionItem] = []
        horizon = now + timedelta(days=3)

        for lead in leads:
            if lead.archived_at is not None or lead.next_action_at is None:
                continue
            next_action_at = normalize_datetime(lead.next_action_at)
            if next_action_at is None or next_action_at > horizon:
                continue
            items.append(
                AttentionItem(
                    title_ru=lead.translated_title_ru or lead.vacancy_title,
                    subtitle_ru=f"Следующее действие · {(lead.source_name or 'источник').strip()}",
                    due_at=next_action_at,
                    overdue=next_action_at <= now,
                    action_url=f"/leads/{lead.id}",
                )
            )

        for reminder in reminders:
            due_at = normalize_datetime(reminder.due_at)
            if reminder.is_done or due_at is None or due_at > horizon:
                continue
            action_url = f"/leads/{reminder.lead_id}" if reminder.lead_id is not None else "/leads"
            items.append(
                AttentionItem(
                    title_ru=reminder.title,
                    subtitle_ru="Напоминание",
                    due_at=due_at,
                    overdue=due_at <= now,
                    action_url=action_url,
                )
            )

        return tuple(
            sorted(
                items,
                key=lambda item: (
                    not item.overdue,
                    item.due_at,
                    item.title_ru.casefold(),
                ),
            )[:6]
        )

    def _build_recent_activity(
        self,
        *,
        search_runs: tuple[SearchRun, ...],
        lead_events: tuple[LeadEvent, ...],
        leads: tuple[ApplicationLead, ...],
    ) -> tuple[RecentActivityItem, ...]:
        lead_title_map = {
            lead.id: lead.translated_title_ru or lead.vacancy_title
            for lead in leads
        }
        items: list[RecentActivityItem] = []

        event_labels = {
            "found": "Найдена вакансия",
            "viewed": "Просмотр",
            "opened_original_link": "Открыт оригинал",
            "saved": "Сохранено",
            "applied": "Отклик отправлен",
            "reply_received": "Получен ответ",
            "rejected": "Получен отказ",
            "interview_scheduled": "Назначено собеседование",
            "archived": "Перенесено в архив",
            "note_added": "Добавлена заметка",
            "reminder_added": "Создано напоминание",
        }

        for event in lead_events:
            title_ru = lead_title_map.get(event.lead_id, "Лид")
            occurred_at = normalize_datetime(event.event_at)
            if occurred_at is None:
                continue
            items.append(
                RecentActivityItem(
                    title_ru=title_ru,
                    subtitle_ru=event_labels.get(event.event_type, event.event_type),
                    event_label_ru=event_labels.get(event.event_type, event.event_type),
                    occurred_at=occurred_at,
                    action_url=f"/leads/{event.lead_id}",
                )
            )

        for search_run in search_runs:
            activity_time = normalize_datetime(search_run.finished_at or search_run.started_at)
            if activity_time is None:
                continue
            items.append(
                RecentActivityItem(
                    title_ru="Запуск поиска",
                    subtitle_ru=(
                        f"Найдено {search_run.total_fetched}, "
                        f"оценено {search_run.total_scored}, новых {search_run.total_new}"
                    ),
                    event_label_ru="Поиск вакансий",
                    occurred_at=activity_time,
                    action_url="/jobs",
                )
            )

        return tuple(
            sorted(
                items,
                key=lambda item: item.occurred_at,
                reverse=True,
            )[:6]
        )

    def _load_lead_events(self, session: Session, *, lead_ids: tuple[int, ...]) -> tuple[LeadEvent, ...]:
        if not lead_ids:
            return ()
        return tuple(
            session.execute(
                select(LeadEvent)
                .where(LeadEvent.lead_id.in_(lead_ids))
                .order_by(LeadEvent.event_at.desc(), LeadEvent.id.desc())
            ).scalars()
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

    def _load_reminders(
        self,
        session: Session,
        *,
        owner_context: StatsOwnerContext,
        lead_ids: tuple[int, ...],
    ) -> tuple[ReminderTask, ...]:
        if owner_context.search_profile_id is None:
            query = (
                select(ReminderTask)
                .where(ReminderTask.user_profile_id == owner_context.user_profile_id)
                .order_by(ReminderTask.id.asc())
            )
            return tuple(session.execute(query).scalars())
        if not lead_ids:
            return ()
        query = (
            select(ReminderTask)
            .where(ReminderTask.lead_id.in_(lead_ids))
            .order_by(ReminderTask.id.asc())
        )
        return tuple(session.execute(query).scalars())

    def _load_search_runs(self, session: Session, *, search_profile_id: int | None) -> tuple[SearchRun, ...]:
        if search_profile_id is None:
            return ()
        query = select(SearchRun).order_by(SearchRun.finished_at.desc(), SearchRun.id.desc())
        query = query.where(SearchRun.search_profile_id == search_profile_id)
        return tuple(session.execute(query).scalars())

    def _load_search_scope(
        self,
        session: Session,
        *,
        search_profile_id: int | None,
    ) -> SearchAnalyticsScope:
        if search_profile_id is None:
            return SearchAnalyticsScope(canonicals=(), source_records=(), latest_scores={})

        score_query = (
            select(VacancyScore)
            .where(VacancyScore.search_profile_id == search_profile_id)
            .order_by(VacancyScore.scored_at.desc(), VacancyScore.id.desc())
        )
        latest_scores: dict[int, VacancyScore] = {}
        for score in session.execute(score_query).scalars():
            latest_scores.setdefault(score.vacancy_canonical_id, score)
        canonical_ids = tuple(sorted(latest_scores.keys()))
        if not canonical_ids:
            return SearchAnalyticsScope(canonicals=(), source_records=(), latest_scores={})

        canonicals = tuple(
            session.execute(
                select(VacancyCanonical)
                .where(VacancyCanonical.id.in_(canonical_ids))
                .order_by(VacancyCanonical.id.asc())
            ).scalars()
        )
        source_records = tuple(
            session.execute(
                select(VacancySourceRecord)
                .where(VacancySourceRecord.canonical_id.in_(canonical_ids))
                .order_by(VacancySourceRecord.id.asc())
            ).scalars()
        )
        return SearchAnalyticsScope(
            canonicals=canonicals,
            source_records=source_records,
            latest_scores=latest_scores,
        )

    def _is_empty(
        self,
        *,
        kpi: StatsKpiSummary,
        source_rows: tuple[SourceQualityRow, ...],
        recent_activities: tuple[RecentActivityItem, ...],
    ) -> bool:
        return (
            kpi.vacancies_found_total == 0
            and kpi.saved_count == 0
            and kpi.applications_sent == 0
            and kpi.replies_received == 0
            and kpi.interviews_count == 0
            and not source_rows
            and not recent_activities
        )


def in_window(value: datetime | None, window: StatsWindow) -> bool:
    normalized_value = normalize_datetime(value)
    if normalized_value is None:
        return False
    if window.start_at is None:
        return True
    normalized_start = normalize_datetime(window.start_at)
    normalized_end = normalize_datetime(window.end_at)
    if normalized_start is None or normalized_end is None:
        return True
    return normalized_start <= normalized_value <= normalized_end


def period_start(value: datetime, *, granularity: TimelineGranularity) -> datetime:
    normalized_value = normalize_datetime(value)
    if normalized_value is None:
        raise ValueError("Expected datetime value for timeline period.")
    localized = normalized_value.astimezone(GERMANY_TZ)
    if granularity == "day":
        return datetime.combine(localized.date(), time.min, tzinfo=GERMANY_TZ)
    week_start = localized.date() - timedelta(days=localized.weekday())
    return datetime.combine(week_start, time.min, tzinfo=GERMANY_TZ)


def timeline_periods(window: StatsWindow, *, counters: tuple[Counter[datetime], ...]) -> tuple[datetime, ...]:
    if window.start_at is not None:
        start = period_start(window.start_at, granularity=window.timeline_granularity)
        end = period_start(window.end_at, granularity=window.timeline_granularity)
        step = timedelta(days=1 if window.timeline_granularity == "day" else 7)
        periods: list[datetime] = []
        current = start
        while current <= end:
            periods.append(current)
            current += step
        return tuple(periods)

    all_keys = sorted({key for counter in counters for key in counter.keys()})
    return tuple(all_keys)


def format_period_label(value: datetime, *, granularity: TimelineGranularity) -> str:
    if granularity == "day":
        return value.strftime("%d.%m")
    return f"Неделя {value.strftime('%d.%m')}"


def normalize_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
