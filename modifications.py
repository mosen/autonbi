import logging
import os.path

logger = logging.getLogger(__name__)

Python_Framework = {
    '10.13': {'sourcepayloads': ['Core'],
              'regex': '\"*Py*\" \"*py*\" \"*libssl*\" \"*libcrypto*\" \"*libffi.dylib*\" \"*libexpat*\"'},
    '10.12': {'sourcepayloads': ['Essentials'],
              'regex': '\"*Py*\" \"*py*\" \"*libssl*\" \"*libffi.dylib*\" \"*libexpat*\"'},
    '10.11': {'sourcepayloads': ['Essentials'],
              'regex': '\"*Py*\" \"*py*\" \"*libssl*\"'},
    'default': {'sourcepayloads': ['BSD'],
                'regex': '\"*Py*\" \"*py*\"'}
}

Ruby_Framework = {
    '10.13': {'sourcepayloads': ['Core'],
              'regex': '\"*ruby*\" \"*lib*ruby*\" \"*Ruby.framework*\"  \"*libssl*\"'},
    '10.12': {'sourcepayloads': ['Essentials'],
              'regex': '\"*ruby*\" \"*lib*ruby*\" \"*Ruby.framework*\"  \"*libssl*\"'},
    '10.11': {'sourcepayloads': ['Essentials'],
              'regex': '\"*ruby*\" \"*lib*ruby*\" \"*Ruby.framework*\"  \"*libssl*\"'},
    'default': {'sourcepayloads': ['BSD', 'Essentials'],
                'regex': '\"*ruby*\" \"*lib*ruby*\" \"*Ruby.framework*\"'}
}

def modify_custom_folder(mount_point, custom_folder):
    logger.debug("Modifying NetBoot volume at %s", mount_point)
    # print("-------------------------------------------------------------------------")
    # print "Modifying NetBoot volume at %s" % nbimount
    #
    # # Sets up which directory to process. This is a simple version until
    # # we implement something more full-fledged, based on a config file
    # # or other user-specified source of modifications.
    # processdir = os.path.join(nbimount, ''.join(self.customfolder.split('/')[-1:]))
    #
    # if isHighSierra:
    #     processdir = os.path.join(basesystemmountpoint, 'System/Installation',
    #                               ''.join(self.customfolder.split('/')[-1:]))
    #
    # # Remove folder being modified - distutils appears to have the easiest
    # # method to recursively delete a folder. Same with recursively copying
    # # back its replacement.
    # print('About to process ' + processdir + ' for replacement...')
    # if os.path.lexists(processdir):
    #     if os.path.isdir(processdir):
    #         print('Removing directory %s' % processdir)
    #         distutils.dir_util.remove_tree(processdir)
    #     # This may be a symlink or other non-dir instead, so double-tap just in case
    #     else:
    #         print('Removing file or symlink %s' % processdir)
    #         os.unlink(processdir)
    #
    # # Copy over the custom folder contents. If the folder didn't exists
    # # we can skip the above removal and get straight to copying.
    # # os.mkdir(processdir)
    # print('Copying ' + self.customfolder + ' to ' + processdir + '...')
    # distutils.dir_util.copy_tree(self.customfolder, processdir)
    # print('Done copying ' + self.customfolder + ' to ' + processdir + '...')
    #
    # # High Sierra 10.13 contains the InstallESD.dmg as part of the installer app, remove it to free up space
    # if isHighSierra:
    #     if os.path.exists(os.path.join(nbimount,
    #                                    'Install macOS High Sierra Beta.app/Contents/SharedSupport/InstallESD.dmg')):
    #         os.unlink(os.path.join(nbimount,
    #                                'Install macOS High Sierra Beta.app/Contents/SharedSupport/InstallESD.dmg'))
    #

def modify_rc_install(basesystem_mount_point, major_version='10.13'):
    """
    ..note:: OS X 10.11 El Capitan triggers an Installer Progress app which causes
        custom installer workflows using 'Packages/Extras' to fail so
        we need to nix it. Thanks, Apple.

    :param basesystem_mount_point:
    :param major_version:
    :return:
    """
    rc_install_path = os.path.join(basesystem_mount_point, 'private', 'etc', 'rc.install')
    with open(rc_install_path, 'r') as fd:
        rc_install_lines = fd.readlines()


    #
    # if isSierra or isHighSierra:
    #     rcdotinstallpath = os.path.join(basesystemmountpoint, 'private/etc/rc.install')
    #     rcdotinstallro = open(rcdotinstallpath, "r")
    #     rcdotinstalllines = rcdotinstallro.readlines()
    #     rcdotinstallro.close()
    #     rcdotinstallw = open(rcdotinstallpath, "w")
    #
    #     # The binary changed to launchprogresswindow for Sierra, still killing it.
    #     # Sierra also really wants to launch the Language Chooser which kicks off various install methods.
    #     # This can mess with some third party imaging tools (Imagr) so we simply change it to 'echo'
    #     #   so it simply echoes the args Language Chooser would be called with instead of launching LC, and nothing else.
    #     for line in rcdotinstalllines:
    #         # Remove launchprogresswindow
    #         if line.rstrip() != "/System/Installation/CDIS/launchprogresswindow &":
    #             # Rewrite $LAUNCH as /bin/echo
    #             if line.rstrip() == "LAUNCH=\"/System/Library/CoreServices/Language Chooser.app/Contents/MacOS/Language Chooser\"":
    #                 rcdotinstallw.write("LAUNCH=/bin/echo")
    #                 # Add back ElCap code to source system imaging extras files
    #                 rcdotinstallw.write(
    #                     "\nif [ -x /System/Installation/Packages/Extras/rc.imaging ]; then\n\t/System/Installation/Packages/Extras/rc.imaging\nfi")
    #             else:
    #                 rcdotinstallw.write(line)
    #
    #     rcdotinstallw.close()
    #
    # if isElCap:
    #     rcdotinstallpath = os.path.join(basesystemmountpoint, 'private/etc/rc.install')
    #     rcdotinstallro = open(rcdotinstallpath, "r")
    #     rcdotinstalllines = rcdotinstallro.readlines()
    #     rcdotinstallro.close()
    #     rcdotinstallw = open(rcdotinstallpath, "w")
    #     for line in rcdotinstalllines:
    #         if line.rstrip() != "/System/Library/CoreServices/Installer\ Progress.app/Contents/MacOS/Installer\ Progress &":
    #             rcdotinstallw.write(line)
    #     rcdotinstallw.close()


def modify_speedup(basesystem_mount_point, major_version='10.13'):
    pass
    # if isElCap or isSierra or isHighSierra:
    #     # Reports of slow NetBoot speeds with 10.11+ have lead others to
    #     #   remove various launch items that seem to cause this. Remove some
    #     #   of those as a stab at speeding things back up.
    #     baseldpath = os.path.join(basesystemmountpoint, 'System/Library/LaunchDaemons')
    #     launchdaemonstoremove = ['com.apple.locationd.plist',
    #                              'com.apple.lsd.plist',
    #                              'com.apple.tccd.system.plist',
    #                              'com.apple.ocspd.plist',
    #                              'com.apple.InstallerProgress.plist']
    #
    #     for ld in launchdaemonstoremove:
    #         ldfullpath = os.path.join(baseldpath, ld)
    #         if os.path.exists(ldfullpath):
    #             os.unlink(ldfullpath)



def modify_utilities_plist(basesystem_mount_point, major_version='10.13'):
    pass
    # Add custom Utilities.plist if passed as an argument
    # if self.utilplist:
    #     print("-------------------------------------------------------------------------")
    #     print("Adding custom Utilities.plist from %s" % self.utilplist)
    #     try:
    #         shutil.copyfile(os.path.abspath(self.utilplist), os.path.join(basesystemmountpoint,
    #                                                                       'System/Installation/CDIS/OS X Utilities.app/Contents/Resources/Utilities.plist'))
    #     except:
    #         print("Failed to add custom Utilites plist from %s" % self.utilplist)


def modify_add_frameworks(basesystem_mount_point, major_version='10.13'):
    pass
    # Is Python or Ruby being added? If so, do the work.
    # if addframeworks:
    #
    #     # Create an empty list to record cached Payload resources
    #     havepayload = []
    #
    #     # Loop through the frameworks we've been asked to include
    #     for framework in addframeworks:
    #
    #         # Get the cpio glob pattern/regex to extract the framework
    #         regex = payloads[framework]['regex']
    #         print("-------------------------------------------------------------------------")
    #         print("Adding %s framework from %s to NBI at %s" % (framework.capitalize(), installersource, nbimount))
    #
    #         # Loop through all possible source payloads for this framework
    #         for payload in payloads[framework]['sourcepayloads']:
    #
    #             payloadsource = os.path.join(TMPDIR, 'Payload')
    #             # os.rename(payloadsource, payloadsource + '-' + payload)
    #             # payloadsource = payloadsource + '-' + payload
    #             cpio_archive = payloadsource + '-' + payload + '.cpio.xz'
    #             xar_source = os.path.join(installersource, 'Packages', payload + '.pkg')
    #
    #             print("Cached payloads: %s" % havepayload)
    #
    #             # Check whether we already have this Payload from a previous run
    #             if cpio_archive not in havepayload:
    #
    #                 print("-------------------------------------------------------------------------")
    #                 print("No cache, extracting %s" % xar_source)
    #
    #                 # Extract Payload(s) from desired OS X installer package
    #                 sysplatform = sys.platform
    #                 self.runcmd(self.xarextract(xar_source, sysplatform))
    #
    #                 # Determine the Payload file type using 'file'
    #                 payloadtype = self.runcmd(self.getfiletype(payloadsource)).split(': ')[1]
    #
    #                 print("Processing payloadsource %s" % payloadsource)
    #                 result = self.processframeworkpayload(payloadsource, payloadtype, cpio_archive)
    #
    #                 # Log that we have this cpio_archive in case we need it later
    #                 if cpio_archive not in havepayload:
    #                     # print("Adding cpio_archive %s to havepayload" % cpio_archive)
    #                     havepayload.append(cpio_archive)
    #
    #             # Extract our needed framework bits from CPIO arch
    #             #   using shell globbing pattern(s)
    #             print("-------------------------------------------------------------------------")
    #             print("Processing cpio_archive %s" % cpio_archive)
    #             self.runcmd(self.cpioextract(cpio_archive, regex),
    #                         cwd=basesystemmountpoint)
    #
    #     for cpio_archive in havepayload:
    #         print("-------------------------------------------------------------------------")
    #         print("Removing cached Payload %s" % cpio_archive)
    #         if os.path.exists(cpio_archive):
    #             os.remove(cpio_archive)

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
