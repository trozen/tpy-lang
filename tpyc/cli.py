"""
TurboPython Compiler CLI

Usage:
    tpyc input.tp.py -o out/
"""

from __future__ import annotations
import argparse
import sys
from pathlib import Path

from .parse import Parser, ParseError
from .sema import SemanticAnalyzer, SemanticError
from .codegen_cpp import CodeGenerator


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="tpyc",
        description="TurboPython Compiler - compiles TurboPython to C++"
    )
    parser.add_argument("input", help="Input TurboPython source file (.tp.py)")
    parser.add_argument("-o", "--output", required=True, help="Output directory")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")

    args = parser.parse_args()

    input_path = Path(args.input)
    output_dir = Path(args.output)

    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}", file=sys.stderr)
        return 1

    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)

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
        codegen = CodeGenerator(analyzer)
        hpp_code, cpp_code = codegen.generate(module)

        # Write output files
        hpp_path = output_dir / "generated.hpp"
        cpp_path = output_dir / "generated.cpp"

        hpp_path.write_text(hpp_code)
        cpp_path.write_text(cpp_code)

        print(f"Generated: {hpp_path}")
        print(f"Generated: {cpp_path}")

        return 0

    except ParseError as e:
        print(f"Parse error: {e}", file=sys.stderr)
        return 1
    except SemanticError as e:
        print(f"Semantic error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Internal error: {e}", file=sys.stderr)
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
