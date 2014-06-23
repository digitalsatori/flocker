# Copyright Hybrid Logic Ltd.  See LICENSE file for details.

"""Various utilities to help with unit and functional testing."""

from __future__ import absolute_import

import io
import sys
from collections import namedtuple
from random import random

from zope.interface import implementer
from zope.interface.verify import verifyClass

from twisted.internet.interfaces import IProcessTransport, IReactorProcess
from twisted.internet.task import Clock, deferLater
from twisted.internet.defer import maybeDeferred
from twisted.internet import reactor

from . import __version__
from .common.script import (
    FlockerScriptRunner, ICommandLineScript)


@implementer(IProcessTransport)
class FakeProcessTransport(object):
    """
    Mock process transport to observe signals sent to a process.

    @ivar signals: L{list} of signals sent to process.
    """

    def __init__(self):
        self.signals = []


    def signalProcess(self, signal):
        self.signals.append(signal)



class SpawnProcessArguments(namedtuple('ProcessData',
                  'processProtocol executable args env path '
                  'uid gid usePTY childFDs transport')):
    """
    Object recording the arguments passed to L{FakeProcessReactor.spawnProcess}
    as well as the L{IProcessTransport} that was connected to the protocol.

    @ivar transport: Fake transport connected to the protocol.
    @type transport: L{IProcessTransport}

    @see L{twisted.internet.interfaces.IReactorProcess.spawnProcess}
    """



@implementer(IReactorProcess)
class FakeProcessReactor(Clock):
    """
    Fake reactor implmenting process support.

    @ivar processes: List of process that have been spawned
    @type processes: L{list} of L{SpawnProcessArguments}.
    """

    def __init__(self):
        Clock.__init__(self)
        self.processes = []


    def timeout(self):
        if self.calls:
            return max(0, self.calls[0].getTime() - self.seconds())
        return 0


    def spawnProcess(self, processProtocol, executable, args=(), env={},
                     path=None, uid=None, gid=None, usePTY=0, childFDs=None):
        transport = FakeProcessTransport()
        self.processes.append(SpawnProcessArguments(
            processProtocol, executable, args, env, path, uid, gid, usePTY,
            childFDs, transport=transport))
        processProtocol.makeConnection(transport)
        return transport


verifyClass(IReactorProcess, FakeProcessReactor)


def loop_until(arg, predicate):
    """Call predicate every 0.1 seconds, until it returns ``True``.

    This should only be used in functional tests.

    :param arg: Value to return.
    :param predicate: Callable returning termination condition.
    :type predicate: 0-argument callable returning a Deferred.

    :return: A ``Deferred`` firing with ``arg``
    """
    d = maybeDeferred(predicate)
    def loop(result):
        if not result:
            d = deferLater(reactor, 0.1, predicate)
            d.addCallback(loop)
            return d
        return arg
    d.addCallback(loop)
    return d


def random_name():
    """Return a short, random name.

    :return name: A random ``unicode`` name.
    """
    return u"%d" % (int(random() * 1e12),)


def help_problems(command_name, help_text):
    """Identify and return a list of help text problems.

    :param unicode command_name: The name of the command which should appear in
        the help text.
    :param bytes help_text: The full help text to be inspected.
    :return: A list of problems found with the supplied ``help_text``.
    :rtype: list
    """
    problems = []
    expected_start = u'Usage: {command}'.format(
        command=command_name).encode('utf8')
    if not help_text.startswith(expected_start):
        problems.append(
            'Does not begin with {expected}. Found {actual} instead'.format(
                expected=repr(expected_start),
                actual=repr(help_text[:len(expected_start)])
            )
        )
    return problems


class FakeSysModule(object):
    """A ``sys`` like substitute.

    For use in testing the handling of `argv`, `stdout` and `stderr` by command
    line scripts.

    :ivar list argv: See ``__init__``
    :ivar stdout: A :py:class:`io.BytesIO` object representing standard output.
    :ivar stderr: A :py:class:`io.BytesIO` object representing standard error.
    """
    def __init__(self, argv=None):
        """Initialise the fake sys module.

        :param list argv: The arguments list which should be exposed as
            ``sys.argv``.
        """
        if argv is None:
            argv = []
        self.argv = argv
        # io.BytesIO is not quite the same as sys.stdout/stderr
        # particularly with respect to unicode handling.  So,
        # hopefully the implementation doesn't try to write any
        # unicode.
        self.stdout = io.BytesIO()
        self.stderr = io.BytesIO()


class FlockerScriptTestsMixin(object):
    """Common tests for scripts that can be run via L{FlockerScriptRunner}

    :ivar ICommandLineScript script: The script class under test.
    :ivar usage.Options options: The options parser class to use in the test.
    :ivar text command_name: The name of the command represented by ``script``.
    """

    script = None
    options = None
    command_name = None

    def test_interface(self):
        """
        A script that is meant to be run by ``FlockerScriptRunner`` must
        implement ``ICommandLineScript``.
        """
        self.assertTrue(verifyClass(ICommandLineScript, self.script))

    def test_incorrect_arguments(self):
        """
        ``FlockerScriptRunner.main`` exits with status 1 and prints help to
        `stderr` if supplied with unexpected arguments.
        """
        sys = FakeSysModule(argv=[self.command_name, b'--unexpected_argument'])
        script = FlockerScriptRunner(
            reactor=None, script=self.script(), options=self.options(),
            sys_module=sys)
        error = self.assertRaises(SystemExit, script.main)
        error_text = sys.stderr.getvalue()
        self.assertEqual(
            (1, []),
            (error.code, help_problems(self.command_name, error_text))
        )


class StandardOptionsTestsMixin(object):
    """Tests for classes decorated with ``flocker_standard_options``.

    Tests for the standard options that should be available on every flocker
    command.

    :ivar usage.Options options: The ``usage.Options`` class under test.
    """
    options = None

    def test_sys_module_default(self):
        """
        ``flocker_standard_options`` adds a ``_sys_module`` attribute which is
        ``sys`` by default.
        """
        self.assertIs(sys, self.options()._sys_module)

    def test_sys_module_override(self):
        """
        ``flocker_standard_options`` adds a ``sys_module`` argument to the
        initialiser which is assigned to ``_sys_module``.
        """
        dummy_sys_module = object()
        self.assertIs(
            dummy_sys_module,
            self.options(sys_module=dummy_sys_module)._sys_module
        )

    def test_version(self):
        """
        Flocker commands have a `--version` option which prints the current
        version string to stdout and causes the command to exit with status
        `0`.
        """
        sys = FakeSysModule()
        error = self.assertRaises(
            SystemExit,
            self.options(sys_module=sys).parseOptions,
            ['--version']
        )
        self.assertEqual(
            (__version__ + '\n', 0),
            (sys.stdout.getvalue(), error.code)
        )

    def test_verbosity_default(self):
        """
        Flocker commands have `verbosity` of `0` by default.
        """
        options = self.options()
        self.assertEqual(0, options['verbosity'])

    def test_verbosity_option(self):
        """
        Flocker commands have a `--verbose` option which increments the
        configured verbosity by `1`.
        """
        options = self.options()
        options.parseOptions(['--verbose'])
        self.assertEqual(1, options['verbosity'])

    def test_verbosity_option_short(self):
        """
        Flocker commands have a `-v` option which increments the configured
        verbosity by 1.
        """
        options = self.options()
        options.parseOptions(['-v'])
        self.assertEqual(1, options['verbosity'])

    def test_verbosity_multiple(self):
        """
        `--verbose` can be supplied multiple times to increase the verbosity.
        """
        options = self.options()
        options.parseOptions(['-v', '--verbose'])
        self.assertEqual(2, options['verbosity'])
