"""Tests for parser utilities."""

from .parse import RelativeImportKey


class TestRelativeImportKey:
    def test_roundtrip_simple(self):
        key = RelativeImportKey(level=1, line=10, col=0, partial="")
        assert RelativeImportKey.decode(key.encode()) == key

    def test_roundtrip_with_partial(self):
        key = RelativeImportKey(level=2, line=5, col=4, partial="utils")
        assert RelativeImportKey.decode(key.encode()) == key

    def test_roundtrip_dotted_partial(self):
        key = RelativeImportKey(level=1, line=99, col=12, partial="pkg.sub")
        assert RelativeImportKey.decode(key.encode()) == key

    def test_is_placeholder_positive(self):
        key = RelativeImportKey(level=1, line=1, col=0, partial="mod")
        assert RelativeImportKey.is_placeholder(key.encode())

    def test_is_placeholder_negative(self):
        assert not RelativeImportKey.is_placeholder("mymodule")
        assert not RelativeImportKey.is_placeholder("__init__")
        assert not RelativeImportKey.is_placeholder("")

    def test_prefix_cannot_collide_with_identifiers(self):
        encoded = RelativeImportKey(level=1, line=1, col=0, partial="").encode()
        assert "\x00" in encoded
