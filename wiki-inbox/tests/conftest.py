from pathlib import Path

import pytest

from wiki_inbox.config import Settings
from wiki_inbox.server import Inbox

LLM_WIKI_CONFIG = """\
[global]
default_wiki = "main"

[[wikis]]
name = "main"
path = "{main}"

[[wikis]]
name = "other"
path = "{other}"
description = "second wiki"

[serve]
http_port = 8080
http_allowed_hosts = ["localhost"]
"""


def make_wiki(root: Path) -> Path:
    for d in ("inbox", "raw", "wiki", "schemas"):
        (root / d).mkdir(parents=True, exist_ok=True)
    (root / "wiki.toml").write_text('name = "x"\n')
    return root


@pytest.fixture
def wikis(tmp_path: Path) -> dict[str, Path]:
    main = make_wiki(tmp_path / "wikis" / "main")
    other = make_wiki(tmp_path / "wikis" / "other")
    return {"main": main, "other": other}


@pytest.fixture
def config_file(tmp_path: Path, wikis: dict[str, Path]) -> Path:
    cfg = tmp_path / "config" / "config.toml"
    cfg.parent.mkdir()
    cfg.write_text(LLM_WIKI_CONFIG.format(main=wikis["main"], other=wikis["other"]))
    return cfg


@pytest.fixture
def settings(tmp_path: Path, config_file: Path) -> Settings:
    return Settings(
        llm_wiki_config=config_file,
        cache_dir=tmp_path / "cache",
        max_bytes=1024 * 1024,
        read_chunk=100,
    )


@pytest.fixture
def inbox(settings: Settings) -> Inbox:
    return Inbox(settings)


@pytest.fixture
def main_inbox(wikis: dict[str, Path]) -> Path:
    return wikis["main"] / "inbox"
