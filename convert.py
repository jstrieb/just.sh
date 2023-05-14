import argparse
from collections import defaultdict
import hashlib
import logging
import os
import stat
import sys
from typing import IO, Dict, List, Tuple, TypeVar, Union

from parse import (
    Assignment,
    Export,
    ExpressionType,
    Setting,
    parse as justfile_parse,
)


#########################################################################################
# Global Variables and Types                                                            #
#########################################################################################


VERSION = "0.0.1"

T = TypeVar("T")


#########################################################################################
# Utility Functions                                                                     #
#########################################################################################


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


#########################################################################################
# POSIX sh Implementations of just Functions                                            #
#########################################################################################

just_functions = {
    # TODO: add more
    "os": r"""os() {
  case "$(uname -s | tr '[:upper:]' '[:lower:]')" in
  *darwin*)
    echo "macos"
    ;;
  *linux*)
    echo "linux"
    ;;
  *windows*|*msys*)
    echo "windows"
    ;;
  *)
    echo "unknown"
    ;;
  esac
}
""",
    "os_family": r"""os_family() {
  case "$(uname -s | tr '[:upper:]' '[:lower:]')" in
  *windows*|*msys*)
    echo "windows"
    ;;
  *)
    echo "unix"
    ;;
  esac
}
""",
    "arch": r"""arch() {
  # Shoutout to: https://stackoverflow.com/a/45125525
  case "$(uname -m | tr '[:upper:]' '[:lower:]')" in
  *aarch64*|*armv[8-9]*)
    echo "aarch64"
    ;;
  *aarch32*|*arm*)
    echo "arm"
    ;;
  *mips*)
    echo "mips"
    ;;
  *powerpc64*|*ppc64*)
    echo "powerpc64"
    ;;
  *powerpc*|*ppc*)
    echo "powerpc"
    ;;
  *s390*)
    echo "s390x"
    ;;
  *sparc*)
    echo "sparc"
    ;;
  *86_64*)
    echo "x86_64"
    ;;
  *86*)
    echo "x86"
    ;;
  *)
    echo "unix"
    ;;
  esac
}
""",
    "env_var_or_default": r"""env_var_or_default() {
  VARSTR="$(
    sh -c 'set -u; echo "${'"${1}"'}"' 2> /dev/null \
      || echo "${1}=${2}"
  )"
  echo "${VARSTR}" \
    | sed 's/^[^=][^=]*=\(.*\)$/\1/'
}
""",
    "env_var": r"""env_var() {
  sh -c 'set -u; echo "${'"${1}"'}"' 2> /dev/null || (
    echo_error "Call to function "'`env_var`'" failed: environment variable "'`'"${1}"'`'" not present"
    exit 1
  ) || exit "${?}"
}
""",
    "invocation_directory": r"""invocation_directory() {
  realpath "${INVOCATION_DIRECTORY}"
}
""",
    "invocation_directory_native": r"""invocation_directory_native() {
  realpath "${INVOCATION_DIRECTORY}"
}
""",
    "just_executable": r"""just_executable() {
  realpath "${0}"
}
""",
    "justfile": r"""justfile() {
  realpath "${0}"
}
""",
    "justfile_directory": r"""justfile_directory() {
  realpath "$(dirname "${0}")"
}
""",
    "sha256_file": r"""sha256_file() {
  if type sha256sum > /dev/null; then
    sha256sum --binary "${1}" | cut -d ' ' -f 1
  elif type python3 > /dev/null 2>&1; then
    python3 -c 'from hashlib import sha256; import sys; print(sha256(sys.stdin.buffer.read()).hexdigest())' \
      < "${1}"
  else
    echo_error "No sha256sum binary found"
    exit 1
  fi
}
""",
    "sha256": r"""sha256() {
  if type sha256sum > /dev/null; then
    printf "%s" "${1}" | sha256sum --binary | cut -d ' ' -f 1
  elif type python3 > /dev/null 2>&1; then
    printf "%s" "${1}" | \
      python3 -c 'from hashlib import sha256; import sys; print(sha256(sys.stdin.buffer.read()).hexdigest())'
  else
    echo_error "No sha256sum binary found"
    exit 1
  fi
}
""",
    # TODO: Confirm that sketchy UUID generation in bash is valid
    "uuid": r"""random_hex_bytes() {
  RANDOM_SOURCE="/dev/urandom"
  if ! [ -e "${RANDOM_SOURCE}" ]; then
    RANDOM_SOURCE="/dev/random"
    if [ -e "${RANDOM_SOURCE}" ]; then
      echo "${YELLOW}warning${NOCOLOR}: only pseudo-randomness available" >&2
    else
      echo_error "No randomness available"
      exit 1
    fi
  fi
  head -c "${1}" "${RANDOM_SOURCE}" \
    | od -t x1 \
    | head -n -1 \
    | cut -d ' ' -f 2- \
    | tr -d ' \n'
}

uuid() {
  (
    if [ -e /proc/sys/kernel/random/uuid ]; then
      cat /proc/sys/kernel/random/uuid
    elif type uuidgen > /dev/null 2>&1; then
      uuidgen
    elif type python3 > /dev/null 2>&1; then
      python3 -c 'import uuid; print(uuid.uuid4())'
    elif type python2 > /dev/null 2>&1; then
      python2 -c 'import uuid; print uuid.uuid4()'
    else
      VARIANT_BYTE="$(random_hex_bytes 1)"
      while ! echo "${VARIANT_BYTE}" | grep '^[89ab].$' > /dev/null; do
        VARIANT_BYTE="$(random_hex_bytes 1)"
      done
      MATCH='^\(........\)\(....\).\(...\)..\(..\)\(............\)$'
      NEW_PATTERN='\1-\2-4\3-'"${VARIANT_BYTE}"'\4-\5\n'
      random_hex_bytes 16 \
        | sed "s/${MATCH}/${NEW_PATTERN}/"
    fi 
  ) | tr '[:upper:]' '[:lower:]'
}
""",
    # TODO: Pass ${LINENO} from caller -- will likely need to modify AST
    "error": r"""error() {
  echo_error "Call to function "'`error`'" failed: ${*:-}"
  exit 1
}
""",
    "path_exists": r"""path_exists() {
  test -e "${1}" && echo "true" || echo "false"
}
""",
    "join": r"""join() {
  # No special Windows support means no support for Windows path separators
  printf "%s/" "${@}" | sed 's:/$::'
}
""",
    "quote": r"""quote() {
  printf "'"
  printf "%s" "${1}" | sed "s/'/'\\\\''/g"
  printf "'"
}
""",
    "uppercase": r"""uppercase() {
  echo "${1}" | tr '[:lower:]' '[:upper:]'
}
""",
    "lowercase": r"""lowercase() {
  echo "${1}" | tr '[:upper:]' '[:lower:]'
}
""",
}


def get_function(name: str) -> str:
    if name in just_functions:
        return just_functions[name]
    raise NotImplementedError()


#########################################################################################
# Helper Classes and Functions                                                          #
#########################################################################################


class CompilerState:
    def __init__(self, parsed) -> None:
        self.parsed = parsed
        self.internal_names: Dict[str, str] = dict()
        self.recipes: List[str] = []
        self.private_recipes: List[str] = []
        self.platform_specific_recipes: Dict[str, Dict[str, str]] = defaultdict(dict)
        self.comments: Dict[str, str] = dict()
        self.parameters: Dict[str, List[str]] = dict()
        self.aliases: Dict[str, List[str]] = defaultdict(list)

        self.settings: Dict[str, Union[bool, None, List[str]]] = self.process_settings()
        self.variables: Dict[str, ExpressionType]
        self.exports: List[str]
        self.variables, self.exports = self.process_variables()
        self.functions: Dict[str, str] = self.process_non_portable_recipes()
        # process_used_functions()

    def process_settings(self) -> Dict[str, Union[bool, None, List[str]]]:
        """
        Walk the AST looking for settings, update the internal state, and
        check for errors.
        """
        settings = dict()
        for item in self.parsed:
            if isinstance(item.item, Setting):
                setting = item.item
                if setting.setting in settings:
                    raise RuntimeError(
                        f"Setting {setting.setting} has already been set"
                    )
                settings[setting.setting] = setting.value

        if settings.get("fallback"):
            # No effect on transpiled shell scripts
            pass

        if settings.get("windows-shell") or settings.get("windows-powershell"):
            raise NotImplementedError("Windows not yet supported")

        return settings

    def process_variables(self) -> Tuple[Dict[str, ExpressionType], List[str]]:
        """
        Walk the AST looking for variables and exports.
        """
        variables = dict()
        exports = list()
        for item in self.parsed:
            if isinstance(item.item, Assignment):
                assignment = item.item
                variables[assignment.name] = assignment.value
            elif isinstance(item.item, Export):
                export = item.item
                assignment = export.assignment
                exports.append(assignment.name)
                variables[assignment.name] = assignment.value
        return variables, exports

    def process_non_portable_recipes(self) -> Dict[str, str]:
        """
        Walk the AST and update the compiler's function mapping to include `os`
        or `os_family` in the generated script for the platform check to use
        at runtime.
        """
        functions = dict()
        for item in self.parsed:
            if item.attributes.names and {"windows", "macos", "linux"} & set(
                item.attributes.names
            ):
                functions["os"] = get_function("os")
            elif item.attributes.names and "unix" in item.attributes.names:
                functions["os_family"] = get_function("os_family")
        return functions


#########################################################################################
# Main Function                                                                         #
#########################################################################################


def compile(justfile: str, f: IO, outfile_path: str, verbose: bool) -> None:
    compiler_state = CompilerState(justfile_parse(justfile, verbose=verbose))


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
