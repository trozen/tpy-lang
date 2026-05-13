"""Frontend plugin contract + loader.

See `docs/FRONTEND_PLUGIN_DESIGN.md` for the contract specification.

M1 scope: plugin discovery + WorkspaceContext + registry. Decorator
manifests are accepted but unused (no registry yet). Subprocess-helper
SubprocessPlugin is future work.
"""

from __future__ import annotations

import importlib
import importlib.util
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Iterable

from .diagnostics import Diagnostic, DiagnosticLevel
from .frontend_ir.nodes import API_VERSION, FrontendModule


@dataclass(frozen=True)
class WorkspaceContext:
    """Read-only view of the compile-time environment exposed to plugins.

    M1 carries only the fields hello-world needs: `options` (routed
    `--dsl-opt` values for this plugin), `no_stdlib` (mirror of the
    compiler flag), and `read_file` for opening the entry source.
    `find_files` and `resolve_module` from the design doc are deferred
    until a plugin needs them.
    """
    api_version: int
    entry_point: Path
    search_dirs: tuple[Path, ...]
    options: dict[str, str]
    no_stdlib: bool

    def read_file(self, path: Path) -> str:
        return Path(path).read_text()


@dataclass
class FrontendOutput:
    """Return value from `FrontendPlugin.parse()`."""
    module: FrontendModule
    diagnostics: list[Diagnostic] = field(default_factory=list)


class FrontendPluginError(Exception):
    """Fatal error raised by a plugin when it cannot produce IR.

    Carries one `Diagnostic`; the compiler catches it and reports the
    diagnostic as `PLUGIN_REPORTED`.
    """
    def __init__(self, diagnostic: Diagnostic):
        super().__init__(diagnostic.message)
        self.diagnostic = diagnostic


class FrontendPlugin(ABC):
    """Base class for frontend plugins.

    Subclasses set the ClassVar metadata and implement `parse()`.
    Plugin modules expose the subclass (not an instance) as the module
    attribute `PLUGIN`; the compiler instantiates it once per `tpyc`
    invocation with the routed options dict.
    """
    api_version: ClassVar[int] = API_VERSION
    name: ClassVar[str] = ""
    extensions: ClassVar[tuple[str, ...]] = ()
    decorator_manifest: ClassVar[tuple] = ()

    def __init__(self, options: dict[str, str]) -> None:
        self.options = options

    @abstractmethod
    def parse(self, ctx: WorkspaceContext,
              module_name: str, file_path: Path) -> FrontendOutput:
        ...


@dataclass
class FrontendRegistry:
    """Maps file extensions to plugin instances."""
    by_extension: dict[str, FrontendPlugin] = field(default_factory=dict)
    plugins: list[FrontendPlugin] = field(default_factory=list)

    def register(self, plugin: FrontendPlugin) -> None:
        for ext in plugin.extensions:
            if ext in self.by_extension:
                existing = self.by_extension[ext]
                raise FrontendPluginError(Diagnostic(
                    level=DiagnosticLevel.ERROR,
                    message=(f"extension {ext!r} claimed by both "
                             f"{existing.name!r} and {plugin.name!r}"),
                ))
            self.by_extension[ext] = plugin
        self.plugins.append(plugin)

    def for_path(self, path: Path) -> FrontendPlugin | None:
        return self.by_extension.get(path.suffix)

    def claims_extension(self, ext: str) -> bool:
        return ext in self.by_extension

    def all_extensions(self) -> frozenset[str]:
        return frozenset(self.by_extension.keys())


def load_plugin(spec: str, options: dict[str, str]) -> FrontendPlugin:
    """Load a plugin by file path or dotted module name.

    Steps follow `docs/FRONTEND_PLUGIN_DESIGN.md` "Loading and
    registration":
      1. If spec resolves to an existing .py file, import via spec_from_file_location.
      2. Else, treat spec as an importable module name.
      3. Fetch `PLUGIN`; validate it is a FrontendPlugin subclass.
      4. Validate api_version is supported.
      5. Instantiate with `options`.
    """
    spec_path = Path(spec)
    module = None
    if spec_path.suffix == ".py" and spec_path.exists():
        module_name = f"_tpyc_plugin_{spec_path.stem}"
        loader_spec = importlib.util.spec_from_file_location(module_name, spec_path)
        if loader_spec is None or loader_spec.loader is None:
            raise _plugin_load_error(
                f"cannot load plugin file: {spec}")
        module = importlib.util.module_from_spec(loader_spec)
        loader_spec.loader.exec_module(module)
    else:
        try:
            module = importlib.import_module(spec)
        except ImportError as e:
            raise _plugin_load_error(
                f"cannot import plugin module {spec!r}: {e}")

    plugin_cls = getattr(module, "PLUGIN", None)
    if plugin_cls is None:
        raise _plugin_load_error(
            f"plugin module {spec!r} has no PLUGIN attribute")
    if not (isinstance(plugin_cls, type) and issubclass(plugin_cls, FrontendPlugin)):
        raise _plugin_load_error(
            f"plugin module {spec!r}: PLUGIN must be a FrontendPlugin subclass, "
            f"got {plugin_cls!r}")
    if plugin_cls.api_version != API_VERSION:
        raise _plugin_load_error(
            f"plugin {plugin_cls.name!r} declares api_version="
            f"{plugin_cls.api_version}; compiler supports {API_VERSION}")
    return plugin_cls(options)


def route_dsl_opts(
    flags: Iterable[str], known_plugin_names: Iterable[str],
) -> dict[str, dict[str, str]]:
    """Split `--dsl-opt name.key=value` strings into per-plugin dicts.

    Returns `{plugin_name: {key: value, ...}}`. Raises FrontendPluginError
    when an option does not match `name.key=value` or `name` is not a
    registered plugin.
    """
    known = set(known_plugin_names)
    out: dict[str, dict[str, str]] = {n: {} for n in known}
    for raw in flags:
        if "=" not in raw or "." not in raw.split("=", 1)[0]:
            raise _plugin_load_error(
                f"--dsl-opt must be 'name.key=value'; got {raw!r}")
        head, value = raw.split("=", 1)
        name, key = head.split(".", 1)
        if name not in known:
            raise _plugin_load_error(
                f"--dsl-opt {raw!r}: no plugin named {name!r}")
        out[name][key] = value
    return out


def _plugin_load_error(msg: str) -> FrontendPluginError:
    return FrontendPluginError(Diagnostic(level=DiagnosticLevel.ERROR, message=msg))
