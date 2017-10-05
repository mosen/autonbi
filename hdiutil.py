import os.path
import subprocess
import plistlib
import logging
import tempfile

logger = logging.getLogger(__name__)


class HDIUtilError(Exception):
    def __init__(self, message, return_code=None, command=None, stdout=None, stderr=None):
        """Error thrown when hdiutil exits unsuccessfully.

        :param message: Exception message.
        :param return_code: The return status of hdiutil.
        :param command: An array representing the command passed to subprocess.Popen()
        :param stdout: The stdout returned by hdiutil.
        :param stderr: The stderr returned by hdiutil.
        """
        super(HDIUtilError, self).__init__(message)
        self.return_code = return_code
        self.command = command
        self.stdout = stdout
        self.stderr = stderr

    def __str__(self):
        return "Executing: {0}, Returns {1}. {2}".format(' '.join(self.command), self.return_code, self.stderr)


class Dmg(object):

    HDIUTIL = '/usr/bin/hdiutil'

    @property
    def path(self):
        """The path of this dmg."""
        return self._path

    @property
    def mount_points(self):
        """The mount point of this dmg, None if it is not mounted/attached."""
        return self._mount_points

    @property
    def shadow(self):
        """If the dmg is mounted with a shadow file, return that path."""
        return self._shadow

    @shadow.setter
    def shadow(self, value):
        self._shadow = value

    def __init__(self, path):
        """Dmg represents an unmounted, existing .dmg on the filesystem.

        :param path: Path to an existing .dmg file
        """
        super(Dmg, self).__init__()
        self._mounted = False
        self._path = path
        self._mount_points = None
        self._shadow = None

    def mount(self, shadow=False):
        """Mount the .dmg

        You can also use the context manager method .mounted()

        :param shadow: Mount with a shadow file, for writing modifications to NetInstall.dmg
        :return: None
        """
        command = [Dmg.HDIUTIL, 'attach', self.path,
                   '-mountrandom', '/tmp', '-nobrowse', '-plist',
                   '-owners', 'on']

        if shadow:
            shadow_name = os.path.basename(self.path) + '.shadow'
            shadow_root = os.path.dirname(self.path)
            shadow_path = os.path.join(shadow_root, shadow_name)
            self._shadow = shadow_path
            command.extend(['-shadow', shadow_path])

        logger.debug(' '.join(command))
        proc = subprocess.Popen(command, bufsize=-1, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        (stdout, stderr) = proc.communicate()
        if proc.returncode:
            raise HDIUtilError(
                "Failed to mount dmg",
                return_code=proc.returncode,
                stdout=stdout, stderr=stderr,
                command=command
            )

        plist = plistlib.readPlistFromString(stdout)

        self._mount_points = [entity['mount-point'] for entity in plist['system-entities'] if 'mount-point' in entity]
        self._mounted = True

    def unmount(self, mount_point=None):
        """Unmount the .dmg

        :param mount_point: Unmount dmg at a specific mount point. Defaults to this .dmg's mount point.
        :return:
        """

        try:
            if mount_point is None:
                mount_point = self.mount_points[0]

            command = [Dmg.HDIUTIL, 'detach', mount_point]
            logger.debug(' '.join(command))
            proc = subprocess.Popen(command, bufsize=-1, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            (stdout, stderr) = proc.communicate()

            if proc.returncode:
                raise HDIUtilError(
                    "Failed to unmount dmg",
                    return_code=proc.returncode,
                    stdout=stdout, stderr=stderr,
                    command=command
                )
        except HDIUtilError as err:
            logger.debug("Polite unmount failed: ", err)
            logger.debug("Attempting to force unmount ", mount_point)

            command = ['/usr/bin/hdiutil', 'detach', '-force', mount_point]
            logger.debug(' '.join(command))
            return_code = subprocess.call(command)

            if return_code:
                raise HDIUtilError(
                    "Failed to unmount dmg",
                    return_code=return_code,
                    command=command
                )

        self._mounted = False

    def convert(self, output, fmt='UDSP'):
        """Convert a DMG with or without a shadow file to the specified format.

        :param output: The output path of the converted dmg. hdiutil will add extension.
        :param fmt: The -format parameter to hdiutil.
        :return Dmg: The instance of the output dmg.
        :throws HDIUtilError:
        """
        if self.shadow:
            # Run a basic 'hdiutil convert' using the shadow file to pick up
            # any changes we made without needing to convert between r/o and r/w
            command = [Dmg.HDIUTIL, 'convert',
                       '-format', fmt,
                       '-o', output,
                       '-shadow', self.shadow,
                       self.path]
        else:
            command = [Dmg.HDIUTIL, 'convert',
                       '-format', fmt,
                       '-o', output,
                       self.path]

        logger.debug(' '.join(command))
        proc = subprocess.Popen(command, bufsize=-1, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        (stdout, stderr) = proc.communicate()

        if proc.returncode:
            raise HDIUtilError(
                "Error attempting to convert dmg",
                stdout=stdout, stderr=stderr,
                return_code=proc.returncode, command=command)

        return Dmg(output)

    def mounted(self, writable=False):
        """Return a context manager for this dmg to mount and unmount.

        :param writable: mount with a shadow file to make r/o dmg's writable.
        :return: MountedDMG
        """
        return MountedDMG(self.path, use_shadow=writable, shadow_path=self._shadow)

    def resize(self, shadow_file=None, size=None, sectors=None):
        """Resize a .dmg, optionally with a shadow file.

        :param shadow_file: Shadow file, if any.
        :param size: Size specification (for -size)
        """
        command = [Dmg.HDIUTIL, 'resize']

        if size is not None:
            command.extend(['-size', size])

        if sectors is not None:
            command.extend(['-sectors', sectors])

        if shadow_file is not None:
            command.extend(['-shadow', shadow_file])
            self._shadow = shadow_file

        command.append(self.path)

        print(' '.join(command))
        proc = subprocess.Popen(command, bufsize=-1, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        (stdout, stderr) = proc.communicate()

        if proc.returncode:
            print(stdout)
            print(stderr)
            raise HDIUtilError(
                "Error attempting to resize dmg at: {}, shadow: {}".format(self.path, shadow_file),
                stdout=stdout, stderr=stderr,
                return_code=proc.returncode, command=command)

    def resize_limits(self):
        """Get resize limits.

        :return: list of limits, first item is minimum size.
        """
        command = [Dmg.HDIUTIL, 'resize', '-limits', self.path]

        logger.debug(' '.join(command))
        proc = subprocess.Popen(command, bufsize=-1, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        (stdout, stderr) = proc.communicate()

        if proc.returncode:
            raise HDIUtilError(
                "Error attempting to get resize limits",
                stdout=stdout, stderr=stderr,
                return_code=proc.returncode, command=command)

        return stdout.split('\t')

    def shrink(self):
        """Resize a .dmg to its minimum size.

        :return:
        """
        limits = self.resize_limits()
        minimum_size = limits[0]

        self.resize(sectors='min')



class MountedDMG(object):

    HDIUTIL = '/usr/bin/hdiutil'

    def __init__(self, dmg_path, mountpoint=None, use_shadow=False, shadow_path=None, unmount=True):
        """MountedDMG is a context manager for mounting a .dmg file with or without a shadow file.

        :param dmg_path: Path to the .dmg to mount
        :param mountpoint: Path to the desired mountpoint if not automatically attaching to /Volumes
        :param use_shadow: If true, uses a shadow file to write changes.
        :param shadow_path: Specify a full path to the shadow file to create, if not specified then one is created
            beside the source .dmg
        :param unmount: Unmount the .dmg when the context exits. Disable this for testing. Will not remove a shadow if
            one has been created.
        """
        self._dmg_path = dmg_path

        if use_shadow and shadow_path is None:
            shadow_name = os.path.basename(self._dmg_path) + '.shadow'
            shadow_root = os.path.dirname(self._dmg_path)
            self._shadow_path = os.path.join(shadow_root, shadow_name)
        elif use_shadow:
            self._shadow_path = shadow_path
        else:
            self._shadow_path = None

        self._mount_points = []

        if mountpoint is None:
            mountpoint = tempfile.mkdtemp()
            logger.debug('created temporary mount point for dmg: {}'.format(mountpoint))

        self._mountpoint = mountpoint
        self._unmount = unmount

    def __enter__(self):
        """Context manager which mounts a .dmg file using hdiutil.

        :return Tuple[List[mount points], Optional[shadow_path]]:
        """
        command = [MountedDMG.HDIUTIL, 'attach', self._dmg_path,
                   '-mountRandom', self._mountpoint, '-nobrowse', '-plist',
                   '-owners', 'on']

        if self._shadow_path is not None:
            command.extend(['-shadow', self._shadow_path])

        logger.debug(' '.join(command))
        proc = subprocess.Popen(command, bufsize=-1, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        (stdout, stderr) = proc.communicate()

        if proc.returncode:
            raise HDIUtilError(
                "Error attempting to attach dmg",
                stdout=stdout, stderr=stderr,
                return_code=proc.returncode, command=command)

        plist = plistlib.readPlistFromString(stdout)

        self._mount_points = [entity['mount-point'] for entity in plist['system-entities'] if 'mount-point' in entity]
        self.mounted = True

        return self._mount_points, self._shadow_path

    def __exit__(self, *args):
        if not self._unmount:
            logger.debug('Not unmounting %s as per request', self._dmg_path)
            return

        try:
            mount_point = self._mount_points[0]

            command = [Dmg.HDIUTIL, 'detach', mount_point]
            logger.debug(' '.join(command))
            proc = subprocess.Popen(command, bufsize=-1, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            (stdout, stderr) = proc.communicate()

            if proc.returncode:
                raise HDIUtilError(
                    "Failed to unmount dmg",
                    return_code=proc.returncode,
                    stdout=stdout, stderr=stderr,
                    command=command
                )
        except HDIUtilError as err:
            logger.debug("Polite unmount failed: ", err)
            logger.debug("Attempting to force unmount ", mount_point)

            command = ['/usr/bin/hdiutil', 'detach', '-force', mount_point]
            logger.debug(' '.join(command))
            return_code = subprocess.call(command)

            if return_code:
                raise HDIUtilError(
                    "Failed to unmount dmg",
                    return_code=return_code,
                    command=command
                )


class NBI(object):

    def __init__(self, path):
        """NBI represents an existing .nbi bundle.

        :param path: Path to the .nbi bundle
        """
        super(NBI, self).__init__()

        self._path = path
        self._dmg_path = os.path.join(self._path, 'NetInstall.dmg')
        self._dmg = Dmg(self._dmg_path)
        self._shadow_path = None

    @property
    def path(self):
        """Path to the .nbi bundle."""
        return self._path

    @property
    def dmg(self):
        """Instance of Dmg representing the NetInstall.dmg"""
        return self._dmg

    @property
    def shadow_path(self):
        return self._shadow_path

    def mounted(self, writable=False, unmount=True):
        """Get a context manager to mount the NetInstall.dmg

        """
        return MountedDMG(self._dmg_path, use_shadow=writable, unmount=unmount)
        # mounted_dmg = MountedDMG(self._dmg_path, use_shadow=writable, unmount=unmount)
        #
        # if writable:
        #     self._shadow_path = mounted_dmg._shadow_path
        #
        # return mounted_dmg
