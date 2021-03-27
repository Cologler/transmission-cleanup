# -*- coding: utf-8 -*-
#
# Copyright (c) 2020~2999 - Cologler <skyoflw@gmail.com>
# ----------
#
# ----------

from typing import *
import os
import sys
import traceback
import collections
import json
import shutil
import hashlib

import bencodepy
import transmissionrpc
from click import Context, echo
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
    def __init__(self, tc: transmissionrpc.Client) -> None:
        self._tc = tc

    def cleanup_incompletedir(self, incomplete_dir, dryrun: bool):
        exists_nodes = collect_incomplete_items(incomplete_dir)

        names = set()
        for tor in self._tc.get_torrents():
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

        # get files from disk before get torrents from transmission
        # ensure no new torrents will be delete.
        info_hash_map = {}
        dup_tor_map = {}
        for name in tor_filenames:
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
            if info_hash in info_hash_map:
                dup_tor_map.setdefault(info_hash, [info_hash_map[info_hash]]).append(name)
            info_hash_map[info_hash] = path
        echo(f'read {len(info_hash_map)} torrents from torrents dir.')

        for info_hash in dup_tor_map:
            echo(f'same info hash ({info_hash!r}) in multi-file:')
            for name in dup_tor_map[info_hash]:
                echo(f'  - {name}')

        biths = set()
        for tor in self._tc.get_torrents():
            info_hash = tor.hashString.lower()
            biths.add(info_hash)
        echo(f'read {len(biths)} torrents from transmission.')

        if biths.issubset(info_hash_map.keys()):
            for info_hash in info_hash_map:
                if info_hash not in biths:
                    remove(info_hash_map[info_hash], dryrun=dryrun)
        else:
            for info_hash in biths:
                if info_hash not in info_hash_map:
                    echo(f'info hash {info_hash!r} is not exists in torrents_dir.')

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

    return collections.ChainMap(
        args,
        conf_from_env,
        conf_from_json
    )

@click_app
class App:
    def __init__(self, dryrun: flag) -> None:
        self._dryrun = dryrun
        self._conf = load_conf(dict(dryrun=dryrun))
        address = self._conf['address']
        port = self._conf['port']
        self._tc = transmissionrpc.Client(address, port)
        self._helper = TransmissionHelper(self._tc)

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

def main(argv=None):
    if argv is None:
        argv = sys.argv
    try:
        App()
    except Exception: # pylint: disable=W0703
        traceback.print_exc()
        if sys.stderr.isatty(): input('wait for read...')

if __name__ == '__main__':
    main()
