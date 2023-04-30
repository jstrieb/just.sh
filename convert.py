import argparse
import hashlib
import logging
import os
import stat
import sys
from typing import IO, TypeVar


###############################################################################
# Global Variables and Types                                                  #
###############################################################################


VERSION = "0.0.1"

T = TypeVar("T")


###############################################################################
# Utility Functions                                                           #
###############################################################################


def quote_string(instring: str, quote: str = "'") -> str:
    if quote != "'" and quote != '"':
        raise ValueError("Expecting single or double quotes!")
    return (
        quote
        + (
            instring.replace("'", "'\"'\"'")
            if quote == "'"
            else instring.replace('"', '"\'"\'"')
        )
        + quote
    )


def pad_line(line, terminator=" #", line_length=89, ignore_overflow=False):
    if len(line) > line_length and not ignore_overflow:
        raise ValueError(f"Line has length {len(line)} > {line_length}:\n{line}")
    return line + " " * (line_length - len(line) - len(terminator)) + terminator


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).digest().hex()


def identity(x: T, *args, **kwargs) -> T:
    return x


###############################################################################
# Main Function                                                               #
###############################################################################


def compile(justfile: str, f: IO, outfile_path: str, verbose: bool) -> None:
    pass


def main(justfile_path, outfile_path, verbose=False):
    if justfile_path is None:
        for filename in ["justfile", ".justfile", "Justfile", ".Justfile"]:
            if os.path.isfile(filename):
                justfile_path = filename
                break
    if justfile_path is None or justfile_path == "-":
        justfile_data = sys.stdin.read()
    else:
        with open(justfile_path, "r") as f:
            justfile_data = f.read()

    if outfile_path == "-":
        compile(justfile_data, sys.stdout, "just.sh", verbose)
    else:
        with open(outfile_path, "w") as f:
            compile(justfile_data, f, outfile_path, verbose)
        os.chmod(outfile_path, os.stat(outfile_path).st_mode | stat.S_IEXEC)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compile a Justfile to a shell script")
    parser.add_argument("-i", "--infile", action="store", help="Input Justfile path")
    parser.add_argument(
        "-o",
        "--outfile",
        action="store",
        default="just.sh",
        help="Output shell script path",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Verbose parser output"
    )
    parser.add_argument("--version", action="store_true", help="Print version string")
    parsed_args = parser.parse_args()

    if parsed_args.version:
        print(f"just.sh – Justfile to POSIX shell script compiler (version {VERSION})")

    logging.basicConfig(format="%(levelname)s: %(message)s")

    main(parsed_args.infile, parsed_args.outfile, verbose=parsed_args.verbose)
