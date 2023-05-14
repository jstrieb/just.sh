# `just.sh`

`just.sh` transpiles [justfiles](https://github.com/casey/just) to portable,
POSIX-compatible shell scripts. 

- Run `justfile` recipes without installing `just` 
- Run `just` commands in constrained environments (such as CI pipelines and
  Docker containers)
- Use a `justfile` as a starting point for a shell script

`just.sh` is built as a drop-in replacement for `just`. It can parse any valid
`justfile`, and generated scripts behave identically to `just`. In almost all
cases, generated scripts even have byte-for-byte identical output to `just`.
There are over 32,000 tests (covering every line of code!) that validate this
compatibility for each commit.


# Install & Quick Start

``` bash
python3 -m pip install just-sh
```

Once installed, run `pyjust` to convert a `justfile` to a shell script. Then
run `./just.sh` as you would run `just`.

```
$ ls
justfile

$ just --summary
build lint test

$ pyjust

$ ls
justfile just.sh

$ ./just.sh --summary
build lint test
```

# Project Status

`just.sh` is "complete" software. I will fix bugs and make changes to matinain
compatibility with `just`, but there are no new features I plan to add. As
such, even if there have not been commits in a long time, the project is not
dead! Few commits means that everything has been running smoothly.

`just.sh` is written in Python with only one dependency outside the Python
standard library: [parsy](https://github.com/python-parsy/parsy). All of the
code is spread across two fairly small files (plus one more for tests), and
every line of code is covered by tests. These characteristics make it extremely
easy to maintain.


