import os.path
import sys
import plistlib
import shutil
import subprocess
import logging

sys.path.append("/usr/local/munki/munkilib")
import FoundationPlist

logger = logging.getLogger(__name__)


class AutoNBIProcessError(BaseException):
    pass


class NBImageInfoBuilder(object):
    """NBImageInfoBuilder builds the NBImageInfo.plist

    """
    def __init__(self):
        self._info = {
            'IsInstall': True,
            'Index': 6667,
            'Description': 'NetBoot Image',
            'Kind': 1,
            'Language': 'Default',
            'SupportsDiskless': False,
            'RootPath': 'NetInstall.dmg',
            'BootFile': 'booter',
            'Architectures': ['i386'],
            'BackwardCompatible': False,
            'IsEnabled': False,
            'IsDefault': False,
        }

    @classmethod
    def from_platform_support(cls, platform_support_path):
        """Generate a new NBImageInfo builder instance, starting with supported system identifiers from a given
        PlatformSupport.plist.

        NOTE: OS X versions prior to 10.11 list both SupportedModelProperties and
            SupportedBoardIds - 10.11 only lists SupportedBoardIds. So we need to
            check both and append to the list if missing. Basically appends any
            model IDs found by looking up their board IDs to 'disabledsystemidentifiers'

        :param platform_support_path: Path to PlatformSupport.plist inside the .nbi eg. ``example.nbi/i386/PlatformSupport.plist``
        :returns: NBImageInfoBuilder seeded with platform support from plist.
        """
        return cls()

    @classmethod
    def from_nbi(cls, nbi_path):
        """Generate a new NBImageInfo builder instance, starting with supported system identifiers from an .nbi

        :param nbi_path: Path to an existing NBI.
        :returns: NBImageInfoBuilder seeded with platform support from plist.
        """
        return NBImageInfoBuilder.from_platform_support(os.path.join(nbi_path, 'i386', 'PlatformSupport.plist'))

    def index(self, idx):
        """
        Set the NetBoot image index (default is 6667)

        :param idx: The index number of the netboot image.
        :return: NBImageInfoBuilder
        """
        self._info['Index'] = idx
        return self

    def description(self, desc):
        """
        Set the NetBoot image description (default is 'NetBoot Image')

        :param desc: The description
        :return: NBImageInfoBuilder
        """
        self._info['Description'] = desc
        return self

    def enabled(self):
        """
        Enable the NetBoot Image

        :return: NBImageInfoBuilder
        """
        self._info['IsEnabled'] = True
        return self

    def default(self):
        """
        Make the NetBoot Image the default

        :return: NBImageInfoBuilder
        """
        self._info['IsDefault'] = True
        return self

    def enable_identifiers(self, identifiers):
        """
        Enable a list of system identifiers

        :param identifiers: A single identifier or a list of identifiers.
        :return: NBImageInfoBuilder
        """
        if isinstance(identifiers, list):
            self._info['EnabledSystemIdentifiers'] = identifiers
        else:
            self._info['EnabledSystemIdentifiers'] = [identifiers]

        return self

    def disable_identifiers(self, identifiers):
        """
        Disable a list of system identifiers

        :param identifiers: A single identifier or a list of identifiers.
        :return: NBImageInfoBuilder
        """
        if isinstance(identifiers, list):
            self._info['DisabledSystemIdentifiers'] = identifiers
        else:
            self._info['DisabledSystemIdentifiers'] = [identifiers]

        return self

    def build(self):
        """
        Write out a property list and return it.

        :return:
        """
        return FoundationPlist.writePlistToString(self._info)


class NBIBuilder(object):

    def __init__(self, build_environment, source, workdir):
        """The builder class for the NetInstall image.

        Provides an interface for specifying .nbi build conditions.

        :param build_environment: Instance of BuildEnvironment
        :param source: Instance of InstallerSource from the passed in argument
        :param workdir: The working directory (for temporary files)

        """
        super(NBIBuilder, self).__init__()
        self._build_environment = build_environment
        self._source = source
        self._workdir = workdir
        self._enable_python = False
        self._enable_ruby = False
        self._custom_folder = None

    def enable_python(self):
        self._enable_python = True
        return self

    def enable_ruby(self):
        self._enable_ruby = True
        return self

    def custom_folder(self, folder):
        self._custom_folder = folder
        return self

    def _prepare_workdir(self, workdir, siu_settings=None):
        """Copies in the required Apple-provided createCommon.sh and also creates
        an empty file named createVariables.sh. We actually pass the variables
        this file might contain using environment variables but it is expected
        to be present so we fake out Apple's createNetInstall.sh script."""
        commonsource = os.path.join(self._build_environment.build_exec_path, 'createCommon.sh')
        commontarget = os.path.join(workdir, 'createCommon.sh')
        shutil.copyfile(commonsource, commontarget)
        open(os.path.join(workdir, 'createVariables.sh'), 'a').close()

        if siu_settings is not None:
            plistlib.writePlist(siu_settings, os.path.join(workdir, '.SIUSettings'))

    def _cleanup_workdir(self, workdir):
        os.unlink(os.path.join(workdir, 'createCommon.sh'))
        os.unlink(os.path.join(workdir, 'createVariables.sh'))

    def build(self, name):
        """
        Build the NBI

        :param name: The NBI name to produce.
        :return str: Path to the output .nbi bundle
        """

        if self._build_environment.is_high_sierra:
            self._prepare_workdir(self._workdir, {
                'SIU-SIP-setting': True,
                'SIU-SKEL-setting': False,
                'SIU-teamIDs-to-add': []
            })
        else:
            self._prepare_workdir(self._workdir)

        build_exec = os.path.join(self._build_environment.build_exec_path, 'createNetInstall.sh')
        command = [build_exec, self._workdir, '7000']
        destpath = os.path.join(self._workdir, name + '.nbi')

        createvariables = {'destPath': destpath,
                           'dmgTarget': 'NetInstall',
                           'dmgVolName': name,
                           'destVolFSType': 'JHFS+',
                           'installSource': self._source.path,
                           'scriptsDebugKey': 'INFO',
                           'ownershipInfoKey': 'root:wheel'}

        proc = subprocess.Popen(command, bufsize=-1, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=createvariables)

        (unused, err) = proc.communicate()

        if proc.returncode:
            raise AutoNBIProcessError(err)

        self._cleanup_workdir(self._workdir)

        return destpath

