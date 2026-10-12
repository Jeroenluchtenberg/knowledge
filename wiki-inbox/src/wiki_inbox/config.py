"""Settings and wiki-name → repository path resolution."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class WikiError(ValueError):
    """Raised when a wiki name cannot be resolved."""


class Settings(BaseSettings):
    """Runtime settings, read from ``INBOX_*`` environment variables."""

    model_config = SettingsConfigDict(env_prefix="INBOX_", extra="ignore")

    llm_wiki_config: Path = Path("/config/config.toml")
    """llm-wiki global config, read-only, used to map wiki name → repo path."""

    wikis: str | None = None
    """Optional override/fallback: ``name=/path,name2=/path2``."""

    max_bytes: int = Field(default=25 * 1024 * 1024, gt=0)
    """Maximum file size for add, fetch and read."""

    read_chunk: int = Field(default=40_000, gt=0)
    """Default ``limit`` (characters) for ``inbox_read``."""

    read_only: bool = False
    """Only register ``inbox_list`` and ``inbox_read``."""

    fetch_allow_private: bool = False
    """Allow ``inbox_add_url`` to reach private/loopback/link-local addresses."""

    fetch_timeout: float = Field(default=30.0, gt=0)
    """Total timeout in seconds for one ``inbox_add_url`` fetch (per hop)."""

    cache_dir: Path = Path("/tmp/wiki-inbox-cache")  # noqa: S108 — container-local cache
    """Where converted Markdown is cached."""

    host: str = "0.0.0.0"  # noqa: S104 — container listens on all interfaces
    port: int = 8081

    allowed_hosts: str = "localhost,127.0.0.1,wiki-inbox"
    """Comma-separated Host header allow-list (DNS rebinding guard), any port. ``*`` disables."""

    def allowed_host_list(self) -> list[str]:
        return [h.strip() for h in self.allowed_hosts.split(",") if h.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


@dataclass(frozen=True)
class Wiki:
    name: str
    root: Path

    @property
    def inbox(self) -> Path:
        return self.root / "inbox"

    @property
    def raw(self) -> Path:
        return self.root / "raw"


def _parse_override(spec: str | None) -> dict[str, Path]:
    wikis: dict[str, Path] = {}
    if not spec:
        return wikis
    for item in spec.split(","):
        item = item.strip()
        if not item:
            continue
        name, sep, path = item.partition("=")
        if not sep or not name.strip() or not path.strip():
            raise WikiError(f"invalid INBOX_WIKIS entry {item!r}; expected name=/path")
        wikis[name.strip()] = Path(path.strip())
    return wikis


def load_wikis(settings: Settings) -> tuple[dict[str, Path], str | None]:
    """Return ``({name: repo_path}, default_wiki)``.

    The llm-wiki config is re-read on every call so spaces created at runtime
    show up without a restart. ``INBOX_WIKIS`` entries override same-named ones.
    """
    wikis: dict[str, Path] = {}
    default: str | None = None
    cfg_path = settings.llm_wiki_config
    if cfg_path.is_file():
        with cfg_path.open("rb") as f:
            data = tomllib.load(f)
        for entry in data.get("wikis", []):
            name, path = entry.get("name"), entry.get("path")
            if isinstance(name, str) and isinstance(path, str) and name and path:
                wikis[name] = Path(path)
        default = data.get("global", {}).get("default_wiki") or None
    wikis.update(_parse_override(settings.wikis))
    if default not in wikis:
        default = next(iter(wikis)) if len(wikis) == 1 else None
    return wikis, default


def resolve_wiki(name: str | None, settings: Settings | None = None) -> Wiki:
    """Map a wiki *name* (never a path) to its repository root."""
    settings = settings or get_settings()
    wikis, default = load_wikis(settings)
    if not wikis:
        raise WikiError("no wikis are registered")
    if not name:
        if default is None:
            raise WikiError(f"no default wiki; pass wiki= one of {sorted(wikis)}")
        name = default
    if name not in wikis:
        raise WikiError(f"unknown wiki {name!r}; registered wikis: {sorted(wikis)}")
    return Wiki(name=name, root=wikis[name].resolve())
