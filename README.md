# transmission-cleanup

cleanup transmission incomplete files.

## Usage

1. create a config json file `~/.config/transmission_cleanup/config.json`.
1. run `poetry install` to install deps.
1. run `poetry run python transmission_cleanup.py` to cleanup.

## Config

`~/.config/transmission_cleanup/config.json`:

``` json
{
    "address": ...,
    "port": ...,
    "incomplete_dir": ...
}
```
