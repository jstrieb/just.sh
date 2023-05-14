import argparse
from collections import defaultdict
import hashlib
import logging
import os
import stat
import sys
from typing import IO, Any, Callable, Dict, List, Optional, Tuple, TypeVar, Union

from parse import (
    Alias,
    Assignment,
    Backtick,
    Comment,
    Conditional,
    Div,
    Eq,
    Export,
    ExpressionType,
    Function,
    Neq,
    Recipe,
    RegexEq,
    Setting,
    Sum,
    Variable,
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
        self.platform_specific_recipes: Dict[str, Dict[str, str]] = defaultdict(dict)
        self.parameters: Dict[str, List[str]] = dict()
        self.aliases: Dict[str, List[str]] = defaultdict(list)

        self.internal_names: Dict[str, str] = dict()
        self.settings: Dict[str, Union[bool, None, List[str]]] = self.process_settings()
        self.variables: Dict[str, ExpressionType]
        self.exports: List[str]
        self.variables, self.exports = self.process_variables()
        self.functions: Dict[str, str] = self.process_used_functions()
        self.private_recipes: List[str] = self.process_private_recipes()
        self.recipes: List[str] = self.list_all_recipes(
            self.settings.get("allow-duplicate-recipes")
        )
        self.docstrings: Dict[str, str] = self.process_docstrings()

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

    def process_used_functions(self) -> Dict[str, str]:
        """
        Walk the parsed structure and find all functions required for variable
        assignments.

        Also walk the AST and update the compiler's function mapping to include
        `os` or `os_family` in the generated script for the platform check to
        use at runtime.
        """
        functions = dict()

        for item in self.parsed:
            if item.attributes.names and {"windows", "macos", "linux"} & set(
                item.attributes.names
            ):
                functions["os"] = get_function("os")
            elif item.attributes.names and "unix" in item.attributes.names:
                functions["os_family"] = get_function("os_family")

        # Closure over local `functions` variable
        def find_functions(ast_item) -> Any:
            """
            Recursively walk the AST and up `functions` with any function in the
            tree.
            """
            if isinstance(ast_item, Sum):
                return find_functions(ast_item.sum_1), find_functions(ast_item.sum_2)
            if isinstance(ast_item, Div):
                return find_functions(ast_item.div_1), find_functions(ast_item.div_2)
            if isinstance(ast_item, Backtick):
                functions["backtick_error"] = (
                    "backtick_error() {\n"
                    '  STATUS="${?}"\n'
                    '  echo_error "Backtick failed with exit code ${STATUS}"\n'
                    '  exit "${STATUS}"\n'
                    "}\n"
                )
                return
            if isinstance(ast_item, Conditional):
                conditional_function_name = self.clean_name(
                    f"if_{sha256(str(ast_item))[:16]}"
                )
                if isinstance(ast_item.if_condition, RegexEq):
                    functions[conditional_function_name] = (
                        f"{conditional_function_name}() {{\n"
                        f"  if "
                        f"echo {self.evaluate(ast_item.if_condition.left)} | "
                        f"grep -E {self.evaluate(ast_item.if_condition.right)} > /dev/null; "
                        f"then \n"
                        f'    THEN_EXPR={self.evaluate(ast_item.then)} || exit "${{?}}"\n'
                        f'    echo "${{THEN_EXPR}}"\n'
                        f"  else\n"
                        f'    ELSE_EXPR={self.evaluate(ast_item.else_then)} || exit "${{?}}"\n'
                        f'    echo "${{ELSE_EXPR}}"\n'
                        f"  fi\n"
                        f"}}\n"
                    )
                    return
                if isinstance(ast_item.if_condition, Eq):
                    comparison = "="
                elif isinstance(ast_item.if_condition, Neq):
                    comparison = "!="
                else:
                    raise ValueError(f"Bad if condition {str(ast_item.if_condition)}.")
                functions[conditional_function_name] = (
                    f"{conditional_function_name}() {{\n"
                    f"  if [ "
                    f"{self.evaluate(ast_item.if_condition.left)} "
                    f"{comparison} "
                    f"{self.evaluate(ast_item.if_condition.right)} ]; then \n"
                    f'    THEN_EXPR={self.evaluate(ast_item.then)} || exit "${{?}}"1\n'
                    f'    echo "${{THEN_EXPR}}"\n'
                    f"  else\n"
                    f'    ELSE_EXPR={self.evaluate(ast_item.else_then)} || exit "${{?}}"\n'
                    f'    echo "${{ELSE_EXPR}}"\n'
                    f"  fi\n"
                    f"}}\n"
                )
                return
            if isinstance(ast_item, Function):
                conditional_function_name = ast_item.name
                functions[conditional_function_name] = get_function(
                    conditional_function_name
                )
                return (
                    (find_functions(argument) for argument in ast_item.arguments)
                    if ast_item.arguments
                    else None
                )
            raise ValueError(f"Unexpected expression type {str(ast_item)}")

        for item in self.parsed:
            find_functions(item)

        return functions

    def process_private_recipes(self) -> List[str]:
        private_recipes = []
        for item in self.parsed:
            if isinstance(item.item, Recipe) or isinstance(item.item, Alias):
                if "private" in item.attributes.names or item.item.name.startswith("_"):
                    private_recipes.append(item.item.name)
        return private_recipes

    def list_all_recipes(self, allow_duplicate_recipes) -> List[str]:
        recipes = []
        for item in self.parsed:
            if isinstance(item.item, Recipe):
                # Filter out platform-specific attributes (e.g., drop "private")
                platform_attributes = {"windows", "macos", "linux", "unix"} & set(
                    item.attributes.names
                )
                if (
                    item.item.name in recipes
                    and not allow_duplicate_recipes
                    and not platform_attributes
                ):
                    raise ValueError("No duplicate recipes!")

                recipes.append(item.item.name)
        return recipes

    def process_docstrings(self) -> Dict[str, str]:
        docstrings = dict()
        for item_index, item in enumerate(self.parsed):
            if isinstance(item.item, Recipe):
                recipe = item.item
                if "private" not in item.attributes and not recipe.name.startswith("_"):
                    # Store docstrings, but only for non-private recipes
                    previous = (
                        self.parsed[item_index - 1].item if item_index > 0 else None
                    )
                    if isinstance(previous, Comment):
                        docstrings[recipe.name] = previous.comment
        return docstrings

    def clean_name(self, to_clean: str, prefix: str = "") -> str:
        """
        Shell names with dashes are not portable. They're necessary to keep
        around in some places for consistency, but internally, use underscored
        versions.
        """
        to_clean = prefix + to_clean
        cleaned = to_clean.replace("-", "_")
        # Handle the rare case when the Justfile contains names like "some-name"
        # AND "some_name"
        num = 2
        while (
            cleaned in self.internal_names and self.internal_names[cleaned] != to_clean
        ):
            cleaned = f'{to_clean.replace("-", "_")}_{num}'
            num += 1
        self.internal_names[cleaned] = to_clean
        return cleaned

    def clean_var_name(self, to_clean: str) -> str:
        return self.clean_name(to_clean, prefix="VAR_")

    def clean_fun_name(self, to_clean: str) -> str:
        return self.clean_name(to_clean, prefix="FUN_")

    def evaluate(self, to_eval: ExpressionType, quote: bool = True) -> str:
        """
        Return a string that can be used for variable interpolations in the
        generated script.
        """
        # mypy HATES him because of this one weird trick!
        # https://github.com/python/mypy/issues/10740
        quote_function: Callable[..., str]
        if quote:
            quote_function = quote_string
        else:
            quote_function = identity
        if isinstance(to_eval, str):
            return quote_function(to_eval)
        if isinstance(to_eval, Variable):
            return quote_function(
                f"${{{self.clean_var_name(to_eval.name)}}}", quote='"'
            )
        if isinstance(to_eval, Sum):
            return self.evaluate(to_eval.sum_1, quote=quote) + self.evaluate(
                to_eval.sum_2, quote=quote
            )
        if isinstance(to_eval, Div):
            return (
                self.evaluate(to_eval.div_1, quote=quote)
                + "'/'"
                + self.evaluate(to_eval.div_2, quote=quote)
            )
        if isinstance(to_eval, Backtick):
            return (
                f'"$('
                f'env "${{DEFAULT_SHELL}}" ${{DEFAULT_SHELL_ARGS}} '
                f"{quote_function(to_eval.command)}"
                f" || backtick_error"
                f')"'
            )
        # For the following two cases, the actual content of the functions is
        # set in internal state in `process_used_functions`
        if isinstance(to_eval, Conditional):
            conditional_function_name = self.clean_name(
                f"if_{sha256(str(to_eval))[:16]}"
            )
            return quote_function(f"$({conditional_function_name})", quote='"')
        if isinstance(to_eval, Function):
            conditional_function_name = to_eval.name
            return (
                f'"$({self.clean_name(conditional_function_name)}'
                + (
                    f" {' '.join([self.evaluate(argument) for argument in to_eval.arguments])}"
                    if to_eval.arguments
                    else ""
                )
                + ')"'
            )
        raise ValueError(f"Unexpected expression type {str(to_eval)}")


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
