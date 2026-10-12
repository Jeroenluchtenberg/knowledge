from pathlib import Path

import pytest

from wiki_inbox.config import Settings, WikiError, load_wikis, resolve_wiki


def test_parses_llm_wiki_config(settings: Settings, wikis: dict[str, Path]) -> None:
    found, default = load_wikis(settings)
    assert found == {"main": wikis["main"], "other": wikis["other"]}
    assert default == "main"


def test_resolve_default_and_named(settings: Settings, wikis: dict[str, Path]) -> None:
    assert resolve_wiki(None, settings).root == wikis["main"].resolve()
    assert resolve_wiki("other", settings).name == "other"


def test_unknown_wiki_and_paths_rejected(settings: Settings) -> None:
    with pytest.raises(WikiError, match="unknown wiki"):
        resolve_wiki("nope", settings)
    with pytest.raises(WikiError, match="unknown wiki"):
        resolve_wiki("/etc", settings)


def test_config_reread_each_call(settings: Settings, config_file: Path, tmp_path: Path) -> None:
    new = tmp_path / "new"
    new.mkdir()
    config_file.write_text(
        config_file.read_text() + f'\n[[wikis]]\nname = "late"\npath = "{new}"\n'
    )
    assert resolve_wiki("late", settings).root == new.resolve()


def test_override_env(tmp_path: Path, settings: Settings) -> None:
    alt = tmp_path / "alt"
    alt.mkdir()
    s = settings.model_copy(update={"wikis": f"main={alt}, extra={alt}"})
    found, default = load_wikis(s)
    assert found["main"] == alt and found["extra"] == alt and default == "main"


def test_override_only_single_wiki_is_default(tmp_path: Path) -> None:
    s = Settings(llm_wiki_config=tmp_path / "missing.toml", wikis=f"solo={tmp_path}")
    assert resolve_wiki(None, s).name == "solo"


def test_no_default_with_many(tmp_path: Path) -> None:
    s = Settings(llm_wiki_config=tmp_path / "missing.toml", wikis=f"a={tmp_path},b={tmp_path}")
    with pytest.raises(WikiError, match="no default"):
        resolve_wiki(None, s)


def test_bad_override(tmp_path: Path) -> None:
    s = Settings(llm_wiki_config=tmp_path / "missing.toml", wikis="broken")
    with pytest.raises(WikiError):
        load_wikis(s)


def test_env_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INBOX_READ_ONLY", "true")
    monkeypatch.setenv("INBOX_MAX_BYTES", "123")
    s = Settings()
    assert s.read_only is True and s.max_bytes == 123
