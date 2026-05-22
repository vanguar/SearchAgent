from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _env_bool(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        return float(raw_value)
    except ValueError:
        return default


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings for local app bootstrap."""

    app_name: str = field(default_factory=lambda: os.getenv("APP_NAME", "SmartJob SearchAgent"))
    app_version: str = field(default_factory=lambda: os.getenv("APP_VERSION", "0.1.0"))
    debug: bool = field(default_factory=lambda: _env_bool("DEBUG", False))
    source_adapter_timeout_seconds: float = field(
        default_factory=lambda: _env_float("SOURCE_ADAPTER_TIMEOUT_SECONDS", 10.0)
    )

    # --- BA ---
    source_ba_enabled: bool = field(default_factory=lambda: _env_bool("SOURCE_BA_ENABLED", True))
    source_ba_base_url: str = field(
        default_factory=lambda: os.getenv(
            "SOURCE_BA_BASE_URL",
            "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service",
        )
    )
    source_ba_api_key: str = field(default_factory=lambda: os.getenv("SOURCE_BA_API_KEY", "jobboerse-jobsuche"))

    # --- Careerjet ---
    source_careerjet_enabled: bool = field(
        default_factory=lambda: _env_bool("SOURCE_CAREERJET_ENABLED", False)
    )
    source_careerjet_base_url: str = field(
        default_factory=lambda: os.getenv(
            "SOURCE_CAREERJET_BASE_URL",
            "https://search.api.careerjet.net/v4/query",
        )
    )
    # API-ключ Careerjet — регистрация издателя: https://www.careerjet.de/publisher/
    source_careerjet_api_key: str | None = field(
        default_factory=lambda: os.getenv("SOURCE_CAREERJET_API_KEY")
    )
    # Размер фрагмента описания вакансии (символов).
    # Больше символов → нормализатор получает больше контекста для сигналов.
    source_careerjet_fragment_size: int = field(
        default_factory=lambda: int(os.getenv("SOURCE_CAREERJET_FRAGMENT_SIZE", "500"))
    )

    # --- EURES ---
    source_eures_enabled: bool = field(default_factory=lambda: _env_bool("SOURCE_EURES_ENABLED", False))
    source_eures_base_url: str = field(
        default_factory=lambda: os.getenv(
            "SOURCE_EURES_BASE_URL",
            "https://jobsearch.api.eures.europa.eu/datamodel/v2/JobSearch",
        )
    )
    # API-ключ EURES — регистрация бесплатна: https://eures.europa.eu/en/find-a-job/eures-job-search-api
    source_eures_api_key: str | None = field(
        default_factory=lambda: os.getenv("SOURCE_EURES_API_KEY")
    )

    # --- Remotive ---
    source_remotive_enabled: bool = field(
        default_factory=lambda: _env_bool("SOURCE_REMOTIVE_ENABLED", False)
    )
    source_remotive_base_url: str = field(
        default_factory=lambda: os.getenv(
            "SOURCE_REMOTIVE_BASE_URL",
            "https://remotive.com/api/remote-jobs",
        )
    )

    # --- Adzuna ---
    # Ключи берём без префикса SOURCE_, как задал пользователь в .env
    source_adzuna_enabled: bool = field(
        default_factory=lambda: _env_bool("SOURCE_ADZUNA_ENABLED", True)
    )
    source_adzuna_base_url: str = field(
        default_factory=lambda: os.getenv(
            "SOURCE_ADZUNA_BASE_URL",
            "https://api.adzuna.com/v1/api/jobs/de/search",
        )
    )
    adzuna_app_id: str | None = field(default_factory=lambda: os.getenv("ADZUNA_APP_ID"))
    adzuna_app_key: str | None = field(default_factory=lambda: os.getenv("ADZUNA_APP_KEY"))

    # --- Jooble ---
    source_jooble_enabled: bool = field(default_factory=lambda: _env_bool("SOURCE_JOOBLE_ENABLED", True))
    source_jooble_base_url: str = field(
        default_factory=lambda: os.getenv("SOURCE_JOOBLE_BASE_URL", "https://jooble.org/api")
    )
    jooble_api_key: str | None = field(default_factory=lambda: os.getenv("JOOBLE_API_KEY"))

    # --- RemoteJobs.org ---
    source_remotejobs_enabled: bool = field(default_factory=lambda: _env_bool("SOURCE_REMOTEJOBS_ENABLED", True))
    source_remotejobs_base_url: str = field(
        default_factory=lambda: os.getenv("SOURCE_REMOTEJOBS_BASE_URL", "https://remotejobs.org/api/v1/jobs")
    )

    # --- Arbeitnow ---
    source_arbeitnow_enabled: bool = field(default_factory=lambda: _env_bool("SOURCE_ARBEITNOW_ENABLED", True))
    source_arbeitnow_base_url: str = field(
        default_factory=lambda: os.getenv("SOURCE_ARBEITNOW_BASE_URL", "https://www.arbeitnow.com/api/job-board-api")
    )

    # --- Greenhouse / Lever company job boards ---
    source_greenhouse_enabled: bool = field(default_factory=lambda: _env_bool("SOURCE_GREENHOUSE_ENABLED", True))
    source_greenhouse_base_url: str = field(
        default_factory=lambda: os.getenv("SOURCE_GREENHOUSE_BASE_URL", "https://boards-api.greenhouse.io/v1/boards")
    )
    source_greenhouse_board_tokens: str = field(default_factory=lambda: os.getenv("SOURCE_GREENHOUSE_BOARD_TOKENS", ""))
    source_lever_enabled: bool = field(default_factory=lambda: _env_bool("SOURCE_LEVER_ENABLED", True))
    source_lever_base_url: str = field(
        default_factory=lambda: os.getenv("SOURCE_LEVER_BASE_URL", "https://api.lever.co/v0/postings")
    )
    source_lever_company_slugs: str = field(default_factory=lambda: os.getenv("SOURCE_LEVER_COMPANY_SLUGS", ""))

    # --- HeadHunter ---
    source_hh_enabled: bool = field(default_factory=lambda: _env_bool("SOURCE_HH_ENABLED", True))
    source_hh_base_url: str = field(
        default_factory=lambda: os.getenv("SOURCE_HH_BASE_URL", "https://api.hh.ru")
    )
    source_hh_country_names: str = field(
        default_factory=lambda: os.getenv(
            "SOURCE_HH_COUNTRY_NAMES",
            "Kazakhstan,Kyrgyzstan,Uzbekistan,Georgia,Moldova",
        )
    )

    # --- DOU / Djinni RSS ---
    source_dou_enabled: bool = field(default_factory=lambda: _env_bool("SOURCE_DOU_ENABLED", True))
    source_dou_feed_url: str = field(
        default_factory=lambda: os.getenv("SOURCE_DOU_FEED_URL", "https://jobs.dou.ua/vacancies/feeds/")
    )
    source_djinni_enabled: bool = field(default_factory=lambda: _env_bool("SOURCE_DJINNI_ENABLED", True))
    source_djinni_feed_url: str = field(
        default_factory=lambda: os.getenv("SOURCE_DJINNI_FEED_URL", "https://djinni.co/jobs/rss/")
    )

    # --- Gmail ---
    gmail_account_email: str | None = field(default_factory=lambda: os.getenv("GMAIL_ACCOUNT_EMAIL"))
    gmail_client_id: str | None = field(default_factory=lambda: os.getenv("GMAIL_CLIENT_ID"))
    gmail_client_secret: str | None = field(default_factory=lambda: os.getenv("GMAIL_CLIENT_SECRET"))
    gmail_refresh_token: str | None = field(default_factory=lambda: os.getenv("GMAIL_REFRESH_TOKEN"))

    # --- Telegram ---
    telegram_bot_token: str | None = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN"))
    telegram_chat_id: str | None = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID"))

    # --- SMTP notifications ---
    notification_smtp_host: str | None = field(default_factory=lambda: os.getenv("NOTIFICATION_SMTP_HOST"))
    notification_smtp_port: int = field(
        default_factory=lambda: int(os.getenv("NOTIFICATION_SMTP_PORT", "587"))
    )
    notification_smtp_user: str | None = field(default_factory=lambda: os.getenv("NOTIFICATION_SMTP_USER"))
    notification_smtp_password: str | None = field(default_factory=lambda: os.getenv("NOTIFICATION_SMTP_PASSWORD"))
    notification_email_from: str | None = field(default_factory=lambda: os.getenv("NOTIFICATION_EMAIL_FROM"))
    notification_email_to: str | None = field(default_factory=lambda: os.getenv("NOTIFICATION_EMAIL_TO"))

    # --- LLM ---
    openai_api_key: str | None = field(default_factory=lambda: os.getenv("OPENAI_API_KEY"))
    openai_model: str = field(
        default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    )
    llm_timeout_seconds: float = field(
        default_factory=lambda: _env_float("OPENAI_TIMEOUT_SECONDS", _env_float("LLM_TIMEOUT_SECONDS", 45.0))
    )

    @property
    def package_root(self) -> Path:
        return Path(__file__).resolve().parent.parent

    @property
    def templates_dir(self) -> Path:
        return self.package_root / "templates"

    @property
    def static_dir(self) -> Path:
        return self.package_root / "static"
