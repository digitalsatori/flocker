Vagrant
=======

There is a :file:`Vagrantfile` in the base of the repository,
that is preinstalled with all of the dependencies required to run flocker.

See the `vagrant documentation <http://docs.vagrantup.com/v2/>`_ for more details.

Base Image
----------

The box the above :file:`Vagrantfile` is based on is generated from :file:`vagrant/base/Vagrantfile`.
The box is initialized with the yum repositories for zfs and for dependencies not available in fedora,
and install all the dependencies besides zfs.

To build the box, run the following commands in the :file:`vagrant/base` directory::

   vagrant up
   vagrant package
   vagrant destroy

This will generate a :file:`package.box`.
To share the box, upload the file somewhere, and add a version on `vagrantcloud <https://vagrantcloud.com>`_.
