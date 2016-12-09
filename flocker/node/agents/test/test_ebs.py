# Copyright ClusterHQ Inc.  See LICENSE file for details.

"""
Tests for ``flocker.node.agents.ebs``.
"""

from string import ascii_lowercase
from uuid import uuid4

from botocore.session import get_session as botocore_get_session
from botocore.stub import Stubber
from boto3.session import Session as Boto3Session

from hypothesis import given
from hypothesis.strategies import lists, sampled_from, builds

from bitmath import GiB

from twisted.python.filepath import FilePath

from eliot.testing import capture_logging, assertHasMessage

from ..ebs import (
    AttachedUnexpectedDevice, _expected_device,
    _attach_volume_and_wait_for_device, _get_blockdevices,
    _get_device_size, _wait_for_new_device, _find_allocated_devices,
    _select_free_device, NoAvailableDevice,
    _is_cluster_volume, CLUSTER_ID_LABEL
)
from .._logging import NO_NEW_DEVICE_IN_OS, INVALID_FLOCKER_CLUSTER_ID
from ..blockdevice import BlockDeviceVolume

from ....testtools import CustomException, TestCase, random_name


# A Hypothesis strategy for generating /dev/sd?
device_path = builds(
    lambda suffix: b"/dev/sd" + b"".join(suffix),
    suffix=lists(
        elements=sampled_from(ascii_lowercase), min_size=1, max_size=2
    ),
)


class AttachedUnexpectedDeviceTests(TestCase):
    """
    Tests for ``AttachedUnexpectedDevice``.
    """
    def test_repr(self):
        """
        The string representation of ``AttachedUnexpectedDevice`` includes the
        requested device name and the discovered device name.
        """
        requested = FilePath(b"/dev/sda")
        discovered = FilePath(b"/dev/sdb")
        expected = (
            "AttachedUnexpectedDevice("
            "requested='/dev/sda', discovered='/dev/sdb'"
            ")"
        )
        self.assertEqual(
            expected,
            repr(AttachedUnexpectedDevice(requested, discovered))
        )

    def test_nothing_discovered_repr(self):
        """
        If no device is discovered, the repr of ``AttachedUnexpectedDevice``
        shows this with a ``None`` value for ``discovered``.
        """
        requested = FilePath(b"/dev/sda")
        discovered = None
        expected = (
            "AttachedUnexpectedDevice(requested='/dev/sda', discovered=None)"
        )
        self.assertEqual(
            expected,
            repr(AttachedUnexpectedDevice(requested, discovered))
        )

    def test_wrong_requested_type(self):
        """
        If ``AttachedUnexpectedDevice.__init__`` is given something other than
        an instance of ``FilePath`` for the value of ``requested``,
        ``TypeError`` is raised.
        """
        self.assertRaises(
            TypeError, AttachedUnexpectedDevice, object(), FilePath(b"/")
        )

    def test_wrong_discovered_type(self):
        """
        If ``AttachedUnexpectedDevice.__init__`` is given something other than
        ``None`` or an instance of ``FilePath`` for the value of ``requested``,
        ``TypeError`` is raised.
        """
        self.assertRaises(
            TypeError, AttachedUnexpectedDevice, FilePath(b"/"), object(),
        )


class AttachVolumeAndWaitTests(TestCase):
    """
    Tests for ``_attach_volume_and_wait_for_device``.
    """
    def setUp(self):
        super(AttachVolumeAndWaitTests, self).setUp()
        self.volume = BlockDeviceVolume(
            dataset_id=uuid4(),
            blockdevice_id=u"opaque storage backend id",
            size=int(GiB(100).to_Byte()),
        )
        self.compute_id = u"opaque compute backend id"

    @given(device=device_path)
    def test_unexpected_attach_exception(self, device):
        """
        If the ``attach_volume`` function raises an unexpected exception, it is
        passed through.
        """
        def attach_volume(blockdevice_id, compute_id, device):
            raise CustomException("Fake failure generated by test")

        self.assertRaises(
            CustomException,
            _attach_volume_and_wait_for_device,
            volume=self.volume,
            attach_to=self.compute_id,
            attach_volume=attach_volume,
            detach_volume=lambda *a, **kw: None,
            device=device,
            blockdevices=[],
        )

    @given(device=device_path)
    def test_unexpected_device_discovered(self, device):
        """
        After attaching the volume, if a new device path is discovered that's
        not related to the path given by the ``device`` parameter in the
        expected way, ``AttachedUnexpectedDevice`` is raised giving details
        about the expected and received paths.
        """
        unexpected_device = []

        # The implementation is going to look at the real system to see what
        # block devices exist.  It would be nice to have an abstraction in
        # place to easily manipulate these results for the tests.  Lacking
        # that, just grab the actual block devices from the system and then
        # drop one to make it look like that one has just appeared as a result
        # of the attach operation.
        blockdevices = _get_blockdevices()

        # But there are complex criteria for selection.  So be careful which
        # device we select so as to fool the implementation.
        for wrong_device in blockdevices:
            # Don't pick the one that's actually the right result
            if wrong_device.basename().endswith(device[-1]):
                continue

            if wrong_device.basename().startswith((b"sd", b"xvd")):
                size = _get_device_size(wrong_device.basename())
                volume = self.volume.set("size", size)
                blockdevices.remove(wrong_device)
                unexpected_device.append(wrong_device)
                break
        else:
            # Ideally we'd have more control over the implementation so we
            # wouldn't have to give up when running on a system lacking just
            # the right devices to let us exercise the code we want to
            # exercise.  Getting to that point involves fixing all of the
            # things in ebs.py like _get_blockdevices and _get_device_size that
            # pass around raw strings or FilePath instances representing
            # devices and then go off and interact directly with the underlying
            # system.
            #
            # We need control over (at least) what devices the code-under-test
            # can discover and what size it thinks they are.  Until then we
            # just have to give up.  If you can't get this test to run without
            # skipping, try adding another block device to your system (eg plug
            # in a usb device).
            #
            # With apologies,
            #  -jean-paul
            raise self.skipTest(
                "Could not find a suitable device to use as a bad device."
            )

        exception = self.assertRaises(
            AttachedUnexpectedDevice,
            _attach_volume_and_wait_for_device,
            volume=volume,
            attach_to=self.compute_id,
            attach_volume=lambda *a, **kw: None,
            detach_volume=lambda *a, **kw: None,
            device=device,
            blockdevices=blockdevices,
        )
        self.assertEqual(
            AttachedUnexpectedDevice(
                requested=FilePath(device),
                discovered=FilePath(b"/dev/").child(
                    unexpected_device.pop().basename()
                ),
            ),
            exception,
        )


class ExpectedDeviceTests(TestCase):
    """
    Tests for ``_expected_device``.
    """
    def test_sdX_to_xvdX(self):
        """
        ``sdX``-style devices are rewritten to corresponding ``xvdX`` devices.
        """
        self.assertEqual(
            (FilePath(b"/dev/xvdj"), FilePath(b"/dev/xvdo")),
            (_expected_device(b"/dev/sdj"), _expected_device(b"/dev/sdo")),
        )

    def test_non_dev_rejected(self):
        """
        Devices not in ``/dev`` are rejected with ``ValueError``.
        """
        self.assertRaises(
            ValueError,
            _expected_device, b"/sys/block/sda",
        )

    def test_non_sdX_rejected(self):
        """
        Devices not in the ``sdX`` category are rejected with ``ValueError``.
        """
        self.assertRaises(
            ValueError,
            _expected_device, b"/dev/hda",
        )


class WaitForNewDeviceTests(TestCase):
    """
    Tests for ``_wait_for_new_device``.
    """
    @capture_logging(assertHasMessage, NO_NEW_DEVICE_IN_OS)
    def test_no_new_device_logged(self, logger):
        """
        ``NO_NEW_DEVICE_IN_OS`` is logged if a new device does not appear.
        """
        self.assertIs(
            None,
            _wait_for_new_device(
                base=[],
                expected_size=1,
                time_limit=0,
            )
        )


class FindAllocatedDeviceTests(TestCase):
    """
    Tests for finding allocated devices.
    """

    def test_returns_device_name_list(self):
        """
        Returns at least one device (the root device).  All returned
        values are existing devices.
        """
        devices = _find_allocated_devices()
        self.assertGreater(len(devices), 0)
        self.assertTrue(
            all(FilePath('/dev/{}'.format(d)).exists() for d in devices)
        )


class SelectFreeDeviceTests(TestCase):
    """
    Tests for selecting new device.
    """

    def test_provides_device_name(self):
        """
        Return a device name.
        """
        self.assertTrue(_select_free_device(['sda']).startswith(u'/dev/'))

    def test_all_devices_used(self):
        """
        Raises exception if no available device names.
        """
        existing = ['sd' + ch for ch in ascii_lowercase]
        self.assertRaises(NoAvailableDevice, _select_free_device, existing)


def boto_volume_for_test(test, cluster_id):
    """
    Create an in-memory boto3 Volume, avoiding any AWS API calls.
    """
    # Create a session directly rather than allow lazy loading of a default
    # session.
    region_name = u"some-test-region-1"
    s = Boto3Session(
        botocore_session=botocore_get_session(),
        region_name=region_name,
    )
    ec2 = s.resource("ec2", region_name=region_name)
    stubber = Stubber(ec2.meta.client)
    # From this point, any attempt to interact with AWS API should fail with
    # botocore.exceptions.StubResponseError
    stubber.activate()
    volume_id = u"vol-{}".format(random_name(test))
    v = ec2.Volume(id=volume_id)
    tags = []
    if cluster_id is not None:
        tags.append(
            dict(
                Key=CLUSTER_ID_LABEL,
                Value=cluster_id,
            ),
        )
    # Pre-populate the metadata to prevent any attempt to load the metadata by
    # API calls.
    v.meta.data = dict(
        Tags=tags
    )
    return v


class IsClusterVolumeTests(TestCase):
    """
    Tests for ``_is_cluster_volume``.
    """
    def test_missing_cluster_id(self):
        """
        Volumes without a flocker-cluster-id Tag are ignored.
        """
        self.assertFalse(
            _is_cluster_volume(
                cluster_id=uuid4(),
                ebs_volume=boto_volume_for_test(
                    test=self,
                    cluster_id=None,
                )
            )
        )

    def test_foreign_cluster_id(self):
        """
        Volumes with an unexpected flocker-cluster-id Tag are ignored.
        """
        self.assertFalse(
            _is_cluster_volume(
                cluster_id=uuid4(),
                ebs_volume=boto_volume_for_test(
                    test=self,
                    cluster_id=unicode(uuid4()),
                )
            )
        )

    @capture_logging(assertHasMessage, INVALID_FLOCKER_CLUSTER_ID)
    def test_invalid_cluster_id(self, logger):
        """
        Volumes that have an non-uuid4 flocker-cluster-id are ignored.
        The invalid flocker-cluster-id is logged.
        """
        bad_cluster_id = u"An invalid flocker-cluster-id"
        self.assertFalse(
            _is_cluster_volume(
                cluster_id=uuid4(),
                ebs_volume=boto_volume_for_test(
                    test=self,
                    cluster_id=bad_cluster_id,
                )
            )
        )

    def test_valid_cluster_id(self):
        """
        Volumes that have the expected uuid4 flocker-cluster-id are identified.
        """
        cluster_id = uuid4()
        self.assertTrue(
            _is_cluster_volume(
                cluster_id=cluster_id,
                ebs_volume=boto_volume_for_test(
                    test=self,
                    cluster_id=unicode(cluster_id),
                )
            )
        )
