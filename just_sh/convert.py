import argparse
import datetime
import hashlib
import logging
import os
import stat
import sys
from collections import defaultdict
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Set,
    Tuple,
    TypeVar,
    Union,
    cast,
)

try:
    from importlib.metadata import PackageNotFoundError, version
except ImportError:
    from importlib_metadata import PackageNotFoundError, version  # type: ignore

from .parse import (
    Alias,
    Assignment,
    Backtick,
    Comment,
    Conditional,
    Dependency,
    Div,
    Eq,
    Export,
    ExpressionType,
    Function,
    Interpolation,
    Item,
    Line,
    Neq,
    Parameter,
    Recipe,
    RegexEq,
    Setting,
    Sum,
    Variable,
    VarPlus,
    VarStar,
)
from .parse import (
    parse as justfile_parse,
)

########################################################################################
# Global Variables and Types                                                           #
########################################################################################


try:
    __version__ = version("just_sh")
except PackageNotFoundError:
    __version__ = "unknown"

T = TypeVar("T")

# Escape characters are not allowed in format strings
newline = "\n"


########################################################################################
# Utility Functions                                                                    #
########################################################################################


def quote_string(instring: str, quote: str = "'") -> str:
    if quote != "'" and quote != '"':
        raise ValueError("Expecting single or double quotes!")
    if quote == "'":
        replaced = instring.replace("'", "'\"'\"'")
    else:
        replaced = instring.replace('"', '"\'"\'"')
    return quote + replaced + quote


def pad_line(
    line: str,
    terminator: str = " #",
    line_length: int = 89,
    ignore_overflow: bool = False,
) -> str:
    if len(line) > line_length and not ignore_overflow:
        raise ValueError(f"Line has length {len(line)} > {line_length}:\n{line}")
    return line + " " * (line_length - len(line) - len(terminator)) + terminator


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).digest().hex()


def identity(x: T, *args: Any, **kwargs: Any) -> T:
    return x


def expression_to_string(expression: ExpressionType, depth: int = 0) -> str:
    if isinstance(expression, str):
        return (
            '"'
            + expression.encode("unicode_escape").decode("utf-8").replace('"', '\\"')
            + '"'
        )
    if isinstance(expression, Variable):
        return expression.name
    if isinstance(expression, Function):
        if not expression.arguments:
            return f"{expression.name}()"
        else:
            arg_strings = [
                expression_to_string(a, depth=depth + 1) for a in expression.arguments
            ]
            return f"{expression.name}({', '.join(arg_strings)})"
    if isinstance(expression, Backtick):
        return f"`{expression.command}`"

    # Values returned before this conditional do not get parenthesized at the
    # top level. Ones returned after this do get parenthesized.
    if depth == 0:
        return f"({expression_to_string(expression, depth=depth + 1)})"

    if isinstance(expression, Sum):
        return (
            expression_to_string(expression.sum_1, depth=depth + 1)
            + " + "
            + expression_to_string(expression.sum_2, depth=depth + 1)
        )
    if isinstance(expression, Div):
        return (
            expression_to_string(expression.div_1, depth=depth + 1)
            + " / "
            + expression_to_string(expression.div_2, depth=depth + 1)
        )
    if isinstance(expression, Conditional):
        if isinstance(expression.if_condition, Eq):
            comparison = "=="
        elif isinstance(expression.if_condition, Neq):
            comparison = "!="
        elif isinstance(expression.if_condition, RegexEq):
            comparison = "=~"
        else:
            raise ValueError("Invalid conditional")
        return (
            f"if "
            f"{expression_to_string(expression.if_condition.left, depth=depth + 1)}"
            f" {comparison} "
            f"{expression_to_string(expression.if_condition.right, depth=depth + 1)}"
            f" {{ {expression_to_string(expression.then, depth=depth + 1)} }}"
            f" else "
            f"{{ {expression_to_string(expression.else_then, depth=depth + 1)} }}"
        )
    raise ValueError(f"Unexpected expression type {type(expression)}.")


########################################################################################
# POSIX sh Implementations of just Functions                                           #
########################################################################################

just_functions = {
    # TODO: Add more
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
  if type sha256sum > /dev/null 2>&1; then
    sha256sum --binary "${1}" | cut -d ' ' -f 1
  elif type python3 > /dev/null 2>&1; then
    python3 -c 'from hashlib import sha256; import sys; print(sha256(sys.stdin.buffer.read()).hexdigest())' \
      < "${1}"
  elif type python > /dev/null 2>&1; then
    python -c 'from hashlib import sha256; import sys; print sha256(sys.stdin.read()).hexdigest()' \
      < "${1}"
  else
    echo_error "No sha256sum binary found"
    exit 1
  fi
}
""",
    "sha256": r"""sha256() {
  if type sha256sum > /dev/null 2>&1; then
    printf "%s" "${1}" | sha256sum --binary | cut -d ' ' -f 1
  elif type python3 > /dev/null 2>&1; then
    printf "%s" "${1}" | \
      python3 -c 'from hashlib import sha256; import sys; print(sha256(sys.stdin.buffer.read()).hexdigest())'
  elif type python > /dev/null 2>&1; then
    printf "%s" "${1}" | \
      python3 -c 'from hashlib import sha256; import sys; print sha256(sys.stdin.read()).hexdigest()'
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


########################################################################################
# Helper Classes and Functions                                                         #
########################################################################################


class CompilerState:
    def __init__(self, parsed: List[Item]) -> None:
        self.parsed = parsed
        self.internal_names: Dict[str, str] = dict()

        # Originally, each of these helper functions were done in one or two
        # large passes over the AST with many conditional branches. Here, we do
        # many passes for cleaner code at the expense of looping more times over
        # the same data. In practice, justfiles are small enough that there is a
        # negligible speed impact.
        #
        # Note that the order in which the following functions are run matters
        # in some cases.
        self.settings: Dict[str, Union[bool, None, List[str]]] = self.process_settings()
        self.variables: Dict[str, ExpressionType]
        self.exports: List[str]
        self.variables, self.exports = self.process_variables()
        self.functions: Dict[str, str] = self.process_used_functions()
        self.private_recipes: List[str] = self.process_private_recipes()
        self.recipes: List[str] = self.list_all_recipes(
            cast(Optional[bool], self.settings.get("allow-duplicate-recipes"))
        )
        self.platform_specific_recipes: Dict[
            str, Dict[str, str]
        ] = self.process_platform_recipes()
        self.docstrings: Dict[str, str] = self.process_docstrings()
        self.parameters: Dict[
            str, List[Union[Parameter, VarStar, VarPlus]]
        ] = self.process_recipe_parameters()
        self.aliases: Dict[str, List[str]] = self.process_aliases()
        self.unique_recipes, self.unique_targets = self.process_unique_recipes()
        self.sorted_unique_targets = self.process_sorted_unique_targets()

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

        if (
            settings.get("shell")
            and isinstance(settings.get("shell"), list)
            and len(cast(List[str], settings.get("shell"))) < 2
        ):
            raise ValueError("`shell` setting must have at least two elements.")

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
        # TODO: Replace Any with correct type
        def find_functions(ast_item: Any) -> Any:
            """
            Recursively walk the AST and update `functions` with any function in
            the tree.
            """
            if isinstance(ast_item, Item):
                return find_functions(ast_item.item)
            if isinstance(ast_item, Recipe):
                for parameter in ast_item.parameters:
                    find_functions(parameter.value)
                if ast_item.variadic:
                    find_functions(ast_item.variadic.param.value)
                for dep in ast_item.before_dependencies + ast_item.after_dependencies:
                    for arg in dep.default_args:
                        find_functions(arg)
                for line in ast_item.body:
                    for line_data in line.data:
                        find_functions(line_data)
                return
            if isinstance(ast_item, Interpolation):
                return find_functions(ast_item.expression)
            if isinstance(ast_item, Assignment):
                return find_functions(ast_item.value)
            if isinstance(ast_item, Export):
                return find_functions(ast_item.assignment)
            if isinstance(ast_item, Sum):
                return find_functions(ast_item.sum_1), find_functions(ast_item.sum_2)
            if isinstance(ast_item, Div):
                return find_functions(ast_item.div_1), find_functions(ast_item.div_2)
            if isinstance(ast_item, Backtick):
                functions[
                    "backtick_error"
                ] = """backtick_error() {
  STATUS="${?}"
  echo_error "Backtick failed with exit code ${STATUS}"
  exit "${STATUS}"
}\n"""
                return
            if isinstance(ast_item, Conditional):
                find_functions(ast_item.if_condition.left)
                find_functions(ast_item.if_condition.right)
                find_functions(ast_item.then)
                find_functions(ast_item.else_then)
                conditional_function_name = self.clean_name(
                    f"if_{sha256(str(ast_item))[:16]}"
                )
                if isinstance(ast_item.if_condition, RegexEq):
                    functions[
                        conditional_function_name
                    ] = f"""{conditional_function_name}() {{
  if echo {self.evaluate(ast_item.if_condition.left)} \\
      | grep -E {self.evaluate(ast_item.if_condition.right)} > /dev/null; then
    THEN_EXPR={self.evaluate(ast_item.then)} || exit "${{?}}"
    echo "${{THEN_EXPR}}"
  else
    ELSE_EXPR={self.evaluate(ast_item.else_then)} || exit "${{?}}"
    echo "${{ELSE_EXPR}}"
  fi
}}\n"""
                    return
                if isinstance(ast_item.if_condition, Eq):
                    comparison = "="
                elif isinstance(ast_item.if_condition, Neq):
                    comparison = "!="
                else:
                    raise ValueError(f"Bad if condition {str(ast_item.if_condition)}.")
                functions[
                    conditional_function_name
                ] = f"""{conditional_function_name}() {{
  if [ {
    self.evaluate(ast_item.if_condition.left)
  } {comparison} {
    self.evaluate(ast_item.if_condition.right)
  } ]; then
    THEN_EXPR={self.evaluate(ast_item.then)} || exit "${{?}}"
    echo "${{THEN_EXPR}}"
  else
    ELSE_EXPR={self.evaluate(ast_item.else_then)} || exit "${{?}}"
    echo "${{ELSE_EXPR}}"
  fi
}}\n"""
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
            # raise ValueError(f"Unexpected expression type {str(ast_item)}")

        for item in self.parsed:
            find_functions(item)

        return functions

    def process_private_recipes(self) -> List[str]:
        private_recipes = []
        for item in self.parsed:
            if isinstance(item.item, (Recipe, Alias)) and (
                "private" in item.attributes.names or item.item.name.startswith("_")
            ):
                private_recipes.append(item.item.name)
        return private_recipes

    def list_all_recipes(self, allow_duplicate_recipes: Optional[bool]) -> List[str]:
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

    def process_platform_recipes(self) -> Dict[str, Dict[str, str]]:
        platform_specific_recipes: Dict[str, Dict[str, str]] = defaultdict(dict)
        for item in self.parsed:
            if isinstance(item.item, Recipe):
                recipe = item.item
                # Filter out platform-specific attributes (e.g., drop "private")
                platform_attributes = {"windows", "macos", "linux", "unix"} & set(
                    item.attributes.names
                )
                if platform_attributes:
                    function_name = f"{recipe.name}_{'_'.join(platform_attributes)}"
                    for platform in platform_attributes:
                        platform_specific_recipes[recipe.name][platform] = function_name
        return platform_specific_recipes

    def process_docstrings(self) -> Dict[str, str]:
        docstrings = dict()
        for item_index, item in enumerate(self.parsed):
            if isinstance(item.item, Recipe):
                recipe = item.item
                if (
                    "private" not in item.attributes.names
                    and not recipe.name.startswith("_")
                ):
                    # Store docstrings, but only for non-private recipes
                    if item_index > 0:
                        previous = self.parsed[item_index - 1].item
                    else:
                        previous = None
                    if isinstance(previous, Comment):
                        docstrings[recipe.name] = previous.comment
            elif isinstance(item.item, Alias):
                alias = item.item
                docstrings[alias.name] = f"alias for `{alias.aliased_to}`"
        return docstrings

    def process_aliases(self) -> Dict[str, List[str]]:
        aliases = defaultdict(list)
        for item in self.parsed:
            if isinstance(item.item, Alias):
                alias = item.item
                aliases[alias.aliased_to].append(alias.name)
                if alias.aliased_to in self.parameters:
                    self.parameters[alias.name] = self.parameters[alias.aliased_to]
        return aliases

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
            args = ""
            if to_eval.arguments:
                args = f""" {
                    ' '.join(
                        [self.evaluate(argument) for argument in to_eval.arguments]
                    )
                }"""
            return f'"$({self.clean_name(conditional_function_name)}' + args + ')"'
        raise ValueError(f"Unexpected expression type {str(to_eval)}")

    def process_recipe_parameters(
        self,
    ) -> Dict[str, List[Union[Parameter, VarStar, VarPlus]]]:
        seen_parameters: Dict[str, List[Union[Parameter, VarStar, VarPlus]]] = dict()
        for item in self.parsed:
            if isinstance(item.item, Recipe):
                recipe = item.item
                recipe_params: List[Union[Parameter, VarStar, VarPlus]] = [
                    *recipe.parameters
                ]
                if recipe.variadic:
                    recipe_params.append(recipe.variadic)
                if (
                    recipe.name in seen_parameters
                    and seen_parameters[recipe.name] != recipe_params
                ):
                    logging.warning(
                        f"Recipe {recipe.name} has different parameters than other versions of the "
                        f"same recipe. Only the parameters for the last version of the recipe "
                        f"in the file will be listed."
                    )
                seen_parameters[recipe.name] = recipe_params
        return seen_parameters

    def process_unique_recipes(self) -> Tuple[List[str], List[str]]:
        seen, unique_recipes, unique_targets = set(), list(), list()
        for target in self.recipes:
            if target in seen:
                continue
            seen.add(target)
            if target not in self.private_recipes:
                unique_recipes.append(target)
            unique_targets.append(target)
            for alias_name in sorted(self.aliases.get(target, list())):
                if alias_name not in seen:
                    seen.add(alias_name)
                    unique_targets.append(alias_name)
        return unique_recipes, unique_targets

    def process_sorted_unique_targets(self) -> List[str]:
        seen, unique_targets = set(), list()
        for target in sorted(self.recipes):
            if target in seen:
                continue
            seen.add(target)
            if target not in self.private_recipes:
                unique_targets.append(target)
            for alias_name in sorted(self.aliases.get(target, list())):
                if alias_name not in seen and alias_name not in self.private_recipes:
                    seen.add(alias_name)
                    unique_targets.append(alias_name)
        return unique_targets


def _compile(compiler_state: CompilerState, outfile_path: str, justfile: str) -> str:
    def header_comment(text: str) -> str:
        border = "#" * 89
        return f"""{border}
{newline.join(pad_line(f'# {line}') for line in text.splitlines())}
{border}"""

    def autogen_comment() -> str:
        return header_comment(
            f"""
This script was auto-generated from a Justfile by just.sh.

Generated on {
    datetime.datetime.now().strftime('%Y-%m-%d')
} with just.sh version {__version__}.
https://github.com/jstrieb/just.sh

Run `./{os.path.basename(outfile_path)} --dump` to recover the original Justfile.\n\n"""
        )

    def functions() -> str:
        if not compiler_state.functions:
            return ""
        return f"""\n\n{header_comment("Internal functions")}

{newline.join(f for f in compiler_state.functions.values())}"""

    def dotenv() -> str:
        if not compiler_state.settings.get("dotenv-load"):
            return ""
        # TODO: Handle invocation directory vs justfile directory?
        return """
# Source a `.env` file
TEMP_DOTENV="$(mktemp)"
sed 's/^/export /g' ./.env > "${TEMP_DOTENV}"
. "${TEMP_DOTENV}"
rm "${TEMP_DOTENV}"
"""

    def tmpdir() -> str:
        tmpdir_value = compiler_state.settings.get("tempdir")
        if not tmpdir_value:
            return ""
        return f"TMPDIR={repr(tmpdir_value)}\n"

    def default_variables() -> str:
        shell_setting = compiler_state.settings.get("shell")
        if shell_setting and isinstance(shell_setting, list):
            shell, *args = shell_setting
        else:
            shell, *args = "sh", "-cu"
        return (
            tmpdir()
            + f"""INVOCATION_DIRECTORY="$(pwd)"
DEFAULT_SHELL='{shell}'
DEFAULT_SHELL_ARGS='{' '.join(args)}'
LIST_HEADING='Available recipes:\n'
LIST_PREFIX='    '
CHOOSER='fzf'
SORTED='true'"""
        )

    def color_variables() -> str:
        return """
# Display colors
SHOW_COLOR='false'
if [ -t 1 ]; then SHOW_COLOR='true'; fi
NOCOLOR="$(test "${SHOW_COLOR}" = 'true' && printf "\\033[m" || echo)"
BOLD="$(test "${SHOW_COLOR}" = 'true' && printf "\\033[1m" || echo)"
RED="$(test "${SHOW_COLOR}" = 'true' && printf "\\033[1m\\033[31m" || echo)"
YELLOW="$(test "${SHOW_COLOR}" = 'true' && printf "\\033[33m" || echo)"
CYAN="$(test "${SHOW_COLOR}" = 'true' && printf "\\033[36m" || echo)"
GREEN="$(test "${SHOW_COLOR}" = 'true' && printf "\\033[32m" || echo)"
PINK="$(test "${SHOW_COLOR}" = 'true' && printf "\\033[35m" || echo)"
BLUE="$(test "${SHOW_COLOR}" = 'true' && printf "\\033[34m" || echo)"
TICK="$(printf '%s' '`')"
DOLLAR="$(printf '%s' '$')"
"""

    def assign_variables_function() -> str:
        if compiler_state.variables:
            variable_str = "\n".join(
                f'''  {
                    compiler_state.clean_var_name(var)
                }={
                    compiler_state.evaluate(expr)
                } || exit "${{?}}"'''
                for var, expr in compiler_state.variables.items()
            )
        else:
            variable_str = "  # No user-declared variables"
        return f"""assign_variables() {{
  test -z "${{HAS_RUN_assign_variables:-}}" || return 0

{variable_str}

  HAS_RUN_assign_variables="true"
}}"""

    def variables() -> str:
        return f"""{header_comment("Variables")}
{dotenv()}
# User-overwritable variables (via CLI)
{default_variables()}
{color_variables()}
{assign_variables_function()}"""

    def parameter(p: Union[Parameter, VarStar, VarPlus]) -> str:
        variadic = env = value = ""
        if isinstance(p, VarStar):
            variadic = '"${PINK}"' + "'*'" + '"${NOCOLOR}"'
            p = p.param
        elif isinstance(p, VarPlus):
            variadic = '"${PINK}"' + "'+'" + '"${NOCOLOR}"'
            p = p.param
        if p.env_var:
            env = "'$'"
        name = '"${CYAN}"' + quote_string(p.name) + '"${NOCOLOR}"'
        if p.value is not None:
            value = (
                "'='"
                + '"${GREEN}"'
                + quote_string(expression_to_string(p.value))
                + '"${NOCOLOR}"'
            )
        return f"{variadic}{env}{name}{value}"

    def handle_min_args(r: Recipe) -> str:
        at_least = ""
        if r.variadic or r.num_eq_params > 0:
            at_least = "at least "
        min_args = r.num_non_eq_params
        if isinstance(r.variadic, VarPlus) and r.variadic.param.value is None:
            min_args += 1
        if min_args == 0:
            return ""
        param_names = [parameter(p) for p in r.parameters]
        if r.variadic:
            param_names.append(parameter(r.variadic))
        return f"""  if [ "${{#}}" -lt {min_args} ]; then
    (
      echo_error 'Recipe `{
        r.name
      }`'" got ${{#}} arguments but takes {at_least}{min_args}"
      echo "${{BOLD}}usage:${{NOCOLOR}}"
      echo "    ${{0}} "'{r.name} '{"' '".join(param_names)}
    ) >&2
    exit 1
  fi\n"""

    def param_assignments(r: Recipe) -> str:
        assignments = []
        for i, param in enumerate(r.parameters):
            param_name = compiler_state.clean_var_name(param.name)
            assignments.append(f'  {param_name}="${{{i + 1}:-}}"')
            if param.value is not None:
                assignments.append(
                    f"""  if [ "${{#}}" -lt {i + 1} ]; then
    {param_name}={compiler_state.evaluate(param.value)}
  fi"""
                )
        if r.variadic:
            param = r.variadic.param
            param_name = compiler_state.clean_var_name(param.name)
            if r.parameters:
                assignments.append(
                    f"""  if [ "${{#}}" -ge {len(r.parameters)} ]; then
    shift {len(r.parameters)}
  elif [ "${{#}}" -gt 0 ]; then
    shift "${{#}}"
  fi"""
                )
            if param.value is not None:
                assignments.append(
                    f"""  if [ "${{#}}" -lt 1 ]; then
    set -- {compiler_state.evaluate(param.value)}
  fi"""
                )
            assignments.append(f'  {param_name}="${{*:-}}"')
        return "\n".join(assignments)

    def recipe_parameter_processing(r: Recipe) -> str:
        if not r.parameters and not r.variadic:
            return ""
        return "\n\n" + handle_min_args(r) + param_assignments(r)

    def before_dependency(r: Recipe, d: Dependency) -> str:
        quoted_args = " ".join(compiler_state.evaluate(arg) for arg in d.default_args)
        if quoted_args:
            quoted_args = " " + quoted_args
        force_recipe = compiler_state.clean_name("FORCE_" + r.name)
        force_dep = compiler_state.clean_name("FORCE_" + d.name)
        return f"""  if [ "${{{force_recipe}:-}}" = "true" ]; then
    {force_dep}="true"
  fi
  {compiler_state.clean_fun_name(d.name)}{quoted_args}
  if [ "${{{force_recipe}:-}}" = "true" ]; then
    {force_dep}=
  fi"""

    def recipe_before_dependencies(r: Recipe) -> str:
        if not r.before_dependencies:
            return ""
        return f"""\n\n{
            newline.join(before_dependency(r, d) for d in r.before_dependencies)
        }"""

    def recipe_preamble(r: Recipe) -> str:
        return f"""  test -z "${{{
            compiler_state.clean_name("HAS_RUN_" + r.name)
        }:-}}" \\
    || test "${{{compiler_state.clean_name("FORCE_" + r.name)}:-}}" = "true" \\
    || return 0{
        recipe_parameter_processing(r)
    }{
        recipe_before_dependencies(r)
    }"""

    def tempfile_interpolated_variables(r: Recipe) -> str:
        """
        Store interpolations as intermediate variables to handle errors that may
        occur when evaluating. If they are directly interpolated inline, the
        shell has no mechanism to handle and display errors.
        """
        interpolations = []
        i = 1
        for line in r.body:
            for part in line.data:
                if isinstance(part, Interpolation):
                    exp = compiler_state.evaluate(part.expression)
                    interpolations.append(
                        f'''  INTERP_{i}={exp} || recipe_error '{
                            r.name
                        }' \"${{LINENO:-}}\"'''
                    )
                    i += 1
        if not interpolations:
            return ""
        return "\n" + "\n".join(interpolations)

    def recipe_tempfile_lines(r: Recipe) -> str:
        recipe_lines = []
        non_interp_data: List[str] = []

        # Closure over lines and non_interp_data
        def reset_non_interp_data() -> None:
            if non_interp_data:
                recipe_lines.append(quote_string("".join(non_interp_data)))
                non_interp_data.clear()

        interp_var_counter = 1
        for i, line in enumerate(r.body):
            if i > 0:
                non_interp_data.append("\n")
            for part in line.data:
                if isinstance(part, str):
                    non_interp_data.append(part)
                elif isinstance(part, Interpolation):
                    reset_non_interp_data()
                    recipe_lines.append(
                        quote_string(f"${{INTERP_{interp_var_counter}}}", quote='"')
                    )
                    interp_var_counter += 1
        reset_non_interp_data()
        return "".join(recipe_lines)

    def export_variables(r: Recipe) -> str:
        vars = [*compiler_state.exports]
        if compiler_state.settings.get("export"):
            vars += list(compiler_state.variables.keys())
        for param in r.parameters:
            if param.env_var or compiler_state.settings.get("export"):
                vars.append(param.name)
        if r.variadic and (
            r.variadic.param.env_var or compiler_state.settings.get("export")
        ):
            vars.append(r.variadic.param.name)

        if not vars:
            return ""

        lines = ["\\"]
        for var in vars:
            lines.append(
                f"""    "{
                    compiler_state.clean_name(var)
                }=${{{
                    compiler_state.clean_var_name(var)
                }}}" \\"""
            )
        lines.append("    ")

        return "\n".join(lines)

    def positional_arguments(r: Recipe, pass_recipe_name: bool = True) -> str:
        args = []
        if compiler_state.settings.get("positional-arguments"):
            if pass_recipe_name:
                args.append(f"'{r.name}'")
            for param in r.parameters:
                args.append(f'"${{{compiler_state.clean_var_name(param.name)}}}"')
            if r.variadic:
                args.append('"${@}"')
        return " ".join(args)

    def recipe_tempfile_body(r: Recipe, attributes: Set[str]) -> str:
        """
        Write the recipe body to a temporary file to execute via shebang.
        """
        cat_recipe = ""
        if not r.echo:
            cat_recipe = '\n  cat "${TEMPFILE}" >&2'

        exit_message = ""
        if "no-exit-message" not in attributes:
            exit_message = f'\\\n    || recipe_error "{r.name}"'

        return f"""  TEMPFILE="$(mktemp)"
  touch "${{TEMPFILE}}"
  chmod +x "${{TEMPFILE}}"{
  tempfile_interpolated_variables(r)
  }
  echo {recipe_tempfile_lines(r)} > "${{TEMPFILE}}"{
  cat_recipe
  }
  env {export_variables(r)}"${{TEMPFILE}}" {
    positional_arguments(r, pass_recipe_name=False)
  } {exit_message}
  rm "${{TEMPFILE}}" """

    def regular_interpolated_variables(
        r: Recipe, line: Line, i: int
    ) -> Tuple[int, List[str]]:
        interpolations = []
        for part in line.data:
            if isinstance(part, Interpolation):
                exp = compiler_state.evaluate(part.expression)
                interpolations.append(
                    f"  INTERP_{i}={exp} || recipe_error '{r.name}' \"${{LINENO:-}}\""
                )
                i += 1
        return i, interpolations

    def recipe_body_line(
        line: Line, interpolation_counter: int, attributes: Set[str]
    ) -> str:
        exec_str = []
        for i, part in enumerate(line.data):
            # TODO: Use better matching for "just" invocations. For example:
            # match common shells, instead of replacing invocation for any
            # non-default shell.
            if (
                isinstance(part, str)
                and i == 0
                and not compiler_state.settings.get("shell")
                and part.startswith("just ")
            ):
                if "no-cd" in attributes:
                    exec_str.append('"${0}"')
                else:
                    # TODO: Better invocation that doesn't assume this file has
                    # chmod +x
                    exec_str.append('"./$(basename "${0}")"')
                part = part[4:]
            if isinstance(part, str):
                exec_str.append(compiler_state.evaluate(part))
            elif isinstance(part, Interpolation):
                exec_str.append(
                    quote_string(f"${{INTERP_{interpolation_counter}}}", quote='"')
                )
                interpolation_counter += 1
        return "".join(exec_str)

    def recipe_regular_body(r: Recipe, attributes: Set[str]) -> str:
        """
        Write the recipe body to be directly executed.
        """
        lines = []
        interpolation_counter = 1
        for line in r.body:
            # TODO: Should interpolations be executed in comments that are
            # ignored? Add test for this behavior.
            if (
                compiler_state.settings.get("ignore-comments")
                and len(line.data) > 0
                and isinstance(line.data[0], str)
                and line.data[0].startswith("#")
            ):
                continue
            interpolation_counter_start = interpolation_counter
            interpolation_counter, interpolations = regular_interpolated_variables(
                r, line, interpolation_counter
            )
            lines.extend(interpolations)
            exec_str = recipe_body_line(line, interpolation_counter_start, attributes)
            if r.echo ^ (line.prefix is not None and "@" in line.prefix):
                lines.append(f"  echo_recipe_line {exec_str}")
            lines.append(
                f"""  env {
                    export_variables(r)
                }"${{DEFAULT_SHELL}}" ${{DEFAULT_SHELL_ARGS}} \\"""
            )
            if line.prefix is not None and "-" in line.prefix:
                lines.append(f"    {exec_str} {positional_arguments(r)} \\")
                lines.append("    || true")
            elif "no-exit-message" in attributes:
                lines.append(f"    {exec_str} {positional_arguments(r)}")
            else:
                lines.append(f"    {exec_str} {positional_arguments(r)} \\")
                lines.append(f'    || recipe_error "{r.name}" "${{LINENO:-}}"')
        return "\n".join(lines)

    def after_dependencies(r: Recipe) -> str:
        # TODO: Use a hash set of executed subcommands to more correctly
        # determine whether to re-execute recipes
        if not r.after_dependencies:
            return ""
        lines = []
        after_deps_seen: Set[str] = set()
        for dep in r.after_dependencies:
            quoted_args = " ".join(
                compiler_state.evaluate(arg) for arg in dep.default_args
            )
            if quoted_args:
                quoted_args = " " + quoted_args
            if dep.name not in after_deps_seen:
                # Force running, even if it has run before
                lines.append(
                    f'  {compiler_state.clean_name("FORCE_" + dep.name)}="true"'
                )
                lines.append(
                    f"  {compiler_state.clean_fun_name(dep.name)}{quoted_args}"
                )
                lines.append(f'  {compiler_state.clean_name("FORCE_" + dep.name)}=')
                after_deps_seen.add(dep.name)
        return "\n" + "\n".join(lines) + "\n\n"

    def recipe_epilogue(r: Recipe, attributes: Set[str]) -> str:
        cd = ""
        if "no-cd" not in attributes:
            cd = '  cd "${OLD_WD}"\n'

        return f"""{
  cd
}{
  after_dependencies(r)
}  if [ -z "${{{compiler_state.clean_name("FORCE_" + r.name)}:-}}" ]; then
    {compiler_state.clean_name("HAS_RUN_" + r.name)}="true"
  fi"""

    def recipe(r: Recipe, attributes: Set[str], index: int) -> str:
        # Filter out platform-specific attributes (e.g., drop "private")
        platform_attributes = {"windows", "macos", "linux", "unix"} & attributes
        function_name = (
            f"{r.name}_{'_'.join(platform_attributes)}"
            if platform_attributes
            else r.name
        )
        if (
            len(r.body) > 0
            and len(r.body[0].data) > 0
            and isinstance(r.body[0].data[0], str)
            and r.body[0].data[0].startswith("#!")
        ):
            recipe_body = recipe_tempfile_body(r, attributes)
        else:
            recipe_body = recipe_regular_body(r, attributes)
        change_workdir = ""
        if "no-cd" not in attributes:
            change_workdir = '''\n\n  OLD_WD="$(pwd)"
  cd "${INVOCATION_DIRECTORY}"'''
        return f"""{compiler_state.clean_fun_name(function_name)}() {{
  # Recipe setup and pre-recipe dependencies
{recipe_preamble(r)}{
change_workdir
}

  # Recipe body
{recipe_body}

  # Post-recipe dependencies and teardown
{recipe_epilogue(r, attributes)}
}}"""

    def comment(c: Comment) -> str:
        logging.warning(
            "Comments may be in unexpected places in the generated script. "
            "They are placed relative to recipes, not variable assignments "
            "or settings, which may be moved around."
        )
        return f"# {c.comment}"

    def alias(a: Alias) -> str:
        return f"""{compiler_state.clean_fun_name(a.name)}() {{
  {compiler_state.clean_fun_name(a.aliased_to)} "$@"
}}"""

    def platform_dispatchers() -> List[str]:
        dispatchers = []
        for (
            recipe_name,
            platform_functions,
        ) in compiler_state.platform_specific_recipes.items():
            conditional_lines = []
            for i, (platform, function_name) in enumerate(platform_functions.items()):
                os_helper = "os_family" if platform == "unix" else "os"
                conditional = "elif"
                if i == 0:
                    conditional = "if"
                conditional_lines.append(
                    f"  {conditional} [ \"$({os_helper})\" = '{platform}' ]; then"
                )
                conditional_lines.append(
                    f"    {compiler_state.clean_fun_name(function_name)}"
                )
            dispatchers.append(
                f"""{compiler_state.clean_fun_name(recipe_name)}() {{
{newline.join(conditional_lines)}
  else
    echo_error \"Justfile does not contain recipe \"'`{recipe_name}`.'
  fi
}}"""
            )
        return dispatchers

    def recipes() -> str:
        compiled_recipes = []
        for index, item in enumerate(compiler_state.parsed):
            if isinstance(item.item, Recipe):
                compiled_recipes.append(
                    recipe(item.item, set(item.attributes.names), index)
                )
            elif isinstance(item.item, Comment):
                compiled_recipes.append(comment(item.item))
            elif isinstance(item.item, Alias):
                compiled_recipes.append(alias(item.item))
        compiled_recipes.extend(platform_dispatchers())
        return f"""
{header_comment("Recipes")}

{(newline * 2).join(compiled_recipes)}"""

    def recipe_summaries() -> str:
        unique_recipe_list = "echo 'Justfile contains no recipes.' >&2"
        if compiler_state.unique_recipes:
            unique_recipe_list = f"""if [ "${{SORTED}}" = "true" ]; then
    printf "%s " {' '.join(sorted(compiler_state.unique_recipes))}
  else
    printf "%s " {' '.join(compiler_state.unique_recipes)}
  fi
  echo\n"""
        return unique_recipe_list

    def comment_str(raw_comment: Optional[str]) -> str:
        if not raw_comment:
            return ""
        return quote_string(f" # {raw_comment}")

    def params_str(params: Optional[List[Union[Parameter, VarStar, VarPlus]]]) -> str:
        if not params:
            return ""
        param_names = [parameter(p) for p in params]
        return "' '" + "' '".join(param_names)

    def colorized_target(target: str) -> str:
        docstring = compiler_state.docstrings.get(target)
        params = compiler_state.parameters.get(target)
        return f'''echo "${{LIST_PREFIX}}"{
            quote_string(target)
        }{
            params_str(params)
        }"${{BLUE}}"{
            comment_str(docstring)
        }"${{NOCOLOR}}"'''

    def list_fn() -> str:
        if compiler_state.sorted_unique_targets:
            sorted_colorized_targets = "\n    ".join(
                [
                    colorized_target(target)
                    for target in compiler_state.sorted_unique_targets
                ]
            )
        else:
            sorted_colorized_targets = "true"
        if not (
            set(compiler_state.unique_targets) - set(compiler_state.private_recipes)
        ):
            colorized_targets = "true"
        else:
            colorized_targets = "\n    ".join(
                [
                    colorized_target(target)
                    for target in compiler_state.unique_targets
                    if target not in compiler_state.private_recipes
                ]
            )
        return f"""listfn() {{
  while [ "$#" -gt 0 ]; do
    case "${{1}}" in
    --list-heading)
      shift
      LIST_HEADING="${{1}}"
      ;;

    --list-prefix)
      shift
      LIST_PREFIX="${{1}}"
      ;;

    -u|--unsorted)
      SORTED="false"
      ;;
    esac
    shift
  done

  printf "%s" "${{LIST_HEADING}}"
  if [ "${{SORTED}}" = "true" ]; then 
    {sorted_colorized_targets}
  else
    {colorized_targets}
  fi
}}"""

    def dump_fn() -> str:
        # Use hash of file instead of EOF to prevent issues if "EOF" literal is
        # in the Justfile
        sha = sha256(justfile)[:16]
        return f"""dumpfn() {{
  cat <<"{sha}"
{justfile.strip()}
{sha}
}}"""

    def spaced_var_name(name: str, max_len: int) -> str:
        spaces = " " * (max_len - len(name) + 1)
        return f'{name}{spaces}:= "\'"${{{compiler_state.clean_var_name(name)}}}"\'"'

    def match_variable_case(name: str) -> str:
        return f"""{name})
      printf "%s" "${{{compiler_state.clean_var_name(name)}}}"
      ;;"""

    def evaluate_fn() -> str:
        if compiler_state.variables:
            max_len = max(len(k) for k in compiler_state.variables)
            echo_variables = "\n    ".join(
                f"echo '{spaced_var_name(name, max_len)}'"
                for name in sorted(compiler_state.variables.keys())
            )
            variable_cases = "\n    ".join(
                match_variable_case(name) for name in compiler_state.variables
            )
        else:
            echo_variables = "true"
            variable_cases = "# No user-declared variables"
        return f"""evaluatefn() {{
  assign_variables || exit "${{?}}"
  if [ "${{#}}" = "0" ]; then
    {echo_variables}
  else
    case "${{1}}" in
    {variable_cases}
    *)
      echo_error 'Justfile does not contain variable `'"${{1}}"'`.'
      exit 1
      ;;
    esac
  fi
}}"""

    def choose_fn() -> str:
        return f"""choosefn() {{
  echo {' '.join(quote_string(target) for target in compiler_state.unique_targets)} \\
    | "${{DEFAULT_SHELL}}" ${{DEFAULT_SHELL_ARGS}} "${{CHOOSER}}"
}}"""

    def helper_functions() -> str:
        return f"""
{header_comment("Helper functions")}

# Sane, portable echo that doesn't escape characters like "\\n" behind your back
echo() {{
  if [ "${{#}}" -gt 0 ]; then
    printf "%s\\n" "${{@}}"
  else
    printf "\\n"
  fi
}}

# realpath is a GNU coreutils extension
realpath() {{
  # The methods to replicate it get increasingly error-prone
  # TODO: improve
  if type -P realpath > /dev/null 2>&1; then
    "$(type -P realpath)" "${{1}}"
  elif type python3 > /dev/null 2>&1; then
    python3 -c 'import os.path, sys; print(os.path.realpath(sys.argv[1]))' "${{1}}"
  elif type python > /dev/null 2>&1; then
    python -c 'import os.path, sys; print os.path.realpath(sys.argv[1])' "${{1}}"
  elif [ -f "${{1}}" ] && ! [ -z "$(dirname "${{1}}")" ]; then
    # We assume the directory exists. For our uses, it always does
    echo "$(
      cd "$(dirname "${{1}}")";
      pwd -P
    )/$(
      basename "${{1}}"
    )"
  elif [ -f "${{1}}" ]; then
    pwd -P
  elif [ -d "${{1}}" ]; then
  (
    cd "${{1}}"
    pwd -P
  )
  else
    echo "${{1}}"
  fi
}}

echo_error() {{
  echo "${{RED}}error${{NOCOLOR}}: ${{BOLD}}${{1}}${{NOCOLOR}}" >&2
}}

recipe_error() {{
  STATUS="${{?}}"
  if [ -z "${{2:-}}" ]; then
      echo_error "Recipe "'`'"${{1}}"'`'" failed with exit code ${{STATUS}}"
  else
      echo_error "Recipe "'`'"${{1}}"'`'" failed on line ${{2}} with exit code ${{STATUS}}"
  fi
  exit "${{STATUS}}"
}}

echo_recipe_line() {{
  echo "${{BOLD}}${{1}}${{NOCOLOR}}" >&2
}}
            
set_var() {{
  export "VAR_${{1}}=${{2}}"
}}
            
summarizefn() {{
  while [ "$#" -gt 0 ]; do
    case "${{1}}" in
    -u|--unsorted)
      SORTED="false"
      ;;
    esac
    shift
  done

  {recipe_summaries()}
}}

usage() {{
  cat <<EOF
${{GREEN}}just.sh${{NOCOLOR}} {__version__}
Jacob Strieb
    Auto-generated from a Justfile by just.sh - https://github.com/jstrieb/just.sh

${{YELLOW}}USAGE:${{NOCOLOR}}
    ./just.sh [FLAGS] [OPTIONS] [ARGUMENTS]...

${{YELLOW}}FLAGS:${{NOCOLOR}}
        ${{GREEN}}--choose${{NOCOLOR}}      Select one or more recipes to run using a binary. If ${{TICK}}--chooser${{TICK}} is not passed the chooser defaults to the value of ${{DOLLAR}}JUST_CHOOSER, falling back to ${{TICK}}fzf${{TICK}}
        ${{GREEN}}--dump${{NOCOLOR}}        Print justfile
        ${{GREEN}}--evaluate${{NOCOLOR}}    Evaluate and print all variables. If a variable name is given as an argument, only print that variable's value.
        ${{GREEN}}--init${{NOCOLOR}}        Initialize new justfile in project root
    ${{GREEN}}-l, --list${{NOCOLOR}}        List available recipes and their arguments
        ${{GREEN}}--summary${{NOCOLOR}}     List names of available recipes
    ${{GREEN}}-u, --unsorted${{NOCOLOR}}    Return list and summary entries in source order
    ${{GREEN}}-h, --help${{NOCOLOR}}        Print help information
    ${{GREEN}}-V, --version${{NOCOLOR}}     Print version information

${{YELLOW}}OPTIONS:${{NOCOLOR}}
        ${{GREEN}}--chooser <CHOOSER>${{NOCOLOR}}           Override binary invoked by ${{TICK}}--choose${{TICK}}
        ${{GREEN}}--list-heading <TEXT>${{NOCOLOR}}         Print <TEXT> before list
        ${{GREEN}}--list-prefix <TEXT>${{NOCOLOR}}          Print <TEXT> before each list item
        ${{GREEN}}--set <VARIABLE> <VALUE>${{NOCOLOR}}      Override <VARIABLE> with <VALUE>
        ${{GREEN}}--shell <SHELL>${{NOCOLOR}}               Invoke <SHELL> to run recipes
        ${{GREEN}}--shell-arg <SHELL-ARG>${{NOCOLOR}}       Invoke shell with <SHELL-ARG> as an argument

${{YELLOW}}ARGS:${{NOCOLOR}}
    ${{GREEN}}<ARGUMENTS>...${{NOCOLOR}}    Overrides and recipe(s) to run, defaulting to the first recipe in the justfile
EOF
}}

err_usage() {{
  cat <<EOF >&2
USAGE:
    ./just.sh [FLAGS] [OPTIONS] [ARGUMENTS]...

For more information try ${{GREEN}}--help${{NOCOLOR}}
EOF
}}

{list_fn()}

{dump_fn()}

{evaluate_fn()}

{choose_fn()}"""

    def target_case(target: str) -> str:
        recipe_parameters = compiler_state.parameters.get(target, [])
        is_variadic = any(isinstance(p, (VarStar, VarPlus)) for p in recipe_parameters)
        if is_variadic:
            shift_params = "break\n    "
        elif recipe_parameters:
            shift_params = f"""if [ "${{#}}" -ge "{len(recipe_parameters)}" ]; then
      shift {len(recipe_parameters)}
    elif [ "${{#}}" -gt 0 ]; then
      shift "${{#}}"
    fi\n    """
        else:
            shift_params = ""
        return f"""{target})
    shift
    assign_variables || exit "${{?}}"
    {compiler_state.clean_fun_name(target)} "$@"
    RUN_DEFAULT='false'
    {shift_params};;"""

    def main_entrypoint() -> str:
        target_cases = "\n\n  ".join(
            target_case(target) for target in compiler_state.unique_targets
        )
        if compiler_state.recipes:
            default_recipe = compiler_state.recipes[0]
            default_call = ""
            if compiler_state.parameters.get(default_recipe):
                non_eq_parameters = len(
                    [
                        p
                        for p in compiler_state.parameters.get(default_recipe, [])
                        if (isinstance(p, Parameter) and p.value is None)
                        or (not isinstance(p, Parameter) and p.param.value is None)
                    ]
                )
                default_call += f"""if [ "${{#}}" -lt "{non_eq_parameters}" ]; then
    echo_error 'Recipe `{
      default_recipe
    }` cannot be used as default recipe since it requires at least {
      non_eq_parameters
    } argument{
      "s" if non_eq_parameters != 1 else ""
    }.'
    exit 1
  fi\n  """
            default_call += f"""assign_variables || exit "${{?}}"
  {compiler_state.clean_fun_name(default_recipe)} "$@" """
        else:
            default_call = """assign_variables || exit "${?}"
  exit 1"""
        return f"""
{header_comment("Main entrypoint")}

RUN_DEFAULT='true'
while [ "${{#}}" -gt 0 ]; do
  case "${{1}}" in 
  
  # User-defined recipes
  {target_cases}
  
  # Built-in flags
  -l|--list)
    shift 
    listfn "$@"
    RUN_DEFAULT="false"
    break
    ;;
    
  -f|--justfile)
    shift 2
    echo "${{YELLOW}}warning${{NOCOLOR}}: ${{BOLD}}-f/--justfile not implemented by just.sh${{NOCOLOR}}" >&2
    ;;

  --summary)
    shift
    summarizefn "$@"
    RUN_DEFAULT="false"
    break
    ;;

  --list-heading)
    shift
    LIST_HEADING="${{1}}"
    shift
    ;;

  --list-prefix)
    shift
    LIST_PREFIX="${{1}}"
    shift
    ;;

  -u|--unsorted)
    SORTED="false"
    shift
    ;;

  --shell)
    shift
    DEFAULT_SHELL="${{1}}"
    shift
    ;;

  --shell-arg)
    shift
    DEFAULT_SHELL_ARGS="${{1}}"
    shift
    ;;
    
  -V|--version)
    shift
    echo "just.sh {__version__}"
    echo
    echo "https://github.com/jstrieb/just.sh"
    RUN_DEFAULT="false"
    break
    ;;

  -h|--help)
    shift
    usage
    RUN_DEFAULT="false"
    break
    ;;

  --choose)
    shift
    assign_variables || exit "${{?}}"
    TARGET="$(choosefn)"
    env "${{0}}" "${{TARGET}}" "$@"
    RUN_DEFAULT="false"
    break
    ;;
    
  --chooser)
    shift
    CHOOSER="${{1}}"
    shift
    ;;
    
  *=*)
    assign_variables || exit "${{?}}"
    NAME="$(
        echo "${{1}}" | tr '\\n' '\\r' | sed 's/\\([^=]*\\)=.*/\\1/g' | tr '\\r' '\\n'
    )"
    VALUE="$(
        echo "${{1}}" | tr '\\n' '\\r' | sed 's/[^=]*=\\(.*\\)/\\1/g' | tr '\\r' '\\n'
    )"
    shift
    set_var "${{NAME}}" "${{VALUE}}"
    ;;

  --set)
    shift
    assign_variables || exit "${{?}}"
    NAME="${{1}}"
    shift
    VALUE="${{1}}"
    shift
    set_var "${{NAME}}" "${{VALUE}}"
    ;;
    
  --dump)
    RUN_DEFAULT="false"
    dumpfn "$@"
    break
    ;;
    
  --evaluate)
    shift
    RUN_DEFAULT="false"
    evaluatefn "$@"
    break
    ;;
    
  --init)
    shift
    RUN_DEFAULT="false"
    if [ -f "justfile" ]; then
      echo_error "Justfile "'`'"$(realpath "justfile")"'`'" already exists"
      exit 1
    fi
    cat > "justfile" <<EOF
default:
    echo 'Hello, world!'
EOF
    echo 'Wrote justfile to `'"$(realpath "justfile")"'`' 2>&1 
    break
    ;;

  -*)
    echo_error "Found argument '${{NOCOLOR}}${{YELLOW}}${{1}}${{NOCOLOR}}${{BOLD}}' that wasn't expected, or isn't valid in this context"
    echo >&2
    err_usage
    exit 1
    ;;

  *)
    assign_variables || exit "${{?}}"
    echo_error 'Justfile does not contain recipe `'"${{1}}"'`.'
    exit 1
    ;;
  esac
done

if [ "${{RUN_DEFAULT}}" = "true" ]; then
  {default_call}
fi"""

    ###
    # End of helper functions – main _compile output
    ###
    return f"""#!/bin/sh

{autogen_comment()}

if sh "set -o pipefail" > /dev/null 2>&1; then
  set -euo pipefail
else
  set -eu
fi
{functions()}

{variables()}

{recipes()}

{helper_functions()}

{main_entrypoint()}


{autogen_comment()}

"""


########################################################################################
# Main Function                                                                        #
########################################################################################


def compile(justfile: str, outfile_path: str, verbose: bool) -> str:
    compiler_state = CompilerState(justfile_parse(justfile, verbose=verbose))
    return _compile(compiler_state, outfile_path, justfile)


def main(
    justfile_path: Optional[str], outfile_path: str, verbose: bool = False
) -> None:
    if justfile_path is None:
        for filename in ["justfile", ".justfile", "Justfile", ".Justfile"]:
            if os.path.isfile(filename):
                justfile_path = filename
                break

    print(
        f"""Compiling Justfile to shell script: `{
            justfile_path if justfile_path != '-' else 'stdin'
        }` -> `{
            outfile_path if outfile_path != '-' else 'stdout'
        }`""",
        file=sys.stderr,
    )

    if justfile_path is None or justfile_path == "-":
        justfile_data = sys.stdin.read()
    else:
        with open(justfile_path) as f:
            justfile_data = f.read()

    if outfile_path == "-":
        sys.stdout.write(compile(justfile_data, "just.sh", verbose))
    else:
        with open(outfile_path, "w") as f:
            f.write(compile(justfile_data, outfile_path, verbose))
        os.chmod(outfile_path, os.stat(outfile_path).st_mode | stat.S_IEXEC)


def cli_entrypoint() -> None:
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
        print(
            f"just.sh – Justfile to POSIX shell script compiler (version {__version__})"
        )
        return

    logging.basicConfig(format="%(levelname)s: %(message)s")

    if sys.argv[0].endswith("just.sh"):
        logging.warning(
            "Call `./just.sh` instead of `just.sh` to execute the generated script."
        )

    main(parsed_args.infile, parsed_args.outfile, verbose=parsed_args.verbose)


if __name__ == "__main__":
    cli_entrypoint()
