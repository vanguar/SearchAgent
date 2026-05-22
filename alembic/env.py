from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Загружаем .env чтобы DATABASE_URL был доступен при запуске alembic напрямую
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from app.db.base import Base
import app.db.models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Если DATABASE_URL задан в окружении — переопределяем URL из alembic.ini,
# но только если его не установили программно (тесты задают его через config.set_main_option).
_env_db_url = os.environ.get("DATABASE_URL")
_cfg_db_url = config.get_main_option("sqlalchemy.url")
if _env_db_url and (not _cfg_db_url or _cfg_db_url == "driver://user:pass@localhost/dbname"):
    config.set_main_option("sqlalchemy.url", _env_db_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
