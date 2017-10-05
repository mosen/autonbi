#!/usr/bin/python
#
# AutoNBI.py - A tool to automate (or not) the building and modifying
#  of Apple NetBoot NBI bundles.
#
# Requirements:
#   * OS X 10.9 Mavericks - This tool relies on parts of the SIUFoundation
#     Framework which is part of System Image Utility, found in
#     /System/Library/CoreServices in Mavericks.
#
#   * Munki tools installed at /usr/local/munki - needed for FoundationPlist.
#
# Thanks to: Greg Neagle for overall inspiration and code snippets (COSXIP)
#            Per Olofsson for the awesome AutoDMG which inspired this tool
#            Tim Sutton for further encouragement and feedback on early versions
#            Michael Lynn for the ServerInformation framework hackery
#
# This tool aids in the creation of Apple NetBoot Image (NBI) bundles.
# It can run either in interactive mode by passing it a folder, installer
# application or DMG or automatically, integrated into a larger workflow.
#
# Required input is:
#
# * [--source][-s] The valid path to a source of one of the following types:
#
#   - A folder (such as /Applications) which will be searched for one
#     or more valid install sources
#   - An OS X installer application (e.g. "Install OS X Mavericks.app")
#   - An InstallESD.dmg file
#
# * [--destination][-d] The valid path to a dedicated build root folder:
#
#   The build root is where the resulting NBI bundle and temporary build
#   files are written. If the optional --folder arguments is given an
#   identically named folder must be placed in the build root:
#
#   ./AutoNBI <arguments> -d /Users/admin/BuildRoot --folder Packages
#
#   +-> Causes AutoNBI to look for /Users/admin/BuildRoot/Packages
#
# * [--name][-n] The name of the NBI bundle, without .nbi extension
#
# * [--folder] *Optional* The name of a folder to be copied onto
#   NetInstall.dmg. If the folder already exists, it will be overwritten.
#   This allows for the customization of a standard NetInstall image
#   by providing a custom rc.imaging and other required files,
#   such as a custom Runtime executable. For reference, see the
#   DeployStudio Runtime NBI.
#
# * [--auto][-a] Enable automated run. The user will not be prompted for
#   input and the application will attempt to create a valid NBI. If
#   the input source path results in more than one possible installer
#   source the application will stop. If more than one possible installer
#   source is found in interactive mode the user will be presented with
#   a list of possible InstallerESD.dmg choices and asked to pick one.
#
# * [--enable-nbi][-e] Enable the output NBI by default. This sets the "Enabled"
#   key in NBImageInfo.plist to "true".
#
# * [--add-python][-p] Add the Python framework and libraries to the NBI
#   in order to support Python-based applications at runtime
#
# * [--add-ruby][-r] Add the Ruby framework and libraries to the NBI
#   in order to support Ruby-based applications at runtime
#
# * [--vnc-password] *Optional* Enable VNC connections using the password given.
#
# To invoke AutoNBI in interactive mode:
#   ./AutoNBI -s /Applications -d /Users/admin/BuildRoot -n Mavericks
#
# To invoke AutoNBI in automatic mode:
#   ./AutoNBI -s ~/InstallESD.dmg -d /Users/admin/BuildRoot -n Mavericks -a
#
# To replace "Packages" on the NBI boot volume with a custom version:
#   ./AutoNBI -s ~/InstallESD.dmg -d ~/BuildRoot -n Mavericks -f Packages -a

import os
import sys
import logging
import tempfile
import argparse
import shutil
import objc
from distutils.version import LooseVersion

ansi = {'HEADER': '\033[95m',
        'OKBLUE': '\033[94m',
        'OKGREEN': '\033[92m',
        'WARNING': '\033[93m',
        'FAIL': '\033[91m',
        'ENDC': '\033[0m',
        'BOLD': '\033[1m',
        'UNDERLINE': '\033[4m'}


EMOJI_POOP = u'\U0001F4A9'
EMOJI_INFORMATION = u'\u2139'
EMOJI_DISC = u'\U0001F4BF'

# Setup access to the ServerInformation private framework to match board IDs to
#   model IDs if encountered (10.11 only so far) Code by Michael Lynn. Thanks!
class attrdict(dict):
    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__


ServerInformation = attrdict()
ServerInformation_bundle = objc.loadBundle('ServerInformation',
                                           ServerInformation,
                                           bundle_path='/System/Library/PrivateFrameworks/ServerInformation.framework')

sysidenabled = []


def main():
    logger = logging.getLogger('AutoNBI')
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    logger.addHandler(ch)

    parser = argparse.ArgumentParser(description='AutoNBI')

    # Setup the recognized options
    parser.add_argument('--source', '-s', required=True,
                        help='Required. Path to Install Mac OS X Lion.app '
                             'or Install OS X Mountain Lion.app or Install OS X Mavericks.app')
    parser.add_argument('--name', '-n', required=True,
                        help='Required. Name of the NBI, also applies to .plist')
    parser.add_argument('--destination', '-d', default=os.getcwd(),
                        help='Optional. Path to save .plist and .nbi files. Defaults to CWD.')
    parser.add_argument('--folder', '-f', default='',
                        help='Optional. Name of a folder on the NBI to modify. This will be the\
                            root below which changes will be made')
    parser.add_argument('--auto', '-a', action='store_true', default=False,
                        help='Optional. Toggles automation mode, suitable for scripted runs')
    parser.add_argument('--enable-nbi', '-e', action='store_true', default=False,
                        help='Optional. Marks NBI as enabled (IsEnabled = True).', dest='enablenbi')
    parser.add_argument('--add-ruby', '-r', action='store_true', default=False,
                        help='Optional. Enables Ruby in BaseSystem.', dest='addruby')
    parser.add_argument('--add-python', '-p', action='store_true', default=False,
                        help='Optional. Enables Python in BaseSystem.', dest='addpython')
    parser.add_argument('--utilities-plist', action='store_true', default=False,
                        help='Optional. Add a custom Utilities.plist to modify the menu.', dest='utilplist')
    parser.add_argument('--default', action='store_true', default=False,
                        help='Optional. Marks the NBI as the default for all clients. Only one default should be '
                             'enabled on any given NetBoot/NetInstall server.', dest='isdefault')
    parser.add_argument('--index', default=5000, dest='nbiindex', type=int,
                        help='Optional. Set a custom Index for the NBI. Default is 5000.')
    parser.add_argument('--type', default='NFS', dest='nbitype',
                        help='Optional. Set a custom Type for the NBI. HTTP or NFS. Default is NFS.')
    parser.add_argument('--sysid-enable', dest='sysidenabled', action='append', type=str,
                        help='Optional. Whitelist a given System ID (\'MacBookPro10,1\') Can be '
                             'defined multiple times. WARNING: This will enable ONLY the listed '
                             'System IDs. Systems not explicitly marked as enabled will not be '
                             'able to boot from this NBI.')
    parser.add_argument('--verbose', '-v', action='count', default=0,
                        help='Increase verbosity level')
    parser.add_argument('--vnc-password', dest='vncpassword',
                        help='Optional. Enable and set the VNC password to the given password.')

    # Parse the provided options
    arguments = parser.parse_args()

    if arguments.verbose > 0:  # TODO: this sucks
        ch.setLevel(logging.DEBUG)
        logger.setLevel(logging.DEBUG)
        logger.debug(ansi['HEADER'] + 'Verbose logging enabled' + ansi['ENDC'])

    # Are we root?
    if os.getuid() != 0:
        logger.error(ansi['FAIL'] + 'This tool requires sudo or root privileges.' + ansi['ENDC'])
        exit(-1)

    if not os.path.exists(arguments.source):
        logger.error(ansi['FAIL'] + 'The given source at %s does not exist.' + ansi['ENDC'], arguments.source)
        exit(-1)

    TMPDIR = tempfile.mkdtemp()

    from environment import BuildEnvironment
    buildenv = BuildEnvironment.from_host()

    try:
        from environment import InstallSource
        logger.debug(ansi['HEADER'] + '  Attempting to locate a valid installer at location:' + ansi['ENDC'] + ' %s', arguments.source)
        source = InstallSource.from_path(arguments.source)

        logger.debug(ansi['OKBLUE'] + 'Got source: ' + ansi['ENDC'] + '%s', source.path)
    except IOError as e:
        logger.error(ansi['FAIL'] + 'Error locating installer' + ansi['ENDC'], exc_info=e)
        sys.exit(1)

    if source.is_netinstall or source.is_esd:
        # If the destination path isn't absolute, we make it so to prevent errors
        if not arguments.destination.startswith('/'):
            destination = os.path.abspath(arguments.destination)
        else:
            destination = arguments.destination

        # Prep the build root - create it if it's not there
        if not os.path.exists(arguments.destination):
            os.mkdir(arguments.destination)

        logger.debug('Mounting %s', source.path)

        source.dmg.mount()
        osversion, osbuild, _ = source.version_info(is_high_sierra=buildenv.is_high_sierra)

        if LooseVersion(osversion) < '10.12':
            description = "OS X %s - %s" % (osversion, osbuild)
        else:
            description = "macOS %s - %s" % (osversion, osbuild)

        source.dmg.unmount()

        # Now move on to the actual NBI creation
        logger.info('Creating NBI at: %s', destination)

        # print 'Base NBI Operating System is ' + osversion
        from builder import NBIBuilder, NBImageInfoBuilder
        builder = NBIBuilder(buildenv, source, destination)
        nbi_path = builder.build(arguments.name)
        logger.info('Created .nbi at %s', nbi_path)

        from hdiutil import NBI, Dmg
        nbi = NBI(nbi_path)

        # Build NBImageInfo.plist
        info = NBImageInfoBuilder.from_nbi(nbi_path)

        if arguments.enablenbi:
            logger.debug('NBI will be enabled')
            info = info.enable()

        if arguments.nbiindex:
            logger.debug('NBI index will be %d', arguments.nbiindex)
            info = info.index(arguments.nbiindex)

        if arguments.isdefault:
            logger.debug('NBI is the default')
            info = info.default()

        info = info.description(description)
        plist_value = info.build()
        logger.info('Writing out NBImageInfo.plist')
        with open(os.path.join(nbi_path, 'NBImageInfo.plist'), 'w+') as fd:
            fd.write(plist_value)

        nbi_shadow_file = None

        with nbi.mounted(writable=True, unmount=False) as mount_points, nbi_shadow_file:
            logger.info("NBI %s, mounted at %s", nbi.path, ','.join(mount_points))
            nbi.dmg.shadow = nbi_shadow_file

            if arguments.folder and os.path.isdir(arguments.folder):
                customfolder = os.path.abspath(arguments.folder)

            # TODO: detect whether BaseSystem even needs to be modified
            # Original: if modifybasesystem: in def modify()
            if buildenv.is_high_sierra:
                logger.info("Install source is 10.13 or newer, BaseSystem.dmg is in an alternate location...")
                base_system_dmg_path = os.path.join(mount_points[0], 'Install macOS High Sierra.app', 'Contents',
                                                    'SharedSupport', 'BaseSystem.dmg')
            else:
                base_system_dmg_path = os.path.join(mount_points[0], 'BaseSystem.dmg')

            base_system_shadow_path = os.path.join(tempfile.mkdtemp(), 'BaseSystem.shadow')
            base_system_dmg = Dmg(base_system_dmg_path)

            logger.info("Running dmg.resize...")
            print(base_system_dmg.path)
            print(base_system_shadow_path)
            base_system_dmg.resize(base_system_shadow_path, size='8G')

            with base_system_dmg.mounted(writable=True) as base_mounts:
                logger.info("BaseSystem mounted at %s", ','.join(base_mounts))

            logger.info("BaseSystem unmounted")

            # Set some DMG conversion targets for later
            basesystemrw = os.path.join(TMPDIR, 'BaseSystemRW.dmg')
            basesystemro = os.path.join(TMPDIR, 'BaseSystemRO.dmg')

            # Convert to UDRW, the only format that will allow resizing the BaseSystem.dmg later
            logger.info("Converting BaseSystem to R/W")
            converted_dmg = base_system_dmg.convert(basesystemrw, 'UDRW')
            logger.info(ansi['OKBLUE'] + "Converted BaseSystem, output in:" + ansi['ENDC'] + "%s", converted_dmg.path)
            # convertresult = self.runcmd(self.dmgconvert(basesystemdmg, basesystemrw, basesystemshadow, 'UDRW'))
            # Delete the original DMG if this is not a netinstall, we need to clear up some space where possible

            logger.info("Removing original BaseSystem.dmg")
            os.remove(base_system_dmg_path)

            # Resize BaseSystem.dmg to its smallest possible size (using hdiutil resize -limits)
            logger.info("Shrinking %s", converted_dmg.path)
            converted_dmg.shrink()

            # Convert again, to UDRO, to shrink the final DMG size more
            logger.info("Converting %s to Read Only", converted_dmg.path)
            converted_ro_dmg = converted_dmg.convert(basesystemro, 'UDRO')

            # Rename the finalized DMG to its intended name BaseSystem.dmg
            logger.info("Copying read-only BaseSystem.dmg back into mounted .nbi at %s", base_system_dmg_path)
            shutil.copyfile(converted_ro_dmg.path, base_system_dmg_path)

        # We're done, unmount the outer NBI DMG.
        logger.info("NBI unmounted")

        # Convert modified DMG to .sparseimage, this will shrink the image
        # automatically after modification.
        logger.info('-' * 20)
        logger.info('Sealing DMG at path %s', nbi.dmg.path)

        nbi.dmg.convert("/tmp/autonbi")

        # dmgfinal = convertdmg(dmgpath, nbishadow)
        # print('Got back final DMG as ' + dmgfinal + ' from convertdmg()...')

if __name__ == '__main__':
    main()
