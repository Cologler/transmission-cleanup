# -*- coding: utf-8 -*-
#
# Copyright (c) 2020~2999 - Cologler <skyoflw@gmail.com>
# ----------
#
# ----------

import hashlib
import os
import shutil
import sys
from functools import cached_property
from typing import Annotated

import bencodepy
import rich
import transmission_rpc
from typer import Abort, Argument, Option, Typer

# encoding is required.
# if we run this on synology task scheduler, by default sys.getdefaultencoding() is ascii.
# we may get file names from `os.listdir(incomplete_dir)` with bad encoding
# their did not match torrent.name, but we cannot remove them because they still need.
assert sys.getdefaultencoding() == 'utf-8', 'encoding is not utf-8'

def compute_info_hash(d: dict):
    return hashlib.sha1(bencodepy.encode(d[b"info"])).hexdigest()

def remove(path: str, dryrun: bool):
    if os.path.isfile(path):
        try:
            if not dryrun:
                os.unlink(path)
            print('removed %s' % path)
        except FileNotFoundError:
            pass
    elif os.path.isdir(path):
        try:
            if not dryrun:
                shutil.rmtree(path)
            print('removed %s' % path)
        except FileNotFoundError:
            pass

class TransmissionHelper:
    def __init__(self, tc: transmission_rpc.Client) -> None:
        self.tc = tc


app = Typer(help='Transmission Cleanup')


@app.command(help='remove torrents from torrents dir if the torrent does not exists in the transmission.')
def cleanup_torrentsdir(
        host: Annotated[str, Argument(envvar="TRANSMISSION_HOST")],
        port: Annotated[int, Argument(envvar="TRANSMISSION_PORT", min=1, max=65535)],
        torrents_dir: Annotated[str, Argument(envvar="TRANSMISSION_TORRENTSDIR")],
        dry_run: Annotated[bool, Option('--dry-run', help='Only print what would be done.')] = False,
    ):

    tc = transmission_rpc.Client(host=host, port=port)

    try:
        tor_filenames = os.listdir(torrents_dir)
    except FileNotFoundError:
        rich.print(f'[red]unable list file from {torrents_dir!r}.[/]')
        raise Abort()

    class _FileEntry:
        def __init__(self, name: str) -> None:
            self.name = name
            self.info_hash: str=None
            self.torrents = []

        @cached_property
        def path(self):
            return os.path.join(torrents_dir, self.name)

    # get files from disk before get torrents from transmission
    # ensure no new torrents will be delete.
    file_entries: dict[str, _FileEntry] = {}
    file_entries_by_infohash: dict[str, list[_FileEntry]] = {}

    for name in tor_filenames:
        assert name not in file_entries
        file_entries[name] = fe = _FileEntry(name)

        path = os.path.join(torrents_dir, name)
        with open(path, 'rb') as f:
            tor_body = bencodepy.decode(f.read())
        magnet_info = tor_body.get(b'magnet-info')
        if magnet_info:
            # this is a magnet
            info_hash: str = magnet_info[b'info_hash'].hex()
        else:
            info_hash: str = compute_info_hash(tor_body)
        info_hash = info_hash.lower()

        fe.info_hash = info_hash
        file_entries_by_infohash.setdefault(info_hash, []).append(fe)
    rich.print(f'read {len(file_entries)} torrents from torrents dir.')

    drift_torrents = []
    torrents = tc.get_torrents(arguments=['id', 'hashString', 'torrentFile'])
    for tor in torrents:
        assert isinstance(tor.torrentFile, str)
        info_hash = tor.hashString.lower()
        tfn = os.path.basename(tor.torrentFile)
        fe = file_entries.get(tfn)
        if fe:
            fe.torrents.append(tor)
        else:
            if len(fels := file_entries_by_infohash.get(info_hash, ())) == 1:
                fels[0].torrents.append(tor)
            else:
                drift_torrents.append(tor)
    rich.print(f'read {len(torrents)} torrents from transmission.')

    remove_list: list[str] = []

    dupih = [(k, v) for k,v in file_entries_by_infohash.items() if len(v) > 1]
    if dupih:
        rich.print('the following *.torrent has the same info hash:')
        for k, v in dupih:
            rich.print(f'  - with info_hash({k!r})')
            for e in v:
                linked = '&' if e.torrents else 'x'
                rich.print(f'    - ({linked}) {e.name}')
            if sum(len(e.torrents) for e in v) == 1: # only one item linked
                remove_list.extend(e.path for e in v if not e.torrents)

    if drift_torrents:
        rich.print('the following item missing torrent files:')
        for tor in drift_torrents:
            rich.print(f'  - {tor.hashString.lower()}')
    else:
        remove_list = [e.path for e in file_entries.values() if not e.torrents]
        new_items_count = len(torrents) + len(remove_list) - len(tor_filenames)
        if new_items_count == 0:
            for item in remove_list:
                remove(item, dryrun=dry_run)
        else:
            rich.print(f'new {new_items_count} torrents added, abort!')


@app.command(help='remove incomplete files from incomplete dir if no torrent linked to it.')
def cleanup_incompletedir(
        host: Annotated[str, Argument(envvar="TRANSMISSION_HOST")],
        port: Annotated[int, Argument(envvar="TRANSMISSION_PORT", min=1, max=65535)],
        incomplete_dir: Annotated[str, Argument(envvar="TRANSMISSION_INCOMPLETEDIR")],
        dry_run: Annotated[bool, Option('--dry-run', help='Only print what would be done.')] = False,
    ):

    class IncompleteItem:
        def __init__(self, name):
            self.path = os.path.join(incomplete_dir, name)
            self.name = name[:-5] if name.endswith('.part') else name

    # must collect before fetch items from transmission client
    incomplete_items = [IncompleteItem(x) for x in os.listdir(incomplete_dir)]

    tc = transmission_rpc.Client(host=host, port=port)
    names = {tor.name for tor in tc.get_torrents(arguments=['id', 'name'])}

    not_exists = [x for x in incomplete_items if x.name not in names]

    rich.print(f'total {len(not_exists)} items will be remove.')
    rich.print('\n'.join(f'   {x.name}' for x in not_exists))
    for item in not_exists:
        remove(item.path, dry_run)


@app.command(help='remove all finished and stopped torrents.')
def remove_finished(
        host: Annotated[str, Argument(envvar="TRANSMISSION_HOST")],
        port: Annotated[int, Argument(envvar="TRANSMISSION_PORT", min=1, max=65535)],
        delete_data: Annotated[bool, Option('--delete-data', help='Delete downloaded files.')] = False,
        dry_run: Annotated[bool, Option('--dry-run', help='Only print what would be done.')] = False,
    ):

    def is_finished(torrent):
        # `.isFinished` is False for removed torrents
        return torrent.doneDate > 0 and torrent.status == 'stopped'

    tc = transmission_rpc.Client(host=host, port=port)
    torrents = tc.get_torrents(arguments=['id', 'name', 'status', 'isFinished', 'doneDate'])
    finished = [x for x in torrents if is_finished(x)]

    rich.print(f'total {len(finished)} items will be remove.')
    rich.print('\n'.join(f'   {x.name}' for x in finished))
    if not dry_run and finished:
        tc.remove_torrent([x.id for x in finished], delete_data=bool(delete_data), timeout=None)


def main(argv=None):
    if argv is None:
        argv = sys.argv

    app()

if __name__ == '__main__':
    main()
