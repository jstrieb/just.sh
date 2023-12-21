import argparse
import dataclasses
import json
import os.path
import re
import sys
from dataclasses import dataclass
from typing import Any, Callable, List, Optional, TextIO, Tuple, Type, Union, cast

from parsy import (
    Parser,
    alt,
    any_char,
    eof,
    forward_declaration,
    generate,
    peek,
    regex,
    seq,
    whitespace,
)
from parsy import (
    string as strp,
)


class DataclassDictEncoder(json.JSONEncoder):  # pragma: no cover
    def default(self, o: Any) -> Any:
        if dataclasses.is_dataclass(o):
            return dataclasses.asdict(o)
        return super().default(o)


class DataclassStringEncoder(json.JSONEncoder):  # pragma: no cover
    def default(self, o: Any) -> Any:
        if dataclasses.is_dataclass(o):
            return str(o)
        return super().default(o)


@dataclass
class Backtick:
    command: str


@dataclass
class Comment:
    comment: str


@dataclass
class Function:
    name: str
    arguments: Optional[List["ExpressionType"]]


@dataclass
class Variable:
    name: str


@dataclass
class Div:
    div_1: "ExpressionType"
    div_2: "ExpressionType"


@dataclass
class Sum:
    sum_1: "ExpressionType"
    sum_2: "ExpressionType"


@dataclass
class Alias:
    name: str
    aliased_to: str


@dataclass
class Assignment:
    name: str
    value: "ExpressionType"


@dataclass
class Export:
    assignment: Assignment


@dataclass
class Setting:
    setting: str
    value: Union[Optional[bool], List[str]]


@dataclass
class Eq:
    left: "ExpressionType"
    right: "ExpressionType"


@dataclass
class Neq:
    left: "ExpressionType"
    right: "ExpressionType"


@dataclass
class RegexEq:
    left: "ExpressionType"
    right: "ExpressionType"


@dataclass
class Conditional:
    if_condition: Union[Eq, Neq, RegexEq]
    then: "ExpressionType"
    else_then: "ExpressionType"


@dataclass
class Attributes:
    names: List[str]


@dataclass
class Parameter:
    env_var: bool
    name: str
    value: Optional["ExpressionType"]


@dataclass
class VarStar:
    param: Parameter


@dataclass
class VarPlus:
    param: Parameter


@dataclass
class Dependency:
    name: str
    default_args: List["ExpressionType"] = dataclasses.field(default_factory=list)


@dataclass
class Interpolation:
    expression: "ExpressionType"


@dataclass
class Line:
    prefix: Optional[str]
    data: List[Union[str, Interpolation]]


@dataclass
class Recipe:
    echo: bool
    name: str
    parameters: List[Parameter]
    variadic: Optional[Union[VarPlus, VarStar]]
    before_dependencies: List[Dependency]
    after_dependencies: List[Dependency]
    body: List[Line]
    num_non_eq_params: int = dataclasses.field(init=False)
    num_eq_params: int = dataclasses.field(init=False)

    def __post_init__(self) -> None:
        try:
            self.num_non_eq_params = next(
                i for i, p in enumerate(self.parameters) if p.value is not None
            )
        except StopIteration:
            self.num_non_eq_params = len(self.parameters)
        self.num_eq_params = len(self.parameters) - self.num_non_eq_params
        if (
            self.variadic is not None
            and self.variadic.param.value is None
            and self.num_eq_params
        ):
            # TODO: Make ParseError
            raise RuntimeError(
                "Variadic following parameters with default values must have "
                f'default values in "{self.name}"'
            )
        # FIXME: Bump eq_params variables based on variadic?


@dataclass
class Item:
    attributes: Attributes
    item: Any


ExpressionType = Union[str, Variable, Sum, Div, Backtick, Conditional, Function]


def parse(data: str, verbose: bool = False) -> List[Item]:
    def preprocess(raw_data: str) -> str:
        return re.sub("\\\\s*\n\\s*", "", raw_data).strip() + "\n\n"

    def surround(p: Parser) -> Callable[[Parser], Parser]:
        def result(p2: Parser) -> Parser:
            return p >> p2 << p

        return result

    def between(s: str) -> Parser:
        s_parser = strp(s)
        return surround(s_parser)(any_char.until(s_parser).concat())

    def settings(*args: Tuple[str, Parser]) -> Parser:
        seqs = []
        for name, p in args:
            seqs.append(
                seq(
                    strp("set") >> whitespace >> lex2(strp(name)),
                    lex2(p),
                ).combine(Setting)
            )
        return alt(*seqs)

    # TODO: Replace Any with type variable
    def debug(name: str) -> Callable[[Any], Any]:
        """
        Handy helper for logging which parsers ran – returns a version of the
        identity function with the side effect of printing the input if
        "verbose" is true.
        """

        # TODO: Replace Any with type variable
        def _debug(val: Any) -> Any:
            if verbose:
                print(name, f'"{val}"')
            return val

        return _debug

    def dedent(s: str) -> str:
        """
        Non-generic dedent – remove common whitespace prefixes for each
        indented line in a multiline string. Delete some leading and trailing
        whitespace lines based on conditions specific to Just.
        """
        if s.startswith("\n"):
            s = s[1:]
        if s.endswith("\n\n"):
            s = s[:-1]
        split = list(s.splitlines())
        prefix = os.path.commonprefix(
            [line for line in split if not re.fullmatch(r"\s*", line)]
        )
        indent_matches = re.findall(r"^\s+", prefix)
        if not indent_matches:
            return s
        indent_end_index = len(indent_matches[0])
        return (
            "\n".join(
                line[indent_end_index:] if line.startswith(prefix) else ""
                for line in split
            )
            + "\n"
        )

    escaped_char = (
        strp(r"\\").result("\\")
        | strp(r"\"").result('"')
        | strp(r"\n").result("\n")
        | strp(r"\r").result("\r")
        | strp(r"\t").result("\t")
    )
    space = regex(r"[ \t]+")
    lex = surround(whitespace.optional())
    lex2 = surround(space.optional())

    # Tokens (adapted) from the official grammar:
    # https://github.com/casey/just/blob/beeaa6ce2d93110766940bf01a0f5428d62cd49f/GRAMMAR.md
    BACKTICK = between("`").map(Backtick).map(debug("backtick"))
    INDENTED_BACKTICK = (
        between("```").map(dedent).map(Backtick).map(debug("indent backtick"))
    )
    NEWLINE = (strp("\n") | strp("\r\n")).map(debug("newline"))
    COMMENT = (
        (lex2(strp("#")) >> regex(r"[^!]") + any_char.until(NEWLINE).concat())
        .map(Comment)
        .map(debug("comment"))
    )
    INDENT = (strp("    ") | strp("  ") | strp("\t")).map(debug("indent"))  # TODO
    EOF = eof.map(debug("eof"))
    NAME = regex(r"[a-zA-Z_][a-zA-Z0-9_-]*").map(debug("name"))
    RAW_STRING = between("'").map(debug("raw_string"))
    INDENTED_RAW_STRING = between("'''").map(dedent).map(debug("raw_string"))
    STRING = (
        strp('"') >> (escaped_char | any_char).until(strp('"')).concat() << strp('"')
    ).map(debug("STRING"))
    INDENTED_STRING = (
        (
            strp('"""')
            >> (escaped_char | any_char).until(strp('"""')).concat()
            << strp('"""')
        )
        .map(dedent)
        .map(debug("indented string"))
    )
    LINE_PREFIX = (strp("@-") | strp("-@") | strp("@") | strp("-")).map(
        debug("line_prefix")
    )

    # Forward declarations
    function_args = forward_declaration()
    expression = forward_declaration()
    condition = forward_declaration()
    conditional = forward_declaration()

    # Parsers (modified) from the official grammar:
    # https://github.com/casey/just/blob/beeaa6ce2d93110766940bf01a0f5428d62cd49f/GRAMMAR.md
    string = (INDENTED_STRING | STRING | INDENTED_RAW_STRING | RAW_STRING).map(
        debug("string")
    )

    eol = (COMMENT >> NEWLINE | NEWLINE.at_least(1)).map(debug("eol"))

    value = (
        seq(
            NAME,
            strp("(") >> lex(function_args).optional() << strp(")"),
        ).combine(Function)
        | (strp("(") >> lex(expression) << strp(")"))
        | INDENTED_BACKTICK
        | BACKTICK
        | string
        | NAME.map(Variable)
    ).map(debug("value"))

    conditional.become(
        seq(
            lex(strp("if")) >> condition,
            lex(strp("{")) >> expression << lex(strp("}")),
            lex(strp("else"))
            >> (conditional | (lex(strp("{")) >> lex(expression) << lex2(strp("}")))),
        ).combine(Conditional)
    )

    expression.become(
        (
            conditional
            | seq(value.optional(""), lex(strp("/")) >> expression).combine(Div)
            | seq(value, lex(strp("+")) >> expression).combine(Sum)
            | value
        ).map(debug("expression"))
    )

    alias = (
        seq(
            strp("alias") >> whitespace >> NAME,
            lex(strp(":=")) >> NAME,
        ).combine(Alias)
        << eol
    ).map(debug("alias"))

    assignment = (
        seq(
            lex2(NAME),
            lex(strp(":=")) >> lex2(expression),
        ).combine(Assignment)
        << eol
    ).map(debug("assignment"))

    export = (
        (strp("export") >> whitespace >> assignment).map(Export).map(debug("export"))
    )

    boolean = (
        lex(strp(":=")) >> (strp("true").result(True) | strp("false").result(False))
    ).map(debug("boolean"))

    listp = (
        strp("[")
        >> lex(string.sep_by(lex(strp(","))))
        << lex(strp(",").optional())
        << strp("]")
    ).map(debug("listp"))

    setting = (
        settings(
            ("allow-duplicate-recipes", boolean.optional(True)),
            ("dotenv-load", boolean.optional(True)),
            ("export", boolean.optional(True)),
            ("fallback", boolean.optional(True)),
            ("ignore-comments", boolean.optional(True)),
            ("positional-arguments", boolean.optional(True)),
            ("windows-powershell", boolean.optional(True)),
            (
                "tempdir",
                lex(strp(":=")) >> string,
            ),
            (
                "shell",
                lex(strp(":=")) >> listp,
            ),
            (
                "windows-shell",
                lex(strp(":=")) >> listp,
            ),
        )
        << eol
    ).map(debug("setting"))

    condition.become(
        (
            seq(expression, lex(strp("==")) >> expression).combine(Eq)
            | seq(expression, lex(strp("!=")) >> expression).combine(Neq)
            | seq(expression, lex(strp("=~")) >> expression).combine(RegexEq)
        ).map(debug("condition"))
    )

    function_args.become(
        (lex(expression).sep_by(lex(strp(","))) << lex(strp(",")).optional()).map(
            debug("function")
        )
    )

    attributes = (
        (
            lex2(strp("["))
            >> (lex(NAME.sep_by(lex(strp(",")))) << lex(strp(",").optional()))
            << lex2(strp("]"))
            << eol
        )
        .many()
        .map(lambda row: Attributes([x for col in row for x in col]))
        .map(debug("attribute"))
    )

    parameter = (
        seq(
            strp("$").result(True).optional(False),
            NAME,
            peek(lex(strp("="))).should_fail("param").result(None),
        ).combine(Parameter)
    ).map(debug("parameter"))

    default_val_parameter = (
        seq(
            strp("$").result(True).optional(False),
            NAME,
            lex(strp("=")) >> value,
        ).combine(Parameter)
    ).map(debug("default_val_parameter"))

    variadic = (
        (strp("*") >> (parameter | default_val_parameter)).map(VarStar)
        | (strp("+") >> (parameter | default_val_parameter)).map(VarPlus)
    ).map(debug("variadic"))

    dependency = (
        (
            NAME.map(lambda s: [s])
            | seq(strp("(") >> NAME, lex(expression).many() << strp(")"))
        )
        .combine(Dependency)
        .map(debug("dependency"))
    )

    interpolation = (
        (strp("{{") >> lex(expression) << strp("}}"))
        .map(Interpolation)
        .map(debug("interpolation"))
    )

    line = (
        seq(
            LINE_PREFIX.optional(),
            (
                strp("{{{{").result("{{")
                | interpolation
                | regex(r"[^\r\n]")
                .until(strp("{{{{") | interpolation | eol, min=1)
                .concat()
            ).at_least(1),
        ).combine(Line)
        << eol
    ).map(debug("line"))

    @generate  # type: ignore  # Untyped decorator makes function _body untyped
    def _body() -> Parser:
        initial_indent = yield peek(INDENT.at_least(1).concat())
        return (strp(initial_indent) >> line).at_least(1)

    body = _body.map(debug("body"))

    recipe = (
        seq(
            lex2(strp("@")).result(False).optional(True),
            lex2(NAME),
            lex2(parameter).many() + lex2(default_val_parameter).many(),
            lex2(variadic).optional() << lex2(strp(":")),
            lex2(dependency).many(),
            (lex2(strp("&&")) >> lex2(dependency).at_least(1)).optional([]),
            (eol | whitespace) >> body.optional([]),
        ).combine(Recipe)
        << NEWLINE.many()
    ).map(debug("recipe"))

    item = (
        seq(
            lex(attributes).optional(Attributes([])),
            recipe | alias | assignment | export | setting | COMMENT | eol,
        )
        .combine(Item)
        .map(debug("item"))
    )

    justfile_parser = item.many() << eol.many() << EOF
    return cast(List[Item], justfile_parser.parse(preprocess(data)))


def run(f: TextIO, encoder: Type[json.JSONEncoder], verbose: bool = False) -> None:
    print(json.dumps(parse(f.read(), verbose=verbose), cls=encoder, indent=2))


def main(justfile_path: str, verbose: bool = False) -> None:
    encoder = DataclassStringEncoder if verbose else DataclassDictEncoder
    if justfile_path is None or justfile_path == "-":
        run(sys.stdin, encoder, verbose=verbose)
    else:
        with open(justfile_path) as f:
            run(f, encoder, verbose=verbose)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Parse a justfile")
    parser.add_argument("-i", "--infile", action="store", help="Input justfile path")
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Verbose parser output"
    )
    parsed_args = parser.parse_args()

    main(parsed_args.infile, verbose=parsed_args.verbose)
