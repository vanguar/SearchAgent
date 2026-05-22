"""Source adapters exposed for PHASE 5+."""

from app.services.source_adapters.ba_adapter import BAAdapter
from app.services.source_adapters.djinni_rss_adapter import DjinniRssAdapter
from app.services.source_adapters.dou_rss_adapter import DouRssAdapter
from app.services.source_adapters.hh_adapter import HHAdapter
from app.services.source_adapters.registry import SourceAdapterRegistry

__all__ = ["BAAdapter", "DjinniRssAdapter", "DouRssAdapter", "HHAdapter", "SourceAdapterRegistry"]
