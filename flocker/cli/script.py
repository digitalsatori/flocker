# Copyright Hybrid Logic Ltd.  See LICENSE file for details.

"""The command-line ``flocker-deploy`` tool."""
from twisted.internet.defer import DeferredList
from twisted.internet.threads import deferToThread
from twisted.python.filepath import FilePath
from twisted.python.usage import Options, UsageError

from zope.interface import implementer

from yaml import safe_load

from ..common.script import (flocker_standard_options, ICommandLineScript,
                             FlockerScriptRunner)
from ..node import ConfigurationError, model_from_configuration

from ..volume._ipc import ProcessNode
from ._sshconfig import DEFAULT_SSH_DIRECTORY, OpenSSHConfiguration

from effect import Effect, parallel


@flocker_standard_options
class DeployOptions(Options):
    """
    Command line options for ``flocker-deploy``.

    :raises ValueError: If either file supplied does not exist.
    """
    longdesc = """flocker-deploy allows you to configure existing nodes.

    """

    synopsis = ("Usage: flocker-deploy [OPTIONS] "
                "DEPLOYMENT_CONFIGURATION_PATH APPLICATION_CONFIGURATION_PATH")

    def parseArgs(self, deployment_config, application_config):
        deployment_config = FilePath(deployment_config)
        application_config = FilePath(application_config)

        if not deployment_config.exists():
            raise UsageError('No file exists at {path}'
                             .format(path=deployment_config.path))

        if not application_config.exists():
            raise UsageError('No file exists at {path}'
                             .format(path=application_config.path))

        deployment_config = safe_load(deployment_config.getContent())
        application_config = safe_load(application_config.getContent())
        try:
            self['deployment'] = model_from_configuration(
                application_configuration=application_config,
                deployment_configuration=deployment_config)
        except ConfigurationError as e:
            raise UsageError(str(e))


@implementer(ICommandLineScript)
class DeployScript(object):
    """
    A script to start configured deployments, covered by:
       https://github.com/ClusterHQ/flocker/issues/19
    """
    def __init__(self, ssh_configuration=None, ssh_port=22):
        if ssh_configuration is None:
            ssh_configuration = OpenSSHConfiguration.defaults()
        self.ssh_configuration = ssh_configuration
        self.ssh_port = ssh_port

    def _configure_ssh(self, deployment):
        """
        :return: A ``Deferred`` which fires when all nodes have been configured
            with ssh keys.
        """
        results = []
        for node in deployment.nodes:
            results.append(
                deferToThread(
                    self.ssh_configuration.configure_ssh,
                    node.hostname, self.ssh_port
                )
            )
        return DeferredList(results)

    def main(self, reactor, options):
        """
        See :py:meth:`ICommandLineScript.main` for parameter documentation.

        :return: A ``Deferred`` which fires when the deployment is complete or
                 has encountered an error.
        """
        return self._configure_ssh(options['deployment'])

    def _get_destinations(self, deployment):
        """
        Return sequence of ``INode`` to connect to for given deployment.

        :param Deployment deployment: The requested already parsed
            configuration.

        :return: Iterable of ``INode`` providers.
        """
        # private_key = DEFAULT_SSH_DIRECTORY.child(b"id_rsa_flocker")

        # for node in deployment.nodes:
        #     yield ProcessNode.using_ssh(node.hostname, 22, b"root",
        #                                 private_key)

    def _changestate_on_nodes(self, deployment, deployment_config,
                              application_config):
        """
        Connect to all nodes and run ``flocker-changestate``.

        :param Deployment deployment: The requested already parsed
            configuration.
        :param bytes deployment_config: YAML-encoded deployment configuration.
        :param bytes application_config: YAML-encoded application configuration.
        """
        # return parallel(list(
        #         Effect(
        #             ChangeState(
        #                 deployment_config=deployment_config,
        #                 application_config=application_config,
        #                 destination=destination))
        #             for destination in self._get_destinations(deployment)))


from characteristic import attributes

@attributes(["deployment_config", "application_config", "destination"])
class ChangeState(object):
    def perform_effect(self, dispatcher):
        return self.destination.get_output([
            b"flocker-changestate",
            self.deployment_config, self.application_config])



def flocker_deploy_main():
    return FlockerScriptRunner(
        script=DeployScript(),
        options=DeployOptions()
    ).main()
