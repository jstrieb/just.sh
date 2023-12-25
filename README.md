# just.sh

`just.sh` transpiles [Justfiles](https://github.com/casey/just) to portable,
POSIX-compatible shell scripts. 

- Run Justfile recipes without installing `just` 
- Run `just` commands in constrained environments (such as CI pipelines and
  Docker containers)
- Use a `justfile` as a starting point for a shell script

`just.sh` is built as a drop-in replacement for `just`. It can parse any valid
`justfile`, and generated scripts behave identically to `just`. In almost all
cases, generated scripts even have byte-for-byte identical output to `just`.
There are over 32,000 tests (covering every line of code!) that validate this
compatibility for each commit.


# Install & Quick Start

[Try `just.sh` online without installing.](https://jstrieb.github.io/just.sh/)

Install `just.sh` locally with:

``` bash
python3 -m pip install just.sh
```

Once installed, run `just.sh` to convert a `Justfile` to a shell script. Then,
run the generated script `./just.sh` as you would run `just`.

```
$ ls
justfile

$ just --summary
build lint test

$ just.sh
Compiling Justfile to shell script: `justfile` -> `just.sh`

$ ls
justfile just.sh

$ ./just.sh --summary
build lint test
```

The `pip` installation script also installs the following aliases to the
`just.sh` command-line tool:

- `just_sh`
- `just-sh`
- `pyjust`

# Project Status

I like tools that effectively achieve one well-defined goal, without growing
indefinitely. My hope is for `just.sh` to be such a tool.

In other words, `just.sh` is "complete" software. I will fix bugs and make
changes to maintain compatibility with `just`, but there are no new features 
planned. 

As such, even if there are no recent commits, the project is not dead! Few
commits means that everything has been running smoothly.

`just.sh` is written in Python with only one dependency outside the Python
standard library: [parsy](https://github.com/python-parsy/parsy), which itself
has no external dependencies. All of the code is spread across two fairly small
files (plus one more for tests), and every line of code is covered by tests.

# Known Issues & Incompatibilities

- Not all Just functions are implemented in shell 
  - For example, many of the string manipulation functions such as
    `trim_end_match`, `trim_start_match`, `titlecase`, etc. remain to be
    implemented 
  - Trying to compile a Justfile with an unimplemented function will raise an
    error
- Some Just functions (`sha256` in particular) cannot be made portable without
  depending on `sha256sum` or Python on the target system
- Not all nested calls to `just` are detected. Calling `just` in the middle of a
  recipe may result in unexpected behavior
- 100% Python line coverage guarantees that all Python code that generates shell
  scripts is exercised, but it does not guarantee that all generated lines of
  shell are exercised in tests
- Just re-runs recipes if they are called again with a unique combination of
  arguments. `just.sh` approximates this behavior instead of replicating it,
  using simpler heuristics for whether a recipe has run before
- Generated `just.sh` shell files may be hard to read, and are typically much
  larger than the Justfiles they replace
- `import`, `[confirm]`, and possibly some other recent features from Just
  versions greater than 1.14.0 may not yet be supported
- The `./just.sh --dump` command does not reformat Justfiles
- The tests check colorless output of `just.sh` against `just`. They do not
  confirm that the colors and ANSI escape sequences are the same between the
  two
  
# Acknowledgments

- Thanks to [Logan Snow](https://github.com/lsnow99) for testing early versions,
  consulting on design decisions, and being a great guy overall
