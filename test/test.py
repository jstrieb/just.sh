import io
import itertools
import random
import re
import subprocess
from typing import Any, Iterable, List, Tuple, Union

import pytest

import just_sh.convert as convert
import just_sh.parse as parse


def flatten(input_list: Iterable[Union[Iterable[Any], Any]]) -> List[Any]:
    result = []
    for x in input_list:
        if isinstance(x, (tuple, list)):
            result += list(x)
        else:
            result.append(x)
    return result


def permuted_combinations(fixed: Any, *args: Any, fix_first: bool = False) -> List[Any]:
    return [
        flatten([fixed, *permutation] if fix_first else permutation)
        for length in range(0 if fix_first else 1, len(args) + 1)
        for combo in itertools.combinations(
            args if fix_first else [fixed, *args], length
        )
        for permutation in itertools.permutations(combo)
    ]


FLAG_COMBOS = [
    [],
    ["invalid_arg"],
    ["-z"],
    ["--invalid-flag"],
    *permuted_combinations("--summary", "--unsorted", fix_first=True),
    *[
        combo
        for prefix in ["pref", "two words", "", "'\n\"\\n\"\n'", "\n\\n\n"]
        for heading in ["test", "two words", "", "\n\\n\n", "'\n\\n\n'", "$'\n\\n\n'"]
        for combo in permuted_combinations(
            "--list",
            "--unsorted",
            ("--list-prefix", prefix),
            ("--list-heading", heading),
            fix_first=True,
        )
    ],
    ["--evaluate"],
    # Don't compare --help output - just.sh implements only some of the flags
    # ["--help"],
    # Don't compare dump output – just reformats and canonicalizes the dump
    # ["--dump"],
]


def chdir(justfile_content: str, tmpdir: Any) -> None:
    justfile = tmpdir.join("Justfile")
    justfile.write(justfile_content)
    convert.main(str(justfile), str(tmpdir.join("just.sh")))
    tmpdir.chdir()


def pair_args_justfile(
    *pairs: Tuple[str, List[List[str]]], reverse: bool = True
) -> Iterable[Tuple[List[str], str]]:
    # Test in reverse order so more recent tests come first to avoid a wait
    for justfile, arg_combos in reversed(pairs) if reverse else pairs:
        for args in FLAG_COMBOS + arg_combos:
            yield args, justfile


def run_justfile(args: Iterable[str]) -> None:
    reference_run = subprocess.run(["just", *args], capture_output=True)
    script_run = subprocess.run(["sh", "./just.sh", *args], capture_output=True)
    assert normalize_output(reference_run.stderr) == normalize_output(script_run.stderr)
    assert normalize_output(reference_run.stdout) == normalize_output(script_run.stdout)
    assert reference_run.returncode == script_run.returncode


# Store in a variable outside of the decorator to avoid MASSIVE backtraces
paired_args_justfiles = pair_args_justfile(
    # Simplest possible Justfile
    (
        """
simple:
    echo Simple!
""",
        [
            ["simple"],
        ],
    ),
    # Default recipes
    # https://just.systems/man/en/chapter_21.html
    (
        """
default: lint build test

build:
  echo Building…

test:
  echo Testing…

lint:
  echo Linting…
""",
        permuted_combinations("default", "build", "test", "lint", fix_first=True),
    ),
    (
        """
default:
  just --list
""",
        [
            ["default"],
        ],
    ),
    # Aliases
    # https://just.systems/man/en/chapter_23.html
    (
        """
alias d := build

lint:
  echo 'Linting!'

alias b := build

build:
  echo 'Building!'

alias c := build
alias a := build

test2:
  echo 'Test2'
""",
        permuted_combinations(
            "d",
            "lint",
            "b",
            "build",
            "test2",
        ),
    ),
    (
        """
run args:
  echo {{args}}

run2 arg1 arg2 default="val":
  echo {{arg1}}
  echo Cool
  echo {{arg2}}
  echo Super cool
  echo {{default}}

alias r := run
alias r2 := run2
""",
        [
            ["run", "arg"],
            ["run", "some arguments here!"],
            ["r", "arg"],
            ["r", "some arguments here!"],
            *permuted_combinations(("run2", "arg2", "arg3"), "extra", fix_first=True),
            *permuted_combinations(("r2", "arg2", "arg3"), "extra", fix_first=True),
        ],
    ),
    # Settings
    # https://just.systems/man/en/chapter_24.html
    (
        """
set shell := ["bash", "-c", "-u"]

foo:
  # this line will be run as `bash -cu 'ls */*'`
  ls */*

set allow-duplicate-recipes

@bar:
  echo foo

@bar:
  echo bar

set export := true
set positional-arguments := false

a := "hello"

@baz b:
  echo $a
  @echo $b
""",
        permuted_combinations("foo", "bar", ("baz", "goodbye")),
    ),
    (
        """
set positional-arguments

@foo bar:
  echo $0
  echo $1

set ignore-comments := true

@test *args='':
  #!/bin/bash
  # Comment 3
  bash -c 'while (( "$#" )); do echo - $1; shift; done' -- "$@"
  # Comment 2

test2 arg1 +restargs="more args":
  # Comment 1
  echo '{{arg1}}!'
  bash -c 'while (( "$#" )); do echo - $1; shift; done' -- "$@"
""",
        [
            ["foo", "hello"],
            *permuted_combinations(
                ("foo", "hello", "test"), "first", "second", "third", fix_first=True
            ),
            *permuted_combinations(
                ("foo", "hello", "test2", "zeroth"),
                "first",
                "second",
                "third",
                fix_first=True,
            ),
            *permuted_combinations(
                "test", "first", "second", "third", "fourth", fix_first=True
            ),
            *permuted_combinations(
                "test2", "first", "second", "third", "fourth", fix_first=True
            ),
        ],
    ),
    (
        """
# use python3 to execute recipe lines and backticks
set shell := ["python3", "-c" ] 
set tempdir := "/tmp/" 

# use print to capture result of evaluation
foos := `print("foo" * 4)`

foo:
  print("Snake snake snake snake.")
  print("{{foos}}")
""",
        # TODO: Change argument parsing loop to parse flags first, then
        #  recipes. Until then, permutations where variable setting happens
        #  after the recipes succeed when they should fail.
        # permuted_combinations(
        #     "foo",
        #     ("--set", "foos", "ro dah"),
        #     "foos=DDDDDDDDD",
        # ),
        [
            ["foo"],
            ["--set", "foos", "ro dah"],
            ["--set", "foos", "ro dah", "foo"],
            ["foos=DDDDDDDDDD"],
            ["foos=DDDDDDDDDD", "foo"],
            ["--set", "foos", "ro dah", "foos=DDDDDDDDDD"],
            ["--set", "foos", "ro dah", "foos=DDDDDDDDDD", "foo"],
        ],
    ),
    # Documentation comments
    # https://just.systems/man/en/chapter_25.html
    (
        """
# build stuff
build: 
  echo ./bin/build 

# test stuff
test:
  echo ./bin/test >&2
""",
        permuted_combinations("build", "test"),
    ),
    # Variables and substitution
    # https://just.systems/man/en/chapter_27.html
    (
        """
tmpdir  := `mktemp -d`
version := "0.2.7"
tardir  := tmpdir / "awesomesauce-" + version
tarball := tardir + ".tar.gz"

readme:
  echo "Some stuff" > README.md
  printf "int main(int argc, char **argv) {\\n\\treturn 0\\n}\\n" > test.c

publish: readme
  rm -f {{tarball}}
  mkdir -p {{tardir}}
  cp README.md *.c {{tardir}}
  tar zcvf {{tarball}} {{tardir}}
  tar tzvf {{tarball}}
  rm -rf {{tarball}} {{tmpdir}}

foo := "a" / "b"

foo2 := "a/"
bar := foo2 / "b"

foo3 := / "b"

braces:
  echo 'I {{{{LOVE}} curly braces!'

braces2:
  echo '{{'I {{LOVE}} curly braces!'}}'

braces3:
  echo 'I {{ "{{" }}LOVE}} curly braces!'
""",
        [
            *permuted_combinations("readme", "publish", "braces", "braces2", "braces3"),
            ["--evaluate", "tmpdir"],
            ["--evaluate", "version"],
            ["--evaluate", "tardir"],
            ["--evaluate", "tarball"],
            ["--evaluate", "foo"],
            ["--evaluate", "foo2"],
            ["--evaluate", "bar"],
            ["--evaluate", "foo3"],
            ["--evaluate", "nonexist"],
        ],
    ),
    # Strings
    # https://just.systems/man/en/chapter_28.html
    (
        r"""
string-with-tab             := "\t"
string-with-newline         := "\n"
string-with-carriage-return := "\r"
string-with-double-quote    := "\""
string-with-slash           := "\\"
string-with-no-newline      := "\
"
single := '
hello
'

double := "
goodbye
"
escapes := '\t\n\r\"\\'
# this string will evaluate to `foo\nbar\n`
x := '''
  foo
  bar
'''

# this string will evaluate to `abc\n  wuv\nbar\n`
y := """
        + '''"""
  abc
    wuv
  xyz
"""

z := """testing"""

recipe:
  @just --evaluate
''',
        [
            ["recipe"],
            ["--evaluate", "string-with-tab"],
            ["--evaluate", "string-with-newline"],
            ["--evaluate", "string-with-carriage-return"],
            ["--evaluate", "string-with-double-quote"],
            ["--evaluate", "string-with-slash"],
            ["--evaluate", "string-with-no-newline"],
            ["--evaluate", "single"],
            ["--evaluate", "double"],
            ["--evaluate", "escapes"],
            ["--evaluate", "x"],
            ["--evaluate", "y"],
            ["--evaluate", "z"],
        ],
    ),
    # Ignoring errors
    # https://just.systems/man/en/chapter_29.html
    (
        r"""
foo:
  -cat foo
  echo 'Done!'
""",
        permuted_combinations("foo"),
    ),
    # Functions
    # https://just.systems/man/en/chapter_30.html
    (
        r"""
system-info:
  @echo "This is an {{arch()}} machine ({{os()}} => {{os_family()}})".
  echo "{{home_dir}} {{home_dir_2}}"

home_dir := env_var('HOME')
home_dir_2 := env_var_or_default('HOME', '/tmp')

test default = join("one", "two", 'three'):
  echo "{{home_dir}} {{home_dir_2}}"
  echo "{{ if home_dir == home_dir_2 { "$(echo equal)" } else { "unequal!" } }}"
  echo "{{env_var_or_default('ThIs_DoEs_NoT_eXiSt', 'FAKE!!!')}}"
  echo "{{invocation_directory()}}"
  echo "{{justfile_directory()}} + {{justfile()}} = {{justfile_directory() / justfile()}}"
  echo "Alternatively, {{justfile_directory() + justfile()}}"
  echo {{quote("I'd've should've quoted this!")}}
  echo {{uppercase(quote("I'd've should've quoted this!"))}}
  echo {{lowercase(quote("I'd've should've quoted this!"))}}
  echo {{default}}
  echo {{sha256(default)}}
  echo '{{if uuid() == "0" { "uh oh...$$$" } else { "success" } }}'
  echo '{{if uuid()=="0"{"something has gone horribly wrong"}else{"success"} }}'
  
should_fail:
  echo "{{invocation_directory()}}
  
to-hash := `mktemp`

make-file filename=to-hash:
  #!/bin/bash
  set -euo pipefail

  cat > "{{filename}}" <<"EOF"
  $(echo ${VARS} shouldn't work here)
      See if you can spot this one?
  {{`date '+%Y'`}}
  EOF


try-hash filename=to-hash: (make-file filename)
  #!/bin/bash
  set -euo pipefail

  if [ \
      "$(sha256sum {{filename}} | cut -d ' ' -f 1)" \
      != \
      "{{sha256_file(filename)}}" \
  ]; then
    echo "FAILURE"
    echo "$(sha256sum {{filename}} | cut -d ' ' -f 1)"
    echo "{{sha256_file(filename)}}"
    exit 1
  fi
  rm -f "{{filename}}"


""",
        [
            *permuted_combinations(
                "system-info",
                ("test", "default"),
                "should_fail",
                ("try-hash", f"/tmp/tmp.{random.randint(0, 10000)}"),
            ),
            ["try-hash"],
            ["test"],
            ["--evaluate", "home_dir"],
            ["--evaluate", "home_dir_2"],
            ["--evaluate", "to-hash"],
        ],
    ),
    # Recipe Attributes
    # https://just.systems/man/en/chapter_31.html
    (
        r"""
[no-cd]
[private]
foo:
    echo "foo"
    pwd
    
[private]
[no-cd]
baz:
    echo "baz"
    pwd

bar: foo _priv
    pwd
    
[no-exit-message]
test:
    exit 1
    
test2 arg="arg": test
    exit 2
    
[unix]
run:
    echo cc main.c
    echo ./a.out

[windows]
run: && windows_only
    echo cl main.c
    echo main.exe
    
windows_only:
    echo 'Micro$oft time!'
    
_priv:
    echo Also private | sed 's/private/[REDACTED]/g'
    
alias a := test2
alias _b := test

[private]
alias c := bar

""",
        [
            ["a"],
            ["_b"],
            ["c"],
            *permuted_combinations(
                "foo",
                "bar",
                "baz",
                "test",
                "test2",
                "run",
                "_priv",
            ),
        ],
    ),
    # Command evaluation with backticks
    # https://just.systems/man/en/chapter_32.html
    (
        r"""
export VAR := "value that should not appear in backticks"

bullets := `echo "this is a test" | tr ' ' '\n' | sed 's/^/- /'`
unbound := `echo "${VAR}" | tr ' ' '\n' | sed 's/^/- /'`
    
# This backtick evaluates the command `echo foo\necho bar\n`, which produces the
# value `foo\nbar\n`.
stuff := ```
    echo foo
    echo bar
  ```
  
dedented := ```
        echo 'this
        is
            a 
        test
            of indents/dedents'
    ```

echo:
    echo '{{bullets}}'
    echo '{{`echo inline`}}'
    echo '{{```
    echo this
echo should
    echo work 
    ```}}'
    echo {{VAR}}""",
        [
            ["--evaluate", "bullets"],
            ["--evaluate", "stuff"],
            ["--evaluate", "dedented"],
            ["--evaluate", "VAR"],
            ["--evaluate", "unbound"],
            ["echo"],
        ],
    ),
    # Conditional expressions
    # https://just.systems/man/en/chapter_33.html
    (
        r"""
foo := if "2" == "2" { "Good!" } else { "1984" }

bar:
  @echo "{{foo}}"
  @echo {{foo2}}
  @echo {{foo3}}

foo2 := if "hello" != "goodbye" { "xyz" } else { "abc" } 
foo3 := if "hello" =~ 'hel+o' { "match" } else { "mismatch" }
foo4 := if env_var_or_default("RELEASE", "false") == "true" { `get-something-from-release-database` } else { "dummy-value" }

bar5 foo5:
  echo {{ if foo5 == "bar" { "hello" } else { "goodbye" } }}

foo6 := if "hello" == "goodbye" {
  "xyz"
} else if "a" == "a" {
  "abc"
} else {
  "123"
}
""",
        [
            ["--evaluate", "foo"],
            ["--evaluate", "foo2"],
            ["--evaluate", "foo3"],
            ["--evaluate", "foo4"],
            ["--evaluate", "foo6"],
            *permuted_combinations("bar", ("bar5", "test")),
            *permuted_combinations("bar", ("bar5", "bar")),
        ],
    ),
    # Stopping execution with error
    # https://just.systems/man/en/chapter_34.html
    (
        r"""
foo := if "hello" == "goodbye" { 
  "xyz"  
} else if "a" == "b" { 
  "abc"  
} else { 
  error("123") 
}

undefined := env_var("NONEXIST_UNDEFINED")
""",
        [
            ["--evaluate", "foo"],
            ["--evaluate", "undefined"],
        ],
    ),
    # Setting variables from the command line
    # https://just.systems/man/en/chapter_35.html
    (
        r"""
os := "linux"

test: build
  echo ./test --test {{os}}

build:
  echo ./build {{os}}
""",
        [
            ["--evaluate", "os"],
            *[
                combo + ["test"]
                for combo in permuted_combinations(
                    ("--set", "os", "plan9"),
                    "os=Free BSD",
                )
            ],
            *[
                combo + ["build"]
                for combo in permuted_combinations(
                    ("--set", "os", "plan9"),
                    "os=Free BSD",
                )
            ],
        ],
    ),
    # Environment variables
    # https://just.systems/man/en/chapter_36.html
    (
        r"""
export RUST_BACKTRACE := "1" 

test:
  echo cargo test ${RUST_BACKTRACE}
  
toast $DUST_BACKTRACE="1":
  echo cargo test ${DUST_BACKTRACE}
  
export WORLD := "world"
# This backtick will fail with "WORLD: unbound variable"
BAR := `echo hello $WORLD`

# Running `just a foo` will fail with "A: unbound variable"
a $A $B=`echo $A`:
  echo $A $B
  
print_home_folder:
  echo "HOME is: '${HOME}'"
""",
        [
            ["--evaluate", "RUST_BACKTRACE"],
            ["--evaluate", "WORLD"],
            ["--evaluate", "BAR"],
            ["a", "foo"],
            ["toast"],
            ["toast", "3", "a", "foo"],
            *permuted_combinations(
                ("a", "foo", "bar"), "test", "print_home_folder", ("toast", "0")
            ),
        ],
    ),
    # Recipe Parameters
    # https://just.systems/man/en/chapter_37.html
    (
        r"""
default: (build "main")

build target:
  @echo 'Building {{target}}…'
  cd {{target}} && echo make
""",
        permuted_combinations(("build", "my-awesome-project"), "default"),
    ),
    (
        r"""
target := "main"

_build version:
  @echo 'Building {{version}}…'
  cd {{version}} && echo make

build: (_build target)
""",
        [
            ["--set", "target", "my-awesome-project"],
            ["--set", "target", "my-awesome-project", "_build", "the_project"],
            *permuted_combinations("build", ("_build", "proj")),
        ],
    ),
    (
        r"""
build target:
  @echo "Building {{target}}…"

push target: (build target)
  @echo 'Pushing {{target}}…'
""",
        permuted_combinations(("build", "proj"), ("push", "notproj")),
    ),
    (
        r"""
default := 'all'

test target tests=default:
  @echo 'Testing {{target}}:{{tests}}…'
  echo ./test --tests {{tests}} {{target}}
""",
        [
            ["--evaluate", "default"],
            ["test"],
            ["test", "faketarget"],
            ["test", "faketarget2", "the tests"],
            ["--set", "default", "notall", "test", "faketarget2", "the tests"],
            ["--set", "default", "notall", "test", "faketarget2"],
            ["--set", "default", "notall", "test"],
            ["default=some", "test", "faketarget2", "the tests"],
            ["default=some", "test", "faketarget2"],
            ["default=some", "test"],
        ],
    ),
    (
        r"""
arch := "wasm"

test triple=(arch + "-unknown-unknown") input=(arch / "input.dat"):
  echo ./test {{triple}}
""",
        [
            ["--evaluate", "arch"],
            ["test"],
            ["test", "faketarget"],
            ["test", "faketarget2", "fakeinput"],
            ["--set", "arch", "winders", "test"],
            ["--set", "arch", "winders", "test", "faketarget2"],
            ["--set", "arch", "winders", "test", "faketarget2", "the input"],
            ["arch=some", "test"],
            ["arch=some", "test", "faketarget2"],
            ["arch=some", "test", "faketarget2", "the input"],
        ],
    ),
    (
        r"""
backup +FILES:
  printf "%s\n" scp {{FILES}} me@server.com:
  
commit MESSAGE *FLAGS:
  printf "%s\n" git commit {{FLAGS}} -m "{{MESSAGE}}"
  
test +FLAGS='-q':
  printf "%s\t" cargo test {{FLAGS}}
  
search QUERY:
  printf "%s\n" lynx https://www.google.com/?q={{QUERY}}
  
foo $bar:
  echo $bar
""",
        [
            ["backup"],
            ["backup", "file"],
            ["backup", "file", "file2"],
            ["backup", "file", "file2", "file3"],
            ["commit"],
            ["commit", "msg"],
            ["commit", "msg", "flag1"],
            ["commit", "msg", "flag1", "flag2"],
            ["commit", "msg", "flag1", "flag2", "flag3"],
            ["commit", "the message", "backup", "file1", "file2"],
            ["backup", "file1", "file2", "commit", "the message"],
            ["test"],
            ["test", "flag1"],
            ["test", "flag1", "flag2"],
            ["test", "flag1", "flag2", "flag3"],
            ["search"],
            ["search", "query"],
            ["search", "multi word query"],
            ["foo"],
            ["foo", "thebar"],
            ["foo", "barbar", "backup", "file"],
        ],
    ),
    # Running recipes at the end of a recipe
    # https://just.systems/man/en/chapter_38.html
    (
        r"""
a:
  echo 'A!'

b: a && c d c
  echo 'B!'

c: && d
  echo 'C!'

d: a
  echo 'D!'
""",
        [
            list(combo)
            for combo in {
                tuple(combo)
                for combo in permuted_combinations(*(["a", "b", "c", "d"] * 2))
            }
        ],
    ),
    # Running recipes in the middle of a recipe
    # https://just.systems/man/en/chapter_39.html
    (
        r"""
a:
  echo 'A!'

b: a
  echo 'B start!'
  just c
  echo 'B end!'

c:
  echo 'C!'
""",
        [
            list(combo)
            for combo in {
                tuple(combo) for combo in permuted_combinations(*(["a", "b", "c"] * 2))
            }
        ],
    ),
    # Shebang recipes
    # https://just.systems/man/en/chapter_40.html
    (
        r"""
polyglot: (python "some args" ("python" / "ic")) js perl sh ruby 

python arg1 arg2:
  #!/usr/bin/env python3
  print('Hello from python!')
  print("{{arg1}} {{arg2}}")

js:
  #!/usr/bin/env node
  console.log('Greetings from JavaScript!')

perl:
  #!/usr/bin/env perl
  print "Larry Wall says Hi!\n";

sh:
  #!/usr/bin/env sh
  hello='Yo' 
  echo "$hello from a shell script!"

ruby:
  #!/usr/bin/env ruby
  puts "Hello from ruby!"
""",
        permuted_combinations(
            "polyglot",
            ("python", "some args", "python/ic"),
            "js",
            "perl",
            "sh",
            "ruby",
        ),
    ),
    # Changing the working directory
    # https://just.systems/man/en/chapter_44.html
    (
        r"""
foo:
  pwd    # This `pwd` will print the same directory… 
  cd ..
  pwd    # …as this `pwd`!
  
foo2:
  cd / && pwd

foo3:
  #!/usr/bin/env bash
  set -euxo pipefail
  cd /
  pwd
""",
        permuted_combinations(
            "foo",
            "foo2",
            "foo3",
        ),
    ),
    # Private recipes
    # https://just.systems/man/en/chapter_48.html
    (
        r"""
test: _test-helper
  echo ./bin/test

_test-helper:
  echo ./bin/super-secret-test-helper-stuff

[private]
foo:

[private]
alias b := bar

bar:
""",
        permuted_combinations("test", "_test-helper", "foo", "b", "bar"),
    ),
    # Quiet recipes
    # https://just.systems/man/en/chapter_49.html
    (
        r"""
@quiet:
  echo hello
  echo goodbye
  @# all done!

foo:
  #!/usr/bin/env bash
  echo 'Foo!'

@bar:
  #!/usr/bin/env bash
  echo 'Bar!'

git *args:
    @git {{args}}

[no-exit-message]
git2 *args:
    @git {{args}}
""",
        permuted_combinations("quiet", "foo", "bar", "git", "git2"),
    ),
    # Misc. tests for coverage
    (
        r"""
# This comment logs a message that comments will be moved around
var_name := if invocation_directory_native() != / "tmp" { "not tmp" } else { "tmp" }
var-name := if just_executable() == / "usr" + ("/local" / "bin") { "is folder...?" } else { "expected" }

echo in=(if just_executable() =~ "just" { "Matches!" } else { "No match!" }):
    echo {{ if var-name == var_name { "uh oh" } else { "nice" } }}
    echo {{ in }}
""",
        [
            ["--evaluate", "var-name"],
            ["--evaluate", "var_name"],
            ["echo"],
            ["echo", "🤔"],
        ],
    ),
    (
        r"""
set positional-arguments

# The recipe below has some indented newlines and some empty newlines. It is 
# hard to see in some editors, but testing lines with all spaces and lines with 
# no spaces are both important.
python3 $default=(
  if if "a" != "b" { "no" } else { "yes" } == "no" { "yes" } else { "no" }
):
    #!/usr/bin/python3

    def main():
        print("Super cool text")
        print("{{default}}") 
        import os
        print(os.environ["default"])
        import sys
        if len(sys.argv) > 1:
            print(sys.argv[1])
        
    if __name__ == "__main__":
        main()
        
[no-cd] 
list:  
    just --list 
""",
        [
            ["python3"],
            ["python3", "$default"],
            ["list"],
            ["list", "python3"],
            ["list", "python3", "$default"],
            ["python3", "list"],
            ["python3", "$default", "list"],
        ],
    ),
    (
        r"""
set export 

big-multiline_var := ```
    echo this 
    echo is
      echo a tabbed-in
        echo multiline
    echo variable
    line


```

with-args *arg1=big-multiline_var:
    echo "{{arg1}}"
    echo "${big-multiline_var}"
    
first-dep: && (with-args "nice") 


[windows]
empty arg="default":

[unix]
empty arg="nondefault": 
""",
        [
            ["--evaluate", "big-multiline_var"],
            ["first-dep"],
            ["first-dep", "with-args"],
            ["first-dep", "with-args", "the arg"],
            ["with-args", "first-dep"],
            ["with-args", "the arg", "first-dep"],
        ],
    ),
    reverse=False,
)


@pytest.mark.parametrize(
    "args, justfile_content",
    paired_args_justfiles,
)
def test_justfile(args: List[str], justfile_content: str, tmpdir: Any) -> None:
    chdir(justfile_content, tmpdir)
    run_justfile(args)


def test_dotenv(tmpdir: Any) -> None:
    tmpdir.chdir()
    justfile_content = """
set dotenv-load

test exists=path_exists(".env"):
    echo "Dotenv exists? {{exists}}!"
    echo "${VAR_NUM_1}"
    echo "${VAR_2}"
"""
    justfile = tmpdir.join("Justfile")
    justfile.write(justfile_content)
    convert.main(None, "just.sh")

    with open(".env", "w") as f:
        f.write("VAR_NUM_1=testing\n")
        f.write("VAR_2='this is very nice'\n")
        f.flush()

    for args in FLAG_COMBOS + permuted_combinations("test"):
        run_justfile(args)
    parse.main("Justfile", verbose=True)


def test_stdin_stdout(tmpdir: Any, monkeypatch: Any, capsys: Any) -> None:
    tmpdir.chdir()
    justfile_content = """
default: lint build test

build:
  echo Building…

test:
  echo Testing…

lint:
  echo Linting…
"""
    monkeypatch.setattr("sys.stdin", io.StringIO(justfile_content))
    convert.main("-", "-")
    just_sh_output = capsys.readouterr().out

    parse.main("-", verbose=True)

    with open("justfile", "w") as f:
        f.write(justfile_content)
    with open("just.sh", "w") as f:
        f.write(just_sh_output)

    for args in FLAG_COMBOS + permuted_combinations("default", "build", "test", "lint"):
        run_justfile(args)


def test_evalute_without_quoting() -> None:
    state = convert.CompilerState([])
    assert state.evaluate("testing") == "'testing'"
    assert state.evaluate("testing", quote=False) == "testing"


NORMALIZE_REGEXES = [
    (re.compile(rb"(\./)?just\.sh"), rb"just"),
    (re.compile(rb"Justfile"), rb"just"),
    (re.compile(rb"on line \d+"), rb""),
    (re.compile(rb"line \d+"), rb"line 0"),
    (re.compile(rb" +"), rb" "),
    (re.compile(rb'"'), rb"'"),
    (re.compile(rb"tmp\.[a-zA-Z0-9]+"), rb"tmp"),
    (re.compile(rb"/var/folder[^\s]+"), rb"tmp"),  # macOS temp directories
    (re.compile(rb"/[^\s]*tmp[^\s]+"), rb"tmp"),
    (re.compile(rb"\d{1,2}:\d{1,2}"), rb"12:00"),  # Normalize times
    (re.compile(rb"\nDid you mean[^\n]*"), rb""),
    (re.compile(rb"which wasn't expected"), rb"that wasn't expected"),
    # (re.compile(rb"(.+\n.+\n.+)?\n\nUSAGE:\n(.+|\n+)+"), rb""),  # TODO: Remove
    (re.compile(rb"\[--\]\s*"), rb""),
    (
        re.compile(rb"(\s*-->[^\n]*\n)?\s*\|[^\n]*\n\s*\d*?\s*\|[^\n]*\n\s*\|[^\n]*"),
        rb"",
    ),
]


def normalize_output(s: bytes) -> bytes:
    result = s.strip()
    for regex, replace in NORMALIZE_REGEXES:
        result = regex.sub(replace, result)
    return result
