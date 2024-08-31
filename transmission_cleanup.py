# -*- coding: utf-8 -*-
#
# Copyright (c) 2020~2999 - Cologler <skyoflw@gmail.com>
# ----------
#
# ----------

import collections
import hashlib
import json
import os
import shutil
import sys

import bencodepy
import requests
import transmission_rpc
from click import Abort, Context, echo
from click_anno import click_app
from click_anno.types import flag

# encoding is required.
# if we run this on synology task scheduler, by default sys.getdefaultencoding() is ascii.
# we may get file names from `os.listdir(incomplete_dir)` with bad encoding
# their did not match torrent.name, but we cannot remove them because they still need.
assert sys.getdefaultencoding() == 'utf-8', 'encoding is not utf-8'

class IncompleteItem:
    def __init__(self, incomplete_dir, name):
        self.path = os.path.join(incomplete_dir, name)
        if name.endswith('.part'):
            self.name = name[:-5]
        else:
            self.name = name

def collect_incomplete_items(incomplete_dir):
    return [
        IncompleteItem(incomplete_dir, x) for x in os.listdir(incomplete_dir)
    ]

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

    def cleanup_incompletedir(self, incomplete_dir, dryrun: bool):
        exists_nodes = collect_incomplete_items(incomplete_dir)

        names = set()
        for tor in self.tc.get_torrents(arguments=['id', 'name']):
            names.add(tor.name)

        for item in exists_nodes:
            if item.name not in names:
                remove(item.path, dryrun)

    def cleanup_torrentsdir(self, torrents_dir, dryrun: bool):
        try:
            tor_filenames = os.listdir(torrents_dir)
        except FileNotFoundError:
            echo(f'unable list file from {torrents_dir!r}.')
            return

        class _FileEntry:
            def __init__(self, name: str) -> None:
                self.name = name
                self.info_hash: str=None
                self.torrents = []

            @property
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
        echo(f'read {len(file_entries)} torrents from torrents dir.')

        drift_torrents = []
        torrents = self.tc.get_torrents(arguments=['id', 'hashString', 'torrentFile'])
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
        echo(f'read {len(torrents)} torrents from transmission.')

        remove_list: list[str] = []

        dupih = [(k, v) for k,v in file_entries_by_infohash.items() if len(v) > 1]
        if dupih:
            echo('the following *.torrent has the same info hash:')
            for k, v in dupih:
                echo(f'  - with info_hash({k!r})')
                for e in v:
                    linked = '&' if e.torrents else 'x'
                    echo(f'    - ({linked}) {e.name}')
                if sum(len(e.torrents) for e in v) == 1: # only one item linked
                    remove_list.extend(e.path for e in v if not e.torrents)

        if drift_torrents:
            echo('the following item missing torrent files:')
            for tor in drift_torrents:
                echo(f'  - {tor.hashString.lower()}')
        else:
            remove_list = [e.path for e in file_entries.values() if not e.torrents]
            new_items_count = len(torrents) + len(remove_list) - len(tor_filenames)
            if new_items_count == 0:
                for item in remove_list:
                    remove(item, dryrun=dryrun)
            else:
                echo(f'new {new_items_count} torrents added, abort!')

def load_conf(args: dict):
    conf_path = os.path.expanduser(
        os.path.join('~', '.config', 'transmission_cleanup', 'config.json')
    )
    conf_from_json = {}
    if os.path.isfile(conf_path):
        with open(conf_path, 'r') as fp:
            conf_from_json = json.load(fp)

    conf_from_env = {}
    address = os.getenv('TRANSMISSION_ADDRESS')
    if address is not None:
        conf_from_env['address'] = address
    port = os.getenv('TRANSMISSION_PORT')
    if port is not None:
        conf_from_env['port'] = int(port)
    incomplete_dir = os.getenv('TRANSMISSION_INCOMPLETEDIR')
    if incomplete_dir is not None:
        conf_from_env['incomplete_dir'] = incomplete_dir

    conf = collections.ChainMap(
        args,
        conf_from_env,
        conf_from_json
    )

    if not conf.get('address'):
        echo('Missing server value.')
        raise Abort()

    if not conf.get('port'):
        echo('Missing port value.')
        raise Abort()

    return conf

def read_trackers(src: str):
    resp = requests.get(src, timeout=10)
    resp.raise_for_status()
    return resp.text

@click_app
class App:
    def __init__(self, dryrun: flag) -> None:
        self._dryrun = dryrun
        self._conf = load_conf(dict(dryrun=dryrun))
        conn = {
            'host': self._conf['address'],
            'port': self._conf['port'],
        }
        self.tc = transmission_rpc.Client(**conn)
        self._helper = TransmissionHelper(self.tc)

    def cleanup_incompletedir(self):
        '''
        cleanup the incomplete dir if the item did not exists in the transmission.
        '''
        self._helper.cleanup_incompletedir(
            self._conf['incomplete_dir'],
            self._conf['dryrun']
        )

    def cleanup_torrentsdir(self, ctx: Context):
        '''
        cleanup the torrents dir if the torrent did not exists in the transmission.

        for more info, search 'deleted torrents keep coming back'
        '''
        conf_dir = self._conf.get('conf_dir')
        if conf_dir is None:
            ctx.fail('conf_dir is unset.')
        if not os.path.isdir(conf_dir):
            ctx.fail(f'conf_dir ({conf_dir!r}) is not a dir')
        torrents_dir = os.path.join(self._conf['conf_dir'], 'torrents')
        self._helper.cleanup_torrentsdir(torrents_dir, self._dryrun)

    def remove_finished(self, ctx: Context, delete_data: flag=False):
        '''
        remove all finished torrents.
        '''
        torrents = self._helper.tc.get_torrents(arguments=['id', 'status', 'isFinished', 'doneDate'])
        finished = []
        for torrent in torrents:
            if torrent.isFinished and torrent.status == 'stopped':
                finished.append(torrent.id)
            elif torrent.doneDate > 0:
                # files have been removed and the transmission have been restart
                finished.append(torrent.id)
        echo('total %d items will be remove' % len(finished))
        if finished and not self._dryrun:
            self._helper.tc.remove_torrent(finished, delete_data=bool(delete_data), timeout=None)

def main(argv=None):
    if argv is None:
        argv = sys.argv
    App()

if __name__ == '__main__':
    main()
