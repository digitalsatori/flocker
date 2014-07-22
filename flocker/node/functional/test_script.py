# Copyright Hybrid Logic Ltd.  See LICENSE file for details.

"""
Functional tests for the ``flocker-changestate`` command line tool.
"""
from os import getuid, putenv, environ
from subprocess import check_output
from unittest import skipUnless

from twisted.python.procutils import which
from twisted.trial.unittest import TestCase
from twisted.python.filepath import FilePath
from twisted.internet import reactor

from ...volume.service import VolumeService, DEFAULT_CONFIG_PATH
from ...volume.filesystems.zfs import StoragePool
from ..script import ChangeStateScript
from ... import __version__


_require_installed = skipUnless(which("flocker-changestate"),
                                "flocker-changestate not installed")
_require_root = skipUnless(getuid() == 0,
                           "Root required to run these tests.")
from .test_gear import _if_gear_configured


class FlockerChangeStateTests(TestCase):
    """Tests for ``flocker-changestate``."""

    @_require_installed
    def setUp(self):
        pass

    def test_version(self):
        """
        ``flocker-changestate`` is a command available on the system path
        """
        putenv('CONFIG_PATH', self.mktemp())
        result = check_output([b"flocker-changestate"] + [b"--version"])
        self.assertEqual(result, b"%s\n" % (__version__,))


class ChangeStateScriptTests(TestCase):
    """
    Tests for ``ChangeStateScript``.

    XXX these tests overwrite the global volume manager config file:
    https://github.com/ClusterHQ/flocker/issues/301
    """
    @_require_root
    def setUp(self):
        pass

    def test_volume_service(self):
        """
        ``ChangeStateScript._deployer`` is created by default with a
        ``VolumeService``.
        """
        self.assertIsInstance(ChangeStateScript()._deployer._volume_service,
                              VolumeService)

    def test_volume_service_config_path(self):
        """
        ``ChangeStateScript._deployer`` is created by default with a
        ``VolumeService`` with the default config path.
        """
        self.assertEqual(
            ChangeStateScript()._deployer._volume_service._config_path,
            DEFAULT_CONFIG_PATH)

    def test_volume_service_custom_config_path(self):
        """
        ``ChangeStateScript._deployer`` is created with a ``VolumeService``
        with a config path which can be using with an environment variable.
        """
        path = self.mktemp()
        environ['CONFIG_PATH'] = path
        self.assertEqual(
            ChangeStateScript()._deployer._volume_service._config_path,
            FilePath(path))

    def test_volume_service_pool(self):
        """
        ``ChangeStateScript._deployer`` is created by default with a
        ``VolumeService`` whose pool is the default ZFS pool.
        """
        self.assertEqual(
            ChangeStateScript()._deployer._volume_service._pool,
            StoragePool(reactor, b"flocker", FilePath(b"/flocker")))

    @_if_gear_configured
    def test_deployer_gear_client(self):
        """
        ``ChangeState._deployer`` is configured with a gear client that works.
        """
        # Trial will fail the test if the returned Deferred fires with an
        # exception:
        return ChangeStateScript()._deployer._gear_client.list()
