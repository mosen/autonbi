from distutils.version import LooseVersion
import os.path
import sys
import mimetypes
import plistlib

from hdiutil import Dmg


class BuildEnvironment(object):
    BUILD_EXEC_PATHS = {
        '10.13': '/System/Library/PrivateFrameworks/SIUFoundation.framework/XPCServices/com.apple.SIUAgent.xpc/Contents/Resources',
        '10.12': '/System/Library/PrivateFrameworks/SIUFoundation.framework/XPCServices/com.apple.SIUAgent.xpc/Contents/Resources',
        '10.11': '/System/Library/PrivateFrameworks/SIUFoundation.framework/XPCServices/com.apple.SIUAgent.xpc/Contents/Resources',
        '10.10': '/System/Library/CoreServices/System Image Utility.app/Contents/Frameworks/SIUFoundation.framework/Versions/A/XPCServices/com.apple.SIUAgent.xpc/Contents/Resources'
    }

    def __init__(self, version):
        self._version = version
        if LooseVersion(self._version) >= '10.13':
            self._build_exec_path = BuildEnvironment.BUILD_EXEC_PATHS['10.13']
            self._version_major = '10.13'
        elif LooseVersion(self._version) >= '10.12':
            self._build_exec_path = BuildEnvironment.BUILD_EXEC_PATHS['10.12']
            self._version_major = '10.12'
        elif LooseVersion(self._version) >= '10.11':
            self._build_exec_path = BuildEnvironment.BUILD_EXEC_PATHS['10.11']
            self._version_major = '10.11'
        elif LooseVersion(self._version) <= '10.10':
            self._build_exec_path = BuildEnvironment.BUILD_EXEC_PATHS['10.10']
            self._version_major = '10.10'

    @property
    def build_exec_path(self):
        return self._build_exec_path

    @property
    def version_major(self):
        return self._version_major

    @property
    def is_high_sierra(self):
        return self._version_major == '10.13'

    @classmethod
    def from_host(cls):
        import subprocess
        p = subprocess.Popen(['sw_vers', '-productVersion'], stdout=subprocess.PIPE)
        stdout, stderr = p.communicate()
        version = stdout.strip()

        return cls(version)


class InstallSource(object):
    """InstallSource describes an installation source such as:

    - App Store installer .app bundle.
    - Recovery partition.
    - Existing NetInstall .nbi bundle (if modifying an existing build).
    """
    INSTALLER_TYPE_ESD = 'esd'
    INSTALLER_TYPE_RECOVERY = 'recovery'
    INSTALLER_TYPE_NETINSTALL = 'netinstall'

    def __init__(self, installer_path, installer_type=INSTALLER_TYPE_ESD):
        self._path = installer_path
        self._type = installer_type

        if self._type == InstallSource.INSTALLER_TYPE_ESD:
            self._esd_source = os.path.join(self._path, 'Contents', 'SharedSupport', 'InstallESD.dmg')
            self._dmg = Dmg(self._esd_source)
        elif self._type == InstallSource.INSTALLER_TYPE_NETINSTALL:
            self._esd_source = None
            self._dmg = Dmg(self._path)
        else:
            self._esd_source = None
            self._dmg = None

    @property
    def path(self):
        """Path refers to the filesystem path of:

        - The .app installer bundle (see _esd_source for the path to InstallESD.dmg when using High Sierra)
        - The .nbi netinstall bundle.
        - The mounted recovery partition.

        :return str: The path to the installer.
        """
        return self._path

    @property
    def dmg(self):
        return self._dmg

    @property
    def dmg_mount_point(self):
        mount = None
        if len(self.dmg.mount_points) > 1:
            for i in self.dmg.mount_points[0]:
                if i.find('dmg'):
                    mount = i
        else:
            mount = self.dmg.mount_points[0]

        return mount

    @property
    def is_netinstall(self):
        return self._type == InstallSource.INSTALLER_TYPE_NETINSTALL

    @property
    def is_esd(self):
        return self._type == InstallSource.INSTALLER_TYPE_ESD

    @classmethod
    def pick(cls):
        """TODO: Implement pickinstaller()"""
        pass

    @classmethod
    def from_path(cls, installer_path):
        """Generate an instance of InstallSource from the given path.

        In AutoNBI this function is performed differently in 'non-auto' mode.
        This method only covers the auto mode, not the manual picker.

        :param installer_path: The location of a recovery partition or installer app to install
            from.
        :raises IOError: If the installer source is unsuitable or unreadable.
        :returns InstallSource: The install source object.
        :todo: Method is too complex
        """
        if os.path.isdir(installer_path):
            # Remove a potential trailing slash (ie. from autocompletion)
            if installer_path.endswith('/'):
                installer_path = installer_path.rstrip('/')

            if not os.path.exists(installer_path):
                raise IOError(
                    'The root path {} is not a valid path - unable to proceed.'.format(installer_path))

            if installer_path.endswith('com.apple.recovery.boot'):
                print 'Source is a Recovery partition, not mounting an InstallESD...'
                return cls(installer_path, installer_type=InstallSource.INSTALLER_TYPE_RECOVERY)

            elif not installer_path.endswith('.app'):
                print 'Mode is auto but the rootpath is not an installer app or DMG, unable to proceed'
                sys.exit(1)

            elif installer_path.endswith('.app'):
                install_esd_path = os.path.join(installer_path, 'Contents/SharedSupport/InstallESD.dmg')
                if os.path.exists(install_esd_path):
                    return cls(installer_path, 'esd')
                else:
                    raise IOError('Unable to locate InstallESD.dmg in {} - exiting.'.format(installer_path))
            else:
                return None  # this should technically never happen, unless you specified a non installer
        elif mimetypes.guess_type(installer_path)[0].endswith('diskimage'):
            print 'Source is a disk image.'
            if 'NetInstall' in installer_path:
                print 'Disk image is an existing NetInstall, will modify only...'
                return cls(installer_path, installer_type=InstallSource.INSTALLER_TYPE_NETINSTALL)
            elif 'InstallESD' in installer_path:
                print 'Disk image is an InstallESD, will create new NetInstall...'
                return cls(installer_path, installer_type=InstallSource.INSTALLER_TYPE_ESD)
        else:
            raise IOError('Source is neither an installer app or InstallESD.dmg.')

    def version_info(self, is_high_sierra=False):
        """"getosversioninfo will attempt to retrieve the OS X version and build
            from the given mount point by reading /S/L/CS/SystemVersion.plist
            Most of the code comes from COSXIP without changes."""

        assert self.dmg.mounted

        if is_high_sierra:
            basesystem_path = os.path.join(self.path, 'Contents/SharedSupport')
        else:
            basesystem_path = self.dmg_mount_point

        # Check for availability of BaseSystem.dmg
        basesystem_dmg = os.path.join(basesystem_path, 'BaseSystem.dmg')
        if not os.path.isfile(basesystem_dmg):
            raise IOError('Missing BaseSystem.dmg in %s' % basesystem_dmg)

        # Mount BaseSystem.dmg

        basesystem = Dmg(basesystem_dmg)
        basesystem.mount()
        basesystem_mount_point = basesystem.mount_points[0]

        # Read SystemVersion.plist from the mounted BaseSystem.dmg
        system_version_plist = os.path.join(
            basesystem_mount_point,
            'System/Library/CoreServices/SystemVersion.plist')
        # Now parse the .plist file
        try:
            version_info = plistlib.readPlist(system_version_plist)

        # Got errors?
        except IOError, err:
            basesystem.unmount()
            raise IOError('Could not read %s: %s' % (system_version_plist, err))

        # Done, unmount BaseSystem.dmg
        finally:
            basesystem.unmount()

        # Return the version and build as found in the parsed plist
        return version_info.get('ProductUserVisibleVersion'), \
               version_info.get('ProductBuildVersion'), basesystem_mount_point