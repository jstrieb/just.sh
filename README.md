# just.sh

`just.sh` transpiles [justfiles](https://github.com/casey/just) to portable,
POSIX-compatible shell scripts. 

- Run `just` commands without installing `just` 
- Run `just` commands in CI
- Build a shell script from a `justfile`


# Install & Quick Start

``` bash
python3 -m pip install just-sh
```

Once installed, run `just.sh` to convert the `justfile` to a shell script. Then
run `./just.sh` as you would run `just`.

```
$ ls
justfile

$ just --summary
build lint test

$ just.sh

$ ls
justfile just.sh

$ ./just.sh --summary
build lint test
```

`just.sh` is written in Python with only one dependency:
[parsy](https://github.com/python-parsy/parsy). 


