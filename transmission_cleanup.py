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
from pathlib import Path
from itertools import groupby
from dataclasses import dataclass
import re

import bencodepy
import rich
import transmission_rpc
from typer import Abort, Argument, Option, Typer

# encoding is required.
# if we run this on synology task scheduler, by default sys.getdefaultencoding() is ascii.
# we may get file names from `os.listdir(incomplete_dir)` with bad encoding
# their did not match torrent.name, but we cannot remove them because they still need.
assert sys.getdefaultencoding() == 'utf-8', 'encoding is not utf-8'

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

def compute_info_hash(d: dict):
    return hashlib.sha1(bencodepy.encode(d[b"info"])).hexdigest()

def torrent_get_infohash(path: Path) -> str:
    # get infohash from transmission .torrent file
    content = bencodepy.decode(path.read_bytes())
    if magnet_info := content.get(b'magnet-info'):
        # this is a magnet
        info_hash: str = magnet_info[b'info_hash'].hex()
    else:
        info_hash: str = compute_info_hash(content)
    return info_hash.lower()


app = Typer(help='Transmission Cleanup')


@app.command(help='remove torrents from torrents dir if the torrent does not exists in the transmission.')
def cleanup_torrentsdir(
        host: Annotated[str, Argument(envvar="TRANSMISSION_HOST")],
        port: Annotated[int, Argument(envvar="TRANSMISSION_PORT", min=1, max=65535)],
        torrents_dir: Annotated[str, Argument(envvar="TRANSMISSION_TORRENTSDIR")],
        dry_run: Annotated[bool, Option('--dry-run', help='Only print what would be done.')] = False,
    ):

    @dataclass
    class LocalTorrentFile:
        path: Path
        info_hash: str

        @cached_property
        def name(self):
            return self.path.name

    tc = transmission_rpc.Client(host=host, port=port)

    # get files from disk before get torrents from transmission
    # ensure no new torrents will be delete.
    rich.print(f'reading torrents from {torrents_dir} ...')
    local_torrents_files = list(Path(torrents_dir).iterdir())

    local_torrents: list[LocalTorrentFile] = []

    for item in local_torrents_files:
        if item.is_file():
            if item.suffix == '.torrent':
                info_hash = torrent_get_infohash(item)

            elif item.suffix == '.magnet':
                # this is a magnet
                content = item.read_text()
                if match := re.search(r'xt=urn:btih:(?P<ih>[0-9a-f]+)(?:&|$)', content, re.IGNORECASE):
                    info_hash = match.group('ih').lower()
                else:
                    rich.print(f'[red]Unknown infohash from magnet: {content!r}[/]')
                    raise Abort()

            else:
                rich.print(f'[red]unknown file type: {item.name!r}[/]')
                raise Abort()

            local_torrents.append(LocalTorrentFile(item, info_hash))

    rich.print(f'Load {len(local_torrents_files)} torrents from torrents dir.')

    server_torrents = tc.get_torrents(arguments=['id', 'hashString', 'torrentFile'])
    rich.print(f'Fetch {len(server_torrents)} torrents from transmission server.')

    server_torrents_infohash = {x.hashString.lower(): x for x in server_torrents}
    def is_local_in_server(t: LocalTorrentFile):
        if t.info_hash is not None and t.info_hash in server_torrents_infohash:
            return True
        return False

    local_torrents_infohashs = {x.info_hash for x in local_torrents}
    local_torrents_names = {x.name for x in local_torrents}
    def is_server_in_local(t: transmission_rpc.Torrent):
        if os.path.basename(t.torrentFile) in local_torrents_names:
            return True
        if info_hash in local_torrents_infohashs:
            return True
        return False

    if notin_local := [x for x in server_torrents if not is_server_in_local(x)]:
        rich.print('The following item missing local torrent files:')
        for x in notin_local:
            rich.print(f'   - {x.hashString.lower()}')
    else:
        remove_list = [e.path for e in local_torrents if not is_local_in_server(e)]
        if not remove_list:
            rich.print('All local torrents are linked to server task.')
            return
        new_items_count = len(server_torrents) + len(remove_list) - len(local_torrents_files)
        if new_items_count == 0:
            for item in remove_list:
                remove(item, dryrun=dry_run)
        else:
            rich.print(f'Found {new_items_count} new torrents added, abort!')
            raise Abort()


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
