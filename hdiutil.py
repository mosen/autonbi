import os.path
import subprocess
import plistlib
import logging
import tempfile

logger = logging.getLogger(__name__)

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

    def __init__(self, path):
        super(Dmg, self).__init__()
        self._mounted = False
        self._path = path
        self._mount_points = None
        self._shadow = None

    def mount(self, shadow=False, tmp_dir=None):
        """Mount the .dmg

        :param shadow: Mount with a shadow file, for writing modifications to NetInstall.dmg
        :param tmp_dir: Use this temporary working directory instead of the default random location
        :return:
        """
        if tmp_dir is None:
            tmp_dir = tempfile.mkdtemp()

        command = [Dmg.HDIUTIL, 'attach', self.path,
                   '-mountRandom', tmp_dir, '-nobrowse', '-plist',
                   '-owners', 'on']

        if shadow:
            shadow_name = os.path.basename(self.path) + '.shadow'
            shadow_root = os.path.dirname(self.path)
            shadow_path = os.path.join(shadow_root, shadow_name)
            self._shadow = shadow_path
            command.extend(['-shadow', shadow_path])

        proc = subprocess.Popen(command, bufsize=-1, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        (pliststr, err) = proc.communicate()
        if proc.returncode:
            raise IOError('Error: "{}" while mounting {}'.format(err, self.path))

        plist = plistlib.readPlistFromString(pliststr)

        self._mount_points = [entity['mount-point'] for entity in plist['system-entities'] if 'mount-point' in entity]
        self._mounted = True

    def unmount(self, mount_point=None):
        """Unmount the .dmg"""
        if mount_point is None:
            mount_point = self.mount_points[0]

        command = [Dmg.HDIUTIL, 'detach', mount_point]
        proc = subprocess.Popen(command, bufsize=-1, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        (unused_output, err) = proc.communicate()

        if proc.returncode:
            logger.debug("Polite unmount failed: ", err)
            logger.debug("Attempting to force unmount ", mount_point)

            retcode = subprocess.call(['/usr/bin/hdiutil', 'detach', '-force',
                                       mount_point])

            logger.info("Unmounting successful...")
            if retcode:
                logger.error("Failed to unmount ", mount_point)

        self._mounted = False

    def convert(self, fmt, output):
        """Convert a DMG with or without a shadow file to the specified format.

        :param fmt: The -format parameter to hdiutil.
        :param output: The output path of the converted dmg.
        :return Dmg: The instance of the output dmg.
        """
        if self.shadow:
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

        proc = subprocess.Popen(command, bufsize=-1, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        (unused_output, err) = proc.communicate()

        return Dmg(output)

    def mounted(self, writable=False):
        return MountedDMG(self.path, use_shadow=writable, shadow_path=self._shadow)

    def resize(self, shadow_file=None, size=None):
        """Resize a .dmg, optionally with a shadow file.

        :param shadow_file: Shadow file, if any.
        :param size: Size specification (for -size)
        """
        command = [Dmg.HDIUTIL, 'resize', '-size', size]

        if shadow_file is not None:
            command.extend(['-shadow', shadow_file])
            self._shadow = shadow_file

        command.append(self.path)
        logger.debug(' '.join(command))

        proc = subprocess.Popen(command, bufsize=-1, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        (output, err) = proc.communicate()
        logger.debug(output)



class MountedDMG(object):

    HDIUTIL = '/usr/bin/hdiutil'

    def __init__(self, dmg_path, mountpoint=None, use_shadow=False, shadow_path=None):
        """MountedDMG is a context manager for mounting a .dmg file with or without a shadow file.

        :param dmg_path: Path to the .dmg to mount
        :param mountpoint: Path to the desired mountpoint if not automatically attaching to /Volumes
        :param use_shadow: If true, uses a shadow file to write changes.
        :param shadow_path: Specify a full path to the shadow file to create, if not specified then one is created
            beside the source .dmg
        """
        self._dmg_path = dmg_path
        self._use_shadow = use_shadow

        if use_shadow and shadow_path is None:
            shadow_name = os.path.basename(self._dmg_path) + '.shadow'
            shadow_root = os.path.dirname(self._dmg_path)
            self._shadow_path = os.path.join(shadow_root, shadow_name)

        self._shadow_path = shadow_path
        self._mount_points = []

        if mountpoint is None:
            mountpoint = tempfile.mkdtemp()
        self._mountpoint = mountpoint

    def __enter__(self):
        command = [MountedDMG.HDIUTIL, 'attach', self._dmg_path,
                   '-mountRandom', self._mountpoint, '-nobrowse', '-plist',
                   '-owners', 'on']

        if self._use_shadow:
            command.extend(['-shadow', self._shadow_path])

        logger.debug(' '.join(command))
        proc = subprocess.Popen(command, bufsize=-1, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        (pliststr, err) = proc.communicate()

        if proc.returncode:
            raise IOError('Error: "{}" while mounting {}'.format(err, self._dmg_path))

        plist = plistlib.readPlistFromString(pliststr)

        self._mount_points = [entity['mount-point'] for entity in plist['system-entities'] if 'mount-point' in entity]
        self.mounted = True

        return self._mount_points

    def __exit__(self, *args):
        command = [MountedDMG.HDIUTIL, 'detach', self._mount_points[0]]
        proc = subprocess.Popen(command, bufsize=-1, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        (unused_output, err) = proc.communicate()

        if proc.returncode:
            logger.debug("Polite unmount failed: ", err)
            logger.debug("Attempting to force unmount ", self._mount_points[0])

            retcode = subprocess.call([MountedDMG.HDIUTIL, 'detach', '-force',
                                       self._mount_points[0]])

            logger.info("Unmounting successful...")
            if retcode:
                logger.error("Failed to unmount ", self._mount_points[0])


class NBI(object):

    def __init__(self, path):
        """NBI represents a .nbi bundle.

        :param path: Path to the .nbi bundle
        """
        super(NBI, self).__init__()
        self._path = path
        self._dmg_path = os.path.join(self._path, 'NetInstall.dmg')
        self._dmg = Dmg(self._dmg_path)

    @property
    def path(self):
        return self._path

    @property
    def dmg(self):
        return self._dmg

    def mounted(self, writable=False):
        return MountedDMG(self._dmg_path, use_shadow=writable)