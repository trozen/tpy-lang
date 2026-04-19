"""Smoke tests for install_docs."""

from pathlib import Path

from .install_docs import install_agent_docs, agents_md_snippet


def test_install_creates_both_files(tmp_path: Path) -> None:
    target = tmp_path / "mydocs"
    written = install_agent_docs(target)

    names = {p.name for p in written}
    assert names == {"TPY_FOR_AGENTS.md", "TPY_LANGUAGE_FEATURES.md", "TPY_STDLIB_ROADMAP.md"}
    for p in written:
        assert p.is_file()
        assert p.read_text().startswith("#")


def test_install_creates_missing_dir(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b" / "c"
    install_agent_docs(target)
    assert target.is_dir()


def test_install_overwrites(tmp_path: Path) -> None:
    target = tmp_path / "docs"
    target.mkdir()
    stale = target / "TPY_FOR_AGENTS.md"
    stale.write_text("old content")

    install_agent_docs(target)

    assert stale.read_text() != "old content"


def test_install_rejects_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "not_a_dir"
    target.write_text("I am a regular file")

    try:
        install_agent_docs(target)
    except NotADirectoryError as e:
        assert "not a directory" in str(e)
    else:
        raise AssertionError("expected NotADirectoryError")


def test_snippet_uses_provided_path() -> None:
    snippet = agents_md_snippet(Path("mydocs"))
    assert "mydocs/TPY_FOR_AGENTS.md" in snippet
    assert "mydocs/TPY_LANGUAGE_FEATURES.md" in snippet
    assert "mydocs/TPY_STDLIB_ROADMAP.md" in snippet
    assert "tpyc --install-agent-docs mydocs" in snippet


def test_snippet_strips_trailing_slash() -> None:
    snippet = agents_md_snippet(Path("docs/"))
    assert "docs/TPY_FOR_AGENTS.md" in snippet
    assert "docs//TPY_FOR_AGENTS.md" not in snippet
