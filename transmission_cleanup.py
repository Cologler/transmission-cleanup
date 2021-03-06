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

import transmissionrpc

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

def cleanup(address, port, incomplete_dir, test: bool):
    exists_nodes = collect_incomplete_items(incomplete_dir)

    tc = transmissionrpc.Client(address, port)
    names = set()
    for tor in tc.get_torrents():
        names.add(tor.name)

    for item in exists_nodes:
        if item.name not in names:
            if os.path.isfile(item.path):
                try:
                    if not test:
                        os.unlink(item.path)
                    print('removed %s' % item.path)
                except FileNotFoundError:
                    pass
            elif os.path.isdir(item.path):
                try:
                    if not test:
                        shutil.rmtree(item.path)
                    print('removed %s' % item.path)
                except FileNotFoundError:
                    pass

def load_conf(argv: List[str]):
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

    conf_from_argv = {}
    try:
        argv.remove('--test')
        conf_from_argv['test'] = True
    except ValueError:
        conf_from_argv['test'] = False

    if argv:
        exit(f'unknown args: {conf_from_argv}')

    return collections.ChainMap(
        conf_from_argv,
        conf_from_env,
        conf_from_json
    )

def main(argv=None):
    if argv is None:
        argv = sys.argv
    try:
        cleanup(**load_conf(argv[1:]))
    except: # pylint: disable=W0703
        traceback.print_exc()
        if sys.stderr.isatty(): input('wait for read...')

if __name__ == '__main__':
    main()
