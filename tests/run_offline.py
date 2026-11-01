"""Run the complete suite with outbound network and DNS blocked in Python.
Git subprocesses also inherit GIT_ALLOW_PROTOCOL='', disabling all transports.
No models, credentials, package installs or Internet access are needed.
"""
import os
import socket
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['GIT_ALLOW_PROTOCOL'] = ''
os.environ['GIT_NO_LAZY_FETCH'] = '1'
connect = socket.socket.connect
resolve = socket.getaddrinfo
LOCAL = {'127.0.0.1', '::1'}


def offline_connect(sock, address):
    if sock.family == socket.AF_UNIX or isinstance(address, tuple) and address[0] in LOCAL:
        return connect(sock, address)
    raise OSError('Offline test harness blocked outbound connection')


def offline_resolve(host, *args, **kwargs):
    if host not in LOCAL:
        raise OSError('Offline test harness blocked DNS')
    return resolve(host, *args, **kwargs)


socket.socket.connect = offline_connect
socket.getaddrinfo = offline_resolve
suite = unittest.defaultTestLoader.discover(str(Path(__file__).parent), pattern='test_*.py')
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
