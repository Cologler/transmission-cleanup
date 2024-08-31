# transmission-cleanup

A helper tool to cleanup transmission files.

## Usage

``` shell
 Usage: transmission_cleanup.py [OPTIONS] COMMAND [ARGS]...

 Transmission Cleanup

╭─ Options ────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│ --install-completion          Install completion for the current shell.                                              │
│ --show-completion             Show completion for the current shell, to copy it or customize the installation.       │
│ --help                        Show this message and exit.                                                            │
╰──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
╭─ Commands ───────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│ cleanup-incompletedir   remove incomplete files from incomplete dir if no torrent linked to it.                      │
│ cleanup-torrentsdir     remove torrents from torrents dir if the torrent does not exists in the transmission.        │
│ remove-finished         remove all finished and stopped torrents.                                                    │
╰──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
```
