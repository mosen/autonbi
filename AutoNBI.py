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
import tempfile
import mimetypes
import distutils.core
import subprocess
import plistlib
import argparse
import shutil
import binascii
from distutils.version import LooseVersion
from distutils.spawn import find_executable
from ctypes import CDLL, Structure, c_void_p, c_size_t, c_uint, c_uint32, c_uint64, create_string_buffer, addressof, \
    sizeof, byref
import objc

sys.path.append("/usr/local/munki/munkilib")
import FoundationPlist
from xml.parsers.expat import ExpatError


# Setup access to the ServerInformation private framework to match board IDs to
#   model IDs if encountered (10.11 only so far) Code by Michael Lynn. Thanks!
class attrdict(dict):
    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__


ServerInformation = attrdict()
ServerInformation_bundle = objc.loadBundle('ServerInformation',
                                           ServerInformation,
                                           bundle_path='/System/Library/PrivateFrameworks/ServerInformation.framework')


#  Below code from COSXIP by Greg Neagle

def cleanUp():
    """Cleanup our TMPDIR"""
    if TMPDIR:
        shutil.rmtree(TMPDIR, ignore_errors=True)


def fail(errmsg=''):
    """Print any error message to stderr,
    clean up install data, and exit"""
    if errmsg:
        print >> sys.stderr, errmsg
    cleanUp()
    exit(1)


def mountdmg(dmgpath, use_shadow=False):
    """
    Attempts to mount the dmg at dmgpath
    and returns a list of mountpoints
    If use_shadow is true, mount image with shadow file
    """
    mountpoints = []
    dmgname = os.path.basename(dmgpath)
    cmd = ['/usr/bin/hdiutil', 'attach', dmgpath,
           '-mountRandom', TMPDIR, '-nobrowse', '-plist',
           '-owners', 'on']
    if use_shadow:
        shadowname = dmgname + '.shadow'
        shadowroot = os.path.dirname(dmgpath)
        shadowpath = os.path.join(shadowroot, shadowname)
        cmd.extend(['-shadow', shadowpath])
    else:
        shadowpath = None
    proc = subprocess.Popen(cmd, bufsize=-1,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (pliststr, err) = proc.communicate()
    if proc.returncode:
        print >> sys.stderr, 'Error: "%s" while mounting %s.' % (err, dmgname)
    if pliststr:
        plist = plistlib.readPlistFromString(pliststr)
        for entity in plist['system-entities']:
            if 'mount-point' in entity:
                mountpoints.append(entity['mount-point'])

    return mountpoints, shadowpath


def unmountdmg(mountpoint):
    """
    Unmounts the dmg at mountpoint
    """
    proc = subprocess.Popen(['/usr/bin/hdiutil', 'detach', mountpoint],
                            bufsize=-1, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    (unused_output, err) = proc.communicate()
    if proc.returncode:
        print >> sys.stderr, 'Polite unmount failed: %s' % err
        print >> sys.stderr, 'Attempting to force unmount %s' % mountpoint
        # try forcing the unmount
        retcode = subprocess.call(['/usr/bin/hdiutil', 'detach', '-force',
                                   mountpoint])
        print('Unmounting successful...')
        if retcode:
            print >> sys.stderr, 'Failed to unmount %s' % mountpoint


# Above code from COSXIP by Greg Neagle

def convertdmg(dmgpath, nbishadow):
    """
    Converts the dmg at mountpoint to a .sparseimage
    """
    # Get the full path to the DMG minus the extension, hdiutil adds one
    dmgfinal = os.path.splitext(dmgpath)[0]

    # Run a basic 'hdiutil convert' using the shadow file to pick up
    #   any changes we made without needing to convert between r/o and r/w
    cmd = ['/usr/bin/hdiutil', 'convert', dmgpath, '-format', 'UDSP',
           '-shadow', nbishadow, '-o', dmgfinal]
    proc = subprocess.Popen(cmd, bufsize=-1,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (unused, err) = proc.communicate()

    # Got errors?
    if proc.returncode:
        print >> sys.stderr, 'Disk image conversion failed: %s' % err

    # Return the name of the converted DMG back to the caller
    return dmgfinal + '.sparseimage'


def getosversioninfo(mountpoint):
    """"getosversioninfo will attempt to retrieve the OS X version and build
        from the given mount point by reading /S/L/CS/SystemVersion.plist
        Most of the code comes from COSXIP without changes."""

    # Check for availability of BaseSystem.dmg
    basesystem_dmg = os.path.join(mountpoint, 'BaseSystem.dmg')
    if not os.path.isfile(basesystem_dmg):
        unmountdmg(mountpoint)
        fail('Missing BaseSystem.dmg in %s' % mountpoint)

    # Mount BaseSystem.dmg
    basesystemmountpoints, unused_shadowpath = mountdmg(basesystem_dmg)
    basesystemmountpoint = basesystemmountpoints[0]

    # Read SystemVersion.plist from the mounted BaseSystem.dmg
    system_version_plist = os.path.join(
        basesystemmountpoint,
        'System/Library/CoreServices/SystemVersion.plist')
    # Now parse the .plist file
    try:
        version_info = plistlib.readPlist(system_version_plist)

    # Got errors?
    except (ExpatError, IOError), err:
        unmountdmg(basesystemmountpoint)
        unmountdmg(mountpoint)
        fail('Could not read %s: %s' % (system_version_plist, err))

    # Done, unmount BaseSystem.dmg
    else:
        unmountdmg(basesystemmountpoint)

    # Return the version and build as found in the parsed plist
    return version_info.get('ProductUserVisibleVersion'), \
           version_info.get('ProductBuildVersion'), mountpoint


def buildplist(nbiindex, nbitype, nbidescription, nbiosversion, nbiname, nbienabled, isdefault, destdir=__file__):
    """buildplist takes a source, destination and name parameter that are used
        to create a valid plist for imagetool ingestion."""

    # Read and parse PlatformSupport.plist which has a reasonably reliable list
    #   of model IDs and board IDs supported by the OS X version being built

    nbipath = os.path.join(destdir, nbiname + '.nbi')
    platformsupport = FoundationPlist.readPlist(os.path.join(nbipath, 'i386', 'PlatformSupport.plist'))

    # OS X versions prior to 10.11 list both SupportedModelProperties and
    #   SupportedBoardIds - 10.11 only lists SupportedBoardIds. So we need to
    #   check both and append to the list if missing. Basically appends any
    #   model IDs found by looking up their board IDs to 'disabledsystemidentifiers'

    disabledsystemidentifiers = platformsupport.get('SupportedModelProperties') or []
    for boardid in platformsupport.get('SupportedBoardIds') or []:
        # Call modelPropertiesForBoardIDs from the ServerInfo framework to
        #   look up the model ID for this board ID.
        for sysid in ServerInformation.ServerInformationComputerModelInfo.modelPropertiesForBoardIDs_([boardid]):
            # If the returned model ID is not yet in 'disabledsystemidentifiers'
            #   add it, but not if it's an unresolved 'Mac-*' board ID.
            if sysid not in disabledsystemidentifiers and 'Mac-' not in sysid:
                disabledsystemidentifiers.append(sysid)

    nbimageinfo = {'IsInstall': True,
                   'Index': nbiindex,
                   'Kind': 1,
                   'Description': nbidescription,
                   'Language': 'Default',
                   'IsEnabled': nbienabled,
                   'SupportsDiskless': False,
                   'RootPath': 'NetInstall.dmg',
                   'EnabledSystemIdentifiers': sysidenabled,
                   'BootFile': 'booter',
                   'Architectures': ['i386'],
                   'BackwardCompatible': False,
                   'DisabledSystemIdentifiers': disabledsystemidentifiers,
                   'Type': nbitype,
                   'IsDefault': isdefault,
                   'Name': nbiname,
                   'osVersion': nbiosversion}

    plistfile = os.path.join(nbipath, 'NBImageInfo.plist')
    FoundationPlist.writePlist(nbimageinfo, plistfile)

def pickinstaller(installers):
    """pickinstaller provides an interactive picker when more than one
        potential OS X installer app was returned by locateinstaller() """

    # Initialize choice
    choice = ''

    # Cycle through the installers and print an enumerated list to stdout
    for item in enumerate(installers):
        print "[%d] %s" % item

    # Have the user pick an installer
    try:
        idx = int(raw_input("Pick installer to use: "))

    # Got errors? Not a number, bail.
    except ValueError:
        print "Not a valid selection - unable to proceed."
        sys.exit(1)

    # Attempt to pull the installer using the user's input
    try:
        choice = installers[idx]

    # Got errors? Not a valid index in the list, bail.
    except IndexError:
        print "Not a valid selection - unable to proceed."
        sys.exit(1)

    # We're done, return the user choice to the caller
    return choice

def vnc_password(password, secret='1734516E8BA8C5E2FF1C39567390ADCA'):
    """
    Generate a VNC password by XORing a string with the fixed VNC string.

    :param password: The plain password to encode
    :param secret: The XOR string (defaults to apple's VNC string)

    :returns string: The encoded password
    """
    if not password:
        password = ''

    password = binascii.hexlify(password)

    xor_list = [int(h + l, 16) for (h, l) in zip(secret[0::2], secret[1::2])]
    value_list = [int(h + l, 16) for (h, l) in zip(password[0::2], password[1::2])]

    def reduce_xor(memo, c):
        """reduce by XORing and substituting with NUL when out of bounds"""
        v = value_list.pop(0) if len(value_list) > 0 else 0
        return memo + chr(c ^ v)

    result = reduce(reduce_xor, xor_list, '')
    return result


# Example usage of the function:
# decompress('PayloadJava.cpio.xz', 'PayloadJava.cpio')
# Decompresses a xz compressed file from the first input file path to the second output file path

class lzma_stream(Structure):
    _fields_ = [
        ("next_in", c_void_p),
        ("avail_in", c_size_t),
        ("total_in", c_uint64),
        ("next_out", c_void_p),
        ("avail_out", c_size_t),
        ("total_out", c_uint64),
        ("allocator", c_void_p),
        ("internal", c_void_p),
        ("reserved_ptr1", c_void_p),
        ("reserved_ptr2", c_void_p),
        ("reserved_ptr3", c_void_p),
        ("reserved_ptr4", c_void_p),
        ("reserved_int1", c_uint64),
        ("reserved_int2", c_uint64),
        ("reserved_int3", c_size_t),
        ("reserved_int4", c_size_t),
        ("reserved_enum1", c_uint),
        ("reserved_enum2", c_uint),
    ]


# Hardcoded this path to the System liblzma dylib location, so that /usr/local/lib or other user
# installed library locations aren't used (which ctypes.util.find_library(...) would hit).
# Available in OS X 10.7+
c_liblzma = CDLL('/usr/lib/liblzma.dylib')

NULL = None
BUFSIZ = 65535
LZMA_OK = 0
LZMA_RUN = 0
LZMA_FINISH = 3
LZMA_STREAM_END = 1
BLANK_BUF = '\x00' * BUFSIZ
UINT64_MAX = c_uint64(18446744073709551615)
LZMA_CONCATENATED = c_uint32(0x08)
LZMA_RESERVED_ENUM = 0
LZMA_STREAM_INIT = [NULL, 0, 0, NULL, 0, 0, NULL, NULL, NULL, NULL, NULL, NULL, 0, 0, 0, 0, LZMA_RESERVED_ENUM,
                    LZMA_RESERVED_ENUM]


def decompress(infile, outfile):
    # Create an empty lzma_stream object
    strm = lzma_stream(*LZMA_STREAM_INIT)

    # Initialize a decoder
    result = c_liblzma.lzma_stream_decoder(byref(strm), UINT64_MAX, LZMA_CONCATENATED)

    # Setup the output buffer
    outbuf = create_string_buffer(BUFSIZ)
    strm.next_out = addressof(outbuf)
    strm.avail_out = sizeof(outbuf)

    # Setup the (blank) input buffer
    inbuf = create_string_buffer(BUFSIZ)
    strm.next_in = addressof(inbuf)
    strm.avail_in = 0

    # Read in the input .xz file
    # ... Not the best way to do things because it reads in the entire file - probably not great for GB+ size

    # f_in = open(infile, 'rb')
    # xz_file = f_in.read()
    # f_in.close()
    xz_file = open(infile, 'rb')

    cursor = 0
    xz_file.seek(0, 2)
    EOF = xz_file.tell()
    xz_file.seek(0)

    # Open up our output file
    f_out = open(outfile, 'wb')

    # Start with a RUN action
    action = LZMA_RUN
    # Keep looping while we're processing
    while True:
        # Check if decoder has consumed the current input buffer and we have remaining data
        if ((strm.avail_in == 0) and (cursor < EOF)):
            # Load more data!
            # In theory, I shouldn't have to clear the input buffer, but I'm paranoid
            # inbuf[:] = BLANK_BUF
            # Now we load it:
            # - Attempt to take a BUFSIZ chunk of data
            input_chunk = xz_file.read(BUFSIZ)
            # - Measure how much we actually got
            input_len = len(input_chunk)
            # - Assign the data to the buffer
            inbuf[0:input_len] = input_chunk
            # - Configure our chunk input information
            strm.next_in = addressof(inbuf)
            strm.avail_in = input_len
            # - Adjust our cursor
            cursor += input_len
            # - If the cursor is at the end, switch to FINISH action
            if (cursor >= EOF):
                action = LZMA_FINISH
        # If we're here, we haven't completed/failed, so process more data!
        result = c_liblzma.lzma_code(byref(strm), action)
        # Check if we filled up the output buffer / completed running
        if ((strm.avail_out == 0) or (result == LZMA_STREAM_END)):
            # Write out what data we have!
            # - Measure how much we got
            output_len = BUFSIZ - strm.avail_out
            # - Get that much from the buffer
            output_chunk = outbuf.raw[:output_len]
            # - Write it out
            f_out.write(output_chunk)
            # - Reset output information to a full available buffer
            # (Intentionally not clearing the output buffer here .. but probably could?)
            strm.next_out = addressof(outbuf)
            strm.avail_out = sizeof(outbuf)
        if (result != LZMA_OK):
            if (result == LZMA_STREAM_END):
                # Yay, we finished
                result = c_liblzma.lzma_end(byref(strm))
                return True
            # If we got here, we have a problem
            # Error codes are defined in xz/src/liblzma/api/lzma/base.h (LZMA_MEM_ERROR, etc.)
            # Implementation of pretty English error messages is an exercise left to the reader ;)
            raise Exception("Error: return code of value %s - naive decoder couldn't handle input!" % (result))


HDIUTIL = '/usr/bin/hdiutil'
# hdiutil commands
attach_dmg = lambda shadow_file, attach_source, tmp: [
    HDIUTIL, 'attach',
    '-shadow', shadow_file,
    '-mountRandom', tmp,
    '-nobrowse', '-plist', '-owners', 'on',
    attach_source]
detach_dmg = lambda mountpoint: [HDIUTIL, 'detach', '-force', mountpoint]


def run(cmd, cwd=None):
    """
    Run a command using subprocess.Popen

    :param cmd: The command to run
    :param cwd: The optional working directory
    :return: the content of stdout
    """
    if cwd:
        proc = subprocess.Popen(cmd, bufsize=-1,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd,
                                shell=True)
        (result, err) = proc.communicate()
    else:
        proc = subprocess.Popen(cmd, bufsize=-1,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        (result, err) = proc.communicate()

    if proc.returncode:
        print >> sys.stderr, 'Error "%s" while running command %s' % (err, cmd)

    return result


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
        self._path = path
        self._mount_points = None
        self._shadow = None

    def mount(self, shadow=False):
        command = [Dmg.HDIUTIL, 'attach', self.path,
                   '-mountRandom', TMPDIR, '-nobrowse', '-plist',
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

        run(command)
        return Dmg(output)


class AutoNBIProcessError(BaseException):
    pass


class NBImageInfoBuilder(object):
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
        return FoundationPlist.writePlistToString(self._info)


class NBIBuilder(object):
    """The builder class for the NetInstall image."""

    def __init__(self, build_environment, source, workdir):
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
        :return:
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
                           'installSource': self._source,
                           'scriptsDebugKey': 'INFO',
                           'ownershipInfoKey': 'root:wheel'}

        proc = subprocess.Popen(command, bufsize=-1, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=createvariables)

        (unused, err) = proc.communicate()
        if proc.returncode:
            raise AutoNBIProcessError(err)

        self._cleanup_workdir(self._workdir)



class InstallSource(object):
    INSTALLER_TYPE_ESD = 'esd'
    INSTALLER_TYPE_RECOVERY = 'recovery'
    INSTALLER_TYPE_NETINSTALL = 'netinstall'

    def __init__(self, installer_path, installer_type=INSTALLER_TYPE_ESD):
        self._path = installer_path
        self._type = installer_type

        if self._type == InstallSource.INSTALLER_TYPE_ESD or self._type == InstallSource.INSTALLER_TYPE_NETINSTALL:
            self._dmg = Dmg(installer_path)
        else:
            self._dmg = None

    @property
    def path(self):
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
                    return cls(install_esd_path, 'esd')
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


class processNBI(object):
    """The processNBI class provides the makerw(), modify() and close()
        functions. All functions serve to make modifications to an NBI
        created by createnbi()"""

    # Don't think we need this.
    def __init__(self, customfolder=None, enablepython=False, enableruby=False, utilplist=False, vncpassword=None):
        super(processNBI, self).__init__()
        self.customfolder = customfolder
        self.enablepython = enablepython
        self.enableruby = enableruby
        self.utilplist = utilplist
        self.hdiutil = '/usr/bin/hdiutil'
        self.vncpassword = vncpassword

    # Make the provided NetInstall.dmg r/w by mounting it with a shadow file
    def makerw(self, netinstallpath):
        # Call mountdmg() with the use_shadow option set to True
        nbimount, nbishadow = mountdmg(netinstallpath, use_shadow=True)

        # Send the mountpoint and shadow file back to the caller
        return nbimount[0], nbishadow

    # Handle the addition of system frameworks like Python and Ruby using the
    #   OS X installer source
    # def enableframeworks(self, source, shadow):



    def dmgresize(self, resize_source, shadow_file=None, size=None):

        print "Will resize DMG at mount: %s" % resize_source

        if shadow_file:
            return [self.hdiutil, 'resize',
                    '-size', size,
                    '-shadow', shadow_file,
                    resize_source]
        else:
            proc = subprocess.Popen(['/usr/bin/hdiutil', 'resize', '-limits', resize_source],
                                    bufsize=-1, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE)

            (output, err) = proc.communicate()

            size = output.split('\t')[0]

            return [self.hdiutil, 'resize',
                    '-size', '%sb' % size, resize_source]

    def xarextract(self, xar_source, sysplatform):

        if 'darwin' in sysplatform:
            return ['/usr/bin/xar', '-x',
                    '-f', xar_source,
                    'Payload',
                    '-C', TMPDIR]
        else:
            # TO-DO: decompress xz lzma with Python
            pass

    def cpioextract(self, cpio_archive, pattern):
        return ['/usr/bin/cpio -idmu --quiet -I %s %s' % (cpio_archive, pattern)]

    def xzextract(self, xzexec, xzfile):
        return ['%s -d %s' % (xzexec, xzfile)]

    def getfiletype(self, filepath):
        return ['/usr/bin/file', filepath]

    # Code for parse_pbzx from https://gist.github.com/pudquick/ff412bcb29c9c1fa4b8d
    # Further write-up: https://gist.github.com/pudquick/29fcfe09c326a9b96cf5
    def seekread(self, f, offset=None, length=0, relative=True):
        if (offset != None):
            # offset provided, let's seek
            f.seek(offset, [0, 1, 2][relative])
        if (length != 0):
            return f.read(length)

    def parse_pbzx(self, pbzx_path):
        import struct

        archivechunks = []
        section = 0
        xar_out_path = '%s.part%02d.cpio.xz' % (pbzx_path, section)
        f = open(pbzx_path, 'rb')
        # pbzx = f.read()
        # f.close()
        magic = self.seekread(f, length=4)
        if magic != 'pbzx':
            raise "Error: Not a pbzx file"
        # Read 8 bytes for initial flags
        flags = self.seekread(f, length=8)
        # Interpret the flags as a 64-bit big-endian unsigned int
        flags = struct.unpack('>Q', flags)[0]
        xar_f = open(xar_out_path, 'wb')
        archivechunks.append(xar_out_path)
        while (flags & (1 << 24)):
            # Read in more flags
            flags = self.seekread(f, length=8)
            flags = struct.unpack('>Q', flags)[0]
            # Read in length
            f_length = self.seekread(f, length=8)
            f_length = struct.unpack('>Q', f_length)[0]
            xzmagic = self.seekread(f, length=6)
            if xzmagic != '\xfd7zXZ\x00':
                # This isn't xz content, this is actually _raw decompressed cpio_ chunk of 16MB in size...
                # Let's back up ...
                self.seekread(f, offset=-6, length=0)
                # ... and split it out ...
                f_content = self.seekread(f, length=f_length)
                section += 1
                decomp_out = '%s.part%02d.cpio' % (pbzx_path, section)
                g = open(decomp_out, 'wb')
                g.write(f_content)
                g.close()
                archivechunks.append(decomp_out)
                # Now to start the next section, which should hopefully be .xz (we'll just assume it is ...)
                xar_f.close()
                section += 1
                new_out = '%s.part%02d.cpio.xz' % (pbzx_path, section)
                xar_f = open(new_out, 'wb')
                archivechunks.append(new_out)
            else:
                f_length -= 6
                # This part needs buffering
                f_content = self.seekread(f, length=f_length)
                tail = self.seekread(f, offset=-2, length=2)
                xar_f.write(xzmagic)
                xar_f.write(f_content)
                if tail != 'YZ':
                    xar_f.close()
                    raise "Error: Footer is not xar file footer"

        try:
            f.close()
            xar_f.close()
        except:
            pass

        return archivechunks

    def processframeworkpayload(self, payloadsource, payloadtype, cpio_archive):
        # Check filetype of the Payload, 10.10 adds a pbzx wrapper
        if payloadtype.startswith('data'):
            # This is most likely pbzx-wrapped, unwrap it
            print("Payload %s is PBZX-wrapped, unwrapping..." % payloadsource)
            chunks = self.parse_pbzx(payloadsource)
            os.remove(payloadsource)
            fout = file(os.path.join(TMPDIR, cpio_archive), 'wb')

            for xzfile in chunks:
                if '.xz' in xzfile and os.path.getsize(xzfile) > 0:
                    print('Decompressing %s' % xzfile)

                    xzexec = find_executable('xz', '/usr/local/bin:/opt/bin:/usr/bin:/bin:/usr/sbin:/sbin')

                    if xzexec is not None:
                        print("Found xz executable at %s..." % xzexec)
                        result = self.runcmd(self.xzextract(xzexec, xzfile), cwd=TMPDIR)
                    else:
                        print("No xz executable found, using decompress()")
                        result = decompress(xzfile, xzfile.strip('.xz'))
                        os.remove(xzfile)

                    fin = file(xzfile.strip('.xz'), 'rb')
                    print("-------------------------------------------------------------------------")
                    print("Concatenating %s" % cpio_archive)
                    shutil.copyfileobj(fin, fout, 65536)
                    fin.close()
                    os.remove(fin.name)
                else:
                    fin = file(xzfile, 'rb')
                    print("-------------------------------------------------------------------------")
                    print("Concatenating %s" % cpio_archive)
                    shutil.copyfileobj(fin, fout, 65536)
                    fin.close()
                    os.remove(fin.name)

            fout.close()

        else:
            # No pbzx wrapper, rename and move to cpio extraction
            os.rename(payloadsource, cpio_archive)

    # Allows modifications to be made to a DMG previously made writable by
    #   processNBI.makerw()
    def modify(self, nbimount, dmgpath, nbishadow, installersource):

        addframeworks = []
        if self.enablepython:
            addframeworks.append('python')
        if self.enableruby:
            addframeworks.append('ruby')

        # Define the needed source PKGs for our frameworks
        if isHighSierra:
            # In High Sierra pretty much everything is in Core. New name. Same contents.
            # We also need to add libssl as it's no longer standard.
            payloads = {'python': {'sourcepayloads': ['Core'],
                                   'regex': '\"*Py*\" \"*py*\" \"*libssl*\" \"*libcrypto*\" \"*libffi.dylib*\" \"*libexpat*\"'},
                        'ruby': {'sourcepayloads': ['Core'],
                                 'regex': '\"*ruby*\" \"*lib*ruby*\" \"*Ruby.framework*\"  \"*libssl*\"'}
                        }

        elif isSierra:
            # In Sierra pretty much everything is in Essentials.
            # We also need to add libssl as it's no longer standard.
            payloads = {'python': {'sourcepayloads': ['Essentials'],
                                   'regex': '\"*Py*\" \"*py*\" \"*libssl*\" \"*libffi.dylib*\" \"*libexpat*\"'},
                        'ruby': {'sourcepayloads': ['Essentials'],
                                 'regex': '\"*ruby*\" \"*lib*ruby*\" \"*Ruby.framework*\"  \"*libssl*\"'}
                        }
        elif isElCap:
            # In ElCap pretty much everything is in Essentials.
            # We also need to add libssl as it's no longer standard.
            payloads = {'python': {'sourcepayloads': ['Essentials'],
                                   'regex': '\"*Py*\" \"*py*\" \"*libssl*\"'},
                        'ruby': {'sourcepayloads': ['Essentials'],
                                 'regex': '\"*ruby*\" \"*lib*ruby*\" \"*Ruby.framework*\"  \"*libssl*\"'}
                        }
        else:
            payloads = {'python': {'sourcepayloads': ['BSD'],
                                   'regex': '\"*Py*\" \"*py*\"'},
                        'ruby': {'sourcepayloads': ['BSD', 'Essentials'],
                                 'regex': '\"*ruby*\" \"*lib*ruby*\" \"*Ruby.framework*\"'}
                        }
        # Set 'modifybasesystem' if any frameworks are to be added, we're building
        #   an ElCap NBI or if we're adding a custom Utilites plist
        # modifybasesystem = (
        # len(addframeworks) > 0 or isElCap or isSierra or isHighSierra or self.utilplist or self.vncpassword)

        # If we need to make modifications to BaseSystem.dmg we mount it r/w
        if modifybasesystem:
            # Setup the BaseSystem.dmg for modification by mounting it with a shadow
            # and resizing the shadowed image, 10 GB should be good. We'll shrink
            # it again later.
            if not isHighSierra:
                basesystemshadow = os.path.join(TMPDIR, 'BaseSystem.shadow')
                basesystemdmg = os.path.join(nbimount, 'BaseSystem.dmg')
            else:
                print("Install source is 10.13 or newer, BaseSystem.dmg is in an alternate location...")
                basesystemshadow = os.path.join(TMPDIR, 'BaseSystem.shadow')
                basesystemdmg = os.path.join(nbimount,
                                             'Install macOS High Sierra Beta.app/Contents/SharedSupport/BaseSystem.dmg')

            print("Running self.dmgresize...")
            result = self.runcmd(self.dmgresize(basesystemdmg, basesystemshadow, '8G'))
            print("Running self.dmgattach...")
            plist = self.runcmd(attach_dmg(basesystemdmg, basesystemshadow, TMPDIR))

            # print("Contents of plist:\n------\n%s\n------" % plist)

            basesystemplist = plistlib.readPlistFromString(plist)

            # print("Contents of basesystemplist:\n------\n%s\n------" % basesystemplist)

            for entity in basesystemplist['system-entities']:
                if 'mount-point' in entity:
                    basesystemmountpoint = entity['mount-point']

        # OS X 10.11 El Capitan triggers an Installer Progress app which causes
        #   custom installer workflows using 'Packages/Extras' to fail so
        #   we need to nix it. Thanks, Apple.
        if isSierra or isHighSierra:
            rcdotinstallpath = os.path.join(basesystemmountpoint, 'private/etc/rc.install')
            rcdotinstallro = open(rcdotinstallpath, "r")
            rcdotinstalllines = rcdotinstallro.readlines()
            rcdotinstallro.close()
            rcdotinstallw = open(rcdotinstallpath, "w")

            # The binary changed to launchprogresswindow for Sierra, still killing it.
            # Sierra also really wants to launch the Language Chooser which kicks off various install methods.
            # This can mess with some third party imaging tools (Imagr) so we simply change it to 'echo'
            #   so it simply echoes the args Language Chooser would be called with instead of launching LC, and nothing else.
            for line in rcdotinstalllines:
                # Remove launchprogresswindow
                if line.rstrip() != "/System/Installation/CDIS/launchprogresswindow &":
                    # Rewrite $LAUNCH as /bin/echo
                    if line.rstrip() == "LAUNCH=\"/System/Library/CoreServices/Language Chooser.app/Contents/MacOS/Language Chooser\"":
                        rcdotinstallw.write("LAUNCH=/bin/echo")
                        # Add back ElCap code to source system imaging extras files
                        rcdotinstallw.write(
                            "\nif [ -x /System/Installation/Packages/Extras/rc.imaging ]; then\n\t/System/Installation/Packages/Extras/rc.imaging\nfi")
                    else:
                        rcdotinstallw.write(line)

            rcdotinstallw.close()

        if isElCap:
            rcdotinstallpath = os.path.join(basesystemmountpoint, 'private/etc/rc.install')
            rcdotinstallro = open(rcdotinstallpath, "r")
            rcdotinstalllines = rcdotinstallro.readlines()
            rcdotinstallro.close()
            rcdotinstallw = open(rcdotinstallpath, "w")
            for line in rcdotinstalllines:
                if line.rstrip() != "/System/Library/CoreServices/Installer\ Progress.app/Contents/MacOS/Installer\ Progress &":
                    rcdotinstallw.write(line)
            rcdotinstallw.close()

        if isElCap or isSierra or isHighSierra:
            # Reports of slow NetBoot speeds with 10.11+ have lead others to
            #   remove various launch items that seem to cause this. Remove some
            #   of those as a stab at speeding things back up.
            baseldpath = os.path.join(basesystemmountpoint, 'System/Library/LaunchDaemons')
            launchdaemonstoremove = ['com.apple.locationd.plist',
                                     'com.apple.lsd.plist',
                                     'com.apple.tccd.system.plist',
                                     'com.apple.ocspd.plist',
                                     'com.apple.InstallerProgress.plist']

            for ld in launchdaemonstoremove:
                ldfullpath = os.path.join(baseldpath, ld)
                if os.path.exists(ldfullpath):
                    os.unlink(ldfullpath)
        # Handle any custom content to be added, customfolder has a value
        if self.customfolder:
            print("-------------------------------------------------------------------------")
            print "Modifying NetBoot volume at %s" % nbimount

            # Sets up which directory to process. This is a simple version until
            # we implement something more full-fledged, based on a config file
            # or other user-specified source of modifications.
            processdir = os.path.join(nbimount, ''.join(self.customfolder.split('/')[-1:]))

            if isHighSierra:
                processdir = os.path.join(basesystemmountpoint, 'System/Installation',
                                          ''.join(self.customfolder.split('/')[-1:]))

            # Remove folder being modified - distutils appears to have the easiest
            # method to recursively delete a folder. Same with recursively copying
            # back its replacement.
            print('About to process ' + processdir + ' for replacement...')
            if os.path.lexists(processdir):
                if os.path.isdir(processdir):
                    print('Removing directory %s' % processdir)
                    distutils.dir_util.remove_tree(processdir)
                # This may be a symlink or other non-dir instead, so double-tap just in case
                else:
                    print('Removing file or symlink %s' % processdir)
                    os.unlink(processdir)

            # Copy over the custom folder contents. If the folder didn't exists
            # we can skip the above removal and get straight to copying.
            # os.mkdir(processdir)
            print('Copying ' + self.customfolder + ' to ' + processdir + '...')
            distutils.dir_util.copy_tree(self.customfolder, processdir)
            print('Done copying ' + self.customfolder + ' to ' + processdir + '...')

            # High Sierra 10.13 contains the InstallESD.dmg as part of the installer app, remove it to free up space
            if isHighSierra:
                if os.path.exists(os.path.join(nbimount,
                                               'Install macOS High Sierra Beta.app/Contents/SharedSupport/InstallESD.dmg')):
                    os.unlink(os.path.join(nbimount,
                                           'Install macOS High Sierra Beta.app/Contents/SharedSupport/InstallESD.dmg'))

        # Is Python or Ruby being added? If so, do the work.
        if addframeworks:

            # Create an empty list to record cached Payload resources
            havepayload = []

            # Loop through the frameworks we've been asked to include
            for framework in addframeworks:

                # Get the cpio glob pattern/regex to extract the framework
                regex = payloads[framework]['regex']
                print("-------------------------------------------------------------------------")
                print("Adding %s framework from %s to NBI at %s" % (framework.capitalize(), installersource, nbimount))

                # Loop through all possible source payloads for this framework
                for payload in payloads[framework]['sourcepayloads']:

                    payloadsource = os.path.join(TMPDIR, 'Payload')
                    # os.rename(payloadsource, payloadsource + '-' + payload)
                    # payloadsource = payloadsource + '-' + payload
                    cpio_archive = payloadsource + '-' + payload + '.cpio.xz'
                    xar_source = os.path.join(installersource, 'Packages', payload + '.pkg')

                    print("Cached payloads: %s" % havepayload)

                    # Check whether we already have this Payload from a previous run
                    if cpio_archive not in havepayload:

                        print("-------------------------------------------------------------------------")
                        print("No cache, extracting %s" % xar_source)

                        # Extract Payload(s) from desired OS X installer package
                        sysplatform = sys.platform
                        self.runcmd(self.xarextract(xar_source, sysplatform))

                        # Determine the Payload file type using 'file'
                        payloadtype = self.runcmd(self.getfiletype(payloadsource)).split(': ')[1]

                        print("Processing payloadsource %s" % payloadsource)
                        result = self.processframeworkpayload(payloadsource, payloadtype, cpio_archive)

                        # Log that we have this cpio_archive in case we need it later
                        if cpio_archive not in havepayload:
                            # print("Adding cpio_archive %s to havepayload" % cpio_archive)
                            havepayload.append(cpio_archive)

                    # Extract our needed framework bits from CPIO arch
                    #   using shell globbing pattern(s)
                    print("-------------------------------------------------------------------------")
                    print("Processing cpio_archive %s" % cpio_archive)
                    self.runcmd(self.cpioextract(cpio_archive, regex),
                                cwd=basesystemmountpoint)

            for cpio_archive in havepayload:
                print("-------------------------------------------------------------------------")
                print("Removing cached Payload %s" % cpio_archive)
                if os.path.exists(cpio_archive):
                    os.remove(cpio_archive)

        # Add custom Utilities.plist if passed as an argument
        if self.utilplist:
            print("-------------------------------------------------------------------------")
            print("Adding custom Utilities.plist from %s" % self.utilplist)
            try:
                shutil.copyfile(os.path.abspath(self.utilplist), os.path.join(basesystemmountpoint,
                                                                              'System/Installation/CDIS/OS X Utilities.app/Contents/Resources/Utilities.plist'))
            except:
                print("Failed to add custom Utilites plist from %s" % self.utilplist)

        if self.vncpassword:
            print("-------------------------------------------------------------------------")
            print("Setting VNC password")
            try:
                with open(os.path.join(basesystemmountpoint, 'Library/Preferences/com.apple.VNCSettings.txt'),
                          'wb') as fd:
                    fd.write(vnc_password(self.vncpassword))
            except:
                print("Failed to set VNC password")

        if modifybasesystem and basesystemmountpoint:

            # Done adding frameworks to BaseSystem, unmount and convert
            # detachresult = self.runcmd(self.dmgdetach(basesystemmountpoint))
            detachresult = unmountdmg(basesystemmountpoint)

            # Set some DMG conversion targets for later
            basesystemrw = os.path.join(TMPDIR, 'BaseSystemRW.dmg')
            basesystemro = os.path.join(TMPDIR, 'BaseSystemRO.dmg')

            # Convert to UDRW, the only format that will allow resizing the BaseSystem.dmg later
            convertresult = self.runcmd(self.dmgconvert(basesystemdmg, basesystemrw, basesystemshadow, 'UDRW'))
            # Delete the original DMG, we need to clear up some space where possible
            os.remove(basesystemdmg)

            # Resize BaseSystem.dmg to its smallest possible size (using hdiutil resize -limits)
            resizeresult = self.runcmd(self.dmgresize(basesystemrw))

            # Convert again, to UDRO, to shrink the final DMG size more
            convertresult = self.runcmd(self.dmgconvert(basesystemrw, basesystemro, None, 'UDRO'))

            # Rename the finalized DMG to its intended name BaseSystem.dmg
            shutil.copyfile(basesystemro, basesystemdmg)

            # For High Sierra, remove the chunklists for InstallESD and BaseSystem since they won't match
            # This includes removing chunklist entry from InstallInfo.plist
            if isHighSierra:
                if os.path.exists(os.path.join(nbimount,
                                               'Install macOS High Sierra Beta.app/Contents/SharedSupport/BaseSystem.chunklist')):
                    os.unlink(os.path.join(nbimount,
                                           'Install macOS High Sierra Beta.app/Contents/SharedSupport/BaseSystem.chunklist'))
                if os.path.exists(os.path.join(nbimount,
                                               'Install macOS High Sierra Beta.app/Contents/SharedSupport/InstallESD.chunklist')):
                    os.unlink(os.path.join(nbimount,
                                           'Install macOS High Sierra Beta.app/Contents/SharedSupport/InstallESD.chunklist'))

                installinfoplist = plistlib.readPlist(os.path.join(nbimount,
                                                                   'Install macOS High Sierra Beta.app/Contents/SharedSupport/InstallInfo.plist'))
                if installinfoplist['System Image Info'].get('chunklistid'):
                    del installinfoplist['System Image Info']['chunklistid']
                if installinfoplist['System Image Info'].get('chunklistURL'):
                    del installinfoplist['System Image Info']['chunklistURL']
                plistlib.writePlist(installinfoplist, os.path.join(nbimount,
                                                                   'Install macOS High Sierra Beta.app/Contents/SharedSupport/InstallInfo.plist'))

        # We're done, unmount the outer NBI DMG.
        unmountdmg(nbimount)

        # Convert modified DMG to .sparseimage, this will shrink the image
        # automatically after modification.
        print("-------------------------------------------------------------------------")
        print "Sealing DMG at path %s" % (dmgpath)
        dmgfinal = convertdmg(dmgpath, nbishadow)
        # print('Got back final DMG as ' + dmgfinal + ' from convertdmg()...')

        # Do some cleanup, remove original DMG, its shadow file and rename
        # .sparseimage to NetInstall.dmg
        os.remove(nbishadow)
        os.remove(dmgpath)
        os.rename(dmgfinal, dmgpath)


TMPDIR = None
sysidenabled = []


def main():
    """Main routine"""

    global TMPDIR
    global sysidenabled

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
    parser.add_argument('--index', default=5000, dest='nbiindex', type='int',
                        help='Optional. Set a custom Index for the NBI. Default is 5000.')
    parser.add_argument('--type', default='NFS', dest='nbitype',
                        help='Optional. Set a custom Type for the NBI. HTTP or NFS. Default is NFS.')
    parser.add_argument('--sysid-enable', dest='sysidenabled', action='append', type='str',
                        help='Optional. Whitelist a given System ID (\'MacBookPro10,1\') Can be '
                             'defined multiple times. WARNING: This will enable ONLY the listed '
                             'System IDs. Systems not explicitly marked as enabled will not be '
                             'able to boot from this NBI.')
    parser.add_argument('--vnc-password', dest='vncpassword',
                        help='Optional. Enable and set the VNC password to the given password.')

    # Parse the provided options
    arguments = parser.parse_args()

    # Are we root?
    if os.getuid() != 0:
        parser.print_usage()
        print >> sys.stderr, 'This tool requires sudo or root privileges.'
        exit(-1)

    if not os.path.exists(arguments.source):
        print >> sys.stderr, 'The given source at %s does not exist.' % arguments.source
        exit(-1)

    buildenv = BuildEnvironment.from_host()

    # Set 'modifydmg' if any of 'addcustom', 'addpython' or 'addruby' are true
    # addcustom = len(customfolder) > 0
    # modifynbi = (addcustom or addpython or addruby or isElCap or isSierra or isHighSierra or options.vncpassword)

    # Spin up a tmp dir for mounting
    TMPDIR = tempfile.mkdtemp(dir=TMPDIR)

    # Now we start a typical run of the tool, first locate one or more
    #   installer app candidates
    try:
        source = InstallSource.from_path(arguments.source)
    except IOError as e:
        print(e.message)
        sys.exit(1)

    if source.is_netinstall or source.is_esd:
        # If the destination path isn't absolute, we make it so to prevent errors
        if not arguments.destination.startswith('/'):
            destination = os.path.abspath(arguments.destination)

        # Prep the build root - create it if it's not there
        if not os.path.exists(arguments.destination):
            os.mkdir(arguments.destination)

        print 'Mounting ' + source.path
        source.dmg.mount()
        mount = source.dmg_mount_point

        if buildenv.is_high_sierra:
            osversion, osbuild, unused = getosversioninfo(os.path.join(mount, 'Contents/SharedSupport'))
        else:
            osversion, osbuild, unused = getosversioninfo(mount)

        if LooseVersion(buildenv.version_major) < '10.12':
            description = "OS X %s - %s" % (osversion, osbuild)
        else:
            description = "macOS %s - %s" % (osversion, osbuild)

        # Prep our build root for NBI creation
        # print 'Prepping ' + destination + ' with source mounted at ' + mount
        # prepworkdir(destination)

        # Now move on to the actual NBI creation
        # print 'Creating NBI at ' + destination
        # print 'Base NBI Operating System is ' + osversion
        # createnbi(destination, description, osversion, name, enablenbi, nbiindex, nbitype, isdefault, mount, arguments.source)
        builder = NBIBuilder(buildenv, mount, arguments.destination).description(description)

        if arguments.enablenbi:
            builder = builder.enable()

        if arguments.nbiindex:
            builder = builder.index(arguments.nbiindex)

        if arguments.isdefault:
            builder = builder.default()

        builder.build()

    # Make our modifications if any were provided from the CLI
    # if modifynbi:
    #     if addcustom:
    #         try:
    #             if os.path.isdir(customfolder):
    #                 customfolder = os.path.abspath(customfolder)
    #         except IOError:
    #             print("%s is not a valid path - unable to proceed." % customfolder)
    #             sys.exit(1)
    #
    #     # Path to the NetInstall.dmg
    #     if shouldcreatenbi:
    #         netinstallpath = os.path.join(destination, name + '.nbi', 'NetInstall.dmg')
    #     else:
    #         netinstallpath = arguments.source
    #         mount = None
    #
    #     # Initialize a new processNBI() instance as 'nbi'
    #     nbi = processNBI(customfolder, addpython, addruby, utilplist)
    #
    #     # Run makerw() to enable modifications
    #     nbimount, nbishadow = nbi.makerw(netinstallpath)
    #
    #     print("NBI mounted at %s" % nbimount)
    #
    #     nbi.modify(nbimount, netinstallpath, nbishadow, mount)
    #
    #     # We're done, unmount all the things
    #     if shouldcreatenbi:
    #         unmountdmg(mount)
    #
    #     distutils.dir_util.remove_tree(TMPDIR)
    #
    #     print("-------------------------------------------------------------------------")
    #     print 'Modifications complete...'
    #     print 'Done.'
    # else:
    #     # We're done, unmount all the things
    #     unmountdmg(mount)
    #     distutils.dir_util.remove_tree(TMPDIR)
    #
    #     print("-------------------------------------------------------------------------")
    #     print 'No modifications will be made...'
    #     print 'Done.'


if __name__ == '__main__':
    main()
