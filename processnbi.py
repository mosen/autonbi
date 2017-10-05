from distutils.spawn import find_executable
from ctypes import CDLL, Structure, c_void_p, c_size_t, c_uint, c_uint32, c_uint64, create_string_buffer, addressof, \
    sizeof, byref

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

