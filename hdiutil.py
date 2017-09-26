import os.path
import subprocess
import plistlib
import logging

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
        self.mounted = False
        self._path = path
        self._mount_points = None
        self._shadow = None

    def mount(self, shadow=False, tmp_dir=None):
        """Mount the .dmg

        :param shadow: Mount with a shadow file
        :param tmp_dir: Use this temporary working directory instead of the default random location
        :return:
        """
        command = [Dmg.HDIUTIL, 'attach', self.path,
                   '-mountRandom', tmp_dir, '-nobrowse', '-plist',
                   '-owners', 'on']

        if shadow:
            shadow_name = os.path.basename(self.path) + '.shadow'
            shadow_root = os.path.dirname(self.path)
            shadow_path = os.path.join(shadow_root, shadow_name)
            command.extend(['-shadow', shadow_path])

        proc = subprocess.Popen(command, bufsize=-1, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        (pliststr, err) = proc.communicate()
        if proc.returncode:
            raise IOError('Error: "{}" while mounting {}'.format(err, self.path))

        plist = plistlib.readPlistFromString(pliststr)

        self._mount_points = [entity['mount-point'] for entity in plist['system-entities'] if 'mount-point' in entity]
        self.mounted = True

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

