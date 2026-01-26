"""
TurboPython Compiler CLI

Usage:
    tpyc input.tp.py              # Compile to C++ in __tpyc__/
    tpyc input.tp.py -o out/      # Compile to C++ in out/
    tpyc input.tp.py --build      # Compile to C++ and build binary
    tpyc input.tp.py --run        # Compile, build, and run
"""

from __future__ import annotations
import argparse
import subprocess
import sys
from pathlib import Path

from .parse import Parser, ParseError
from .sema import SemanticAnalyzer, SemanticError
from .codegen_cpp import CodeGenerator, CodeGenOptions


def get_runtime_dir() -> Path:
    """Get the path to the runtime directory."""
    # Runtime is at package_root/runtime/
    package_root = Path(__file__).parent.parent
    return package_root / "runtime"


def get_module_name(input_path: Path) -> str:
    """Get module name from source file (e.g., hello.tp.py -> hello)."""
    name = input_path.name
    # Strip .tp.py or .py suffix
    if name.endswith(".tp.py"):
        return name[:-6]
    elif name.endswith(".py"):
        return name[:-3]
    return name


def compile_file(input_path: str, output_dir: str, options: CodeGenOptions | None = None) -> tuple[Path, Path]:
    """Compile a TurboPython file to C++.

    Args:
        input_path: Path to the input .tp.py file
        output_dir: Root output directory (e.g., __tpyc__/)
        options: Code generation options (optional)

    Returns:
        Tuple of (hpp_path, cpp_path) for the generated files.
        Files are placed in {output_dir}/{module}.d/{module}.{hpp,cpp}
    """
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    module_name = get_module_name(input_path)

    # Create per-module subdirectory with .d suffix
    module_dir = output_dir / f"{module_name}.d"
    module_dir.mkdir(parents=True, exist_ok=True)

    source = input_path.read_text()

    # Parse
    p = Parser()
    module = p.parse(source)

    # Semantic analysis
    analyzer = SemanticAnalyzer()
    analyzer.analyze(module)

    # Code generation
    codegen = CodeGenerator(analyzer, options)
    hpp_code, cpp_code = codegen.generate(module, module_name)

    # Write output files with module name
    hpp_path = module_dir / f"{module_name}.hpp"
    cpp_path = module_dir / f"{module_name}.cpp"

    hpp_path.write_text(hpp_code)
    cpp_path.write_text(cpp_code)

    return hpp_path, cpp_path


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="tpyc",
        description="TurboPython Compiler - compiles TurboPython to C++"
    )
    parser.add_argument("input", help="Input TurboPython source file (.tp.py)")
    parser.add_argument("-o", "--output", help="Output directory (default: __tpyc__/ next to source)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    parser.add_argument("--build", action="store_true", help="Compile C++ to binary after generating")
    parser.add_argument("--run", action="store_true", help="Build and run the program")
    parser.add_argument("--emit-source", action="store_true", help="Embed Python source as comments in generated C++")

    args = parser.parse_args()

    input_path = Path(args.input).resolve()

    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}", file=sys.stderr)
        return 1

    # Determine output directory
    if args.output:
        output_dir = Path(args.output)
    else:
        # Default: __tpyc__/ next to source file
        output_dir = input_path.parent / "__tpyc__"

    # Get module name for output paths
    module_name = get_module_name(input_path)

    # Create per-module subdirectory with .d suffix for C++ sources
    module_dir = output_dir / f"{module_name}.d"
    module_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Read source
        source = input_path.read_text()

        if args.verbose:
            print(f"Compiling {input_path}...")

        # Parse
        p = Parser()
        module = p.parse(source)

        if args.verbose:
            print(f"  Parsed {len(module.records)} records, {len(module.functions)} functions")

        # Semantic analysis
        analyzer = SemanticAnalyzer()
        analyzer.analyze(module)

        if args.verbose:
            print("  Semantic analysis passed")

        # Code generation
        options = CodeGenOptions(emit_source_comments=args.emit_source)
        codegen = CodeGenerator(analyzer, options)
        hpp_code, cpp_code = codegen.generate(module, module_name)

        # Write output files with module name in per-module directory
        hpp_path = module_dir / f"{module_name}.hpp"
        cpp_path = module_dir / f"{module_name}.cpp"

        hpp_path.write_text(hpp_code)
        cpp_path.write_text(cpp_code)

        if args.verbose or not (args.build or args.run):
            print(f"Generated: {hpp_path}")
            print(f"Generated: {cpp_path}")

        # Build if requested
        if args.build or args.run:
            runtime_dir = get_runtime_dir()
            # Binary goes at output root (no conflict with .d dir)
            binary_path = output_dir / module_name

            if args.verbose:
                print(f"Building {binary_path}...")

            compile_cmd = [
                "g++", "-std=c++20",
                "-I", str(runtime_dir),
                "-o", str(binary_path),
                str(cpp_path),
                "-lgmp"
            ]

            result = subprocess.run(compile_cmd, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"C++ compilation failed:", file=sys.stderr)
                print(result.stderr, file=sys.stderr)
                return 1

            if args.verbose or not args.run:
                print(f"Built: {binary_path}")

            # Run if requested
            if args.run:
                if args.verbose:
                    print(f"Running {binary_path}...")
                    print("---")

                result = subprocess.run([str(binary_path)])
                return result.returncode

        return 0

    except ParseError as e:
        print(f"Parse error: {e}", file=sys.stderr)
        return 1
    except SemanticError as e:
        print(f"Semantic error: {e}", file=sys.stderr)
        return 1
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Internal error: {e}", file=sys.stderr)
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
