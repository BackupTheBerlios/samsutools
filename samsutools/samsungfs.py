#!/usr/bin/python
#
# File System for Samsung mobile phones.
# Copyright (c) 2007 Paulo Matias
# 
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are
# met:
#
#    1. Redistributions of source code must retain the above copyright
#       notice, this list of conditions and the following disclaimer.
#    2. Redistributions in binary form must reproduce the above copyright
#       notice, this list of conditions and the following disclaimer in the
#       documentation and/or other materials provided with the distribution.
#    3. The name of the author may not be used to endorse or promote
#       products derived from this software without specific prior written
#       permission.
#
# THIS SOFTWARE IS PROVIDED BY THE AUTHOR ``AS IS'' AND ANY EXPRESS OR
# IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES
# OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN
# NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
# SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED
# TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR
# PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
# LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
# NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#

import sys, os, tty, re, time, select, struct, zlib
import fuse, errno, stat

def copyright():
        print """
File System for Samsung mobile phones.
Copyright (c) 2007 Paulo Matias

This is a free software licensed under a BSD license AND
HAS NO WARRANTIES. See source code for details.

This is a STRONGLY EXPERIMENTAL SOFTWARE. Use only if you
can't setup your mobile phone for usb-storage or bluetooth.
"""

def usage():
    print """Usage: %s mount-point [dev=tty-device[, fuse-options]]
       dev=tty-device   TTY device the mobile phone is plugged into.
                        Defaults to /dev/ttyACM0.
""" % sys.argv[0]
    sys.exit(1)


class ModemDevice:
    __skip__ = 0
    
    def __init__(self, modem_device = '/dev/modem'):
        """ Open and init the device as a RAW tty. """
        self.__fd__ = os.open(modem_device, os.O_RDWR|os.O_NONBLOCK|os.O_NOCTTY)
        tty.setraw(self.__fd__)
        
    def __del__(self):
        os.close(self.__fd__)
        
    def send(self, data, skip_echo = True):
        """ Send data to device. Keep skip_echo True if you know the sent data will
        produce echo in the device. So it will be skipped in the next recv(). """
        os.write(self.__fd__, data)
        self.__skip__ = len(data)

    def recv(self, expect, timeout = 1.0):
        """ Read data from the device until one of the values of the expect list
        is found at the end of the stream or until timeout is reached. Returns a
        tuple (i, r) where i is the index at expect of the found value or None if
        timeout was reached, and r is the data read until the value expected. """
        
        max_time = time.time() + timeout  # Timestamp of the timeout time.
        buf = ''
        
        while 1:
            if len(select.select([self.__fd__], [], [], 0.1)[0]) == 1:
                # There is new data, read it.
                buf += os.read(self.__fd__, 1024)
                # Skip data if there is data to skip.
                if self.__skip__ != 0:
                    skip = min([self.__skip__, len(buf)])
                    buf = buf[skip:]
                    self.__skip__ -= skip
                # Check for one of the expected values at end of stream.
                for i in range(len(expect)):
                    s = expect[i]
                    if buf[-len(s):] == s:
                        buf = buf[:-len(s)]
                        return (i, buf)
            # Check for timeout.
            if time.time() > max_time:
                return (None, buf)
    

def sepdirfname(path):
    """ Separates dir from base filename. Returns a tuple (dir, filename). """
    i = path.rindex('/')
    dir = path[:i+1]
    filename = path[i+1:]
    return (dir, filename)
    
    
class UnexpectedResponse(Exception):
    """ Exception raised when unexpected data is received from modem. """
    def __init__(self, value):
        self.value = value
    def __str__(self):
        return repr(self.value)

    
class Samsung(ModemDevice):
    __curdir__ = '/'                        # Current dir we had cd to.
    __fileinfo_cache__ = {}                 # Cache for fileinfo() call.
    __fileinfo_cache_timeout__ = 10.0       # Seconds to consider the cache valid.
        
    def __init__(self, modem_device = '/dev/ttyACM0'):
        """ Inits Samsung mobile phone device and gather model name. """
        ModemDevice.__init__(self, modem_device)
        
        self.send('ATZ\r\n')
        (i, r) = self.recv(['OK\r\n'])
        if i != 0:
            raise UnexpectedResponse, r
        
        self.send('AT+CGMM\r\n')
        (i, r) = self.recv(['OK\r\n'])
        if (i != 0) or (r.find('SAMSUNG') == -1):
            raise UnexpectedResponse, r
        self.model = r.strip()
    
    def cd(self, dir = '/'):
        """ Change current dir. """
        if (len(dir) != 1) and (dir[-1:] == '/'): dir = dir[:-1]
        self.send('AT+FSCD="' + dir + '"\r\n')
        (i, r) = self.recv(['OK\r\n'])
        if i != 0:
            raise IOError, "Can't change dir."
        self.__curdir__ = dir
    
    def __get__flist__(self, expr, addtup=()):
        """ Returns a list of tuples gathered from expr regular expression for
        each line of the file list sent by mobile phone. The addtup parameter
        is added at the end of each tuple. """
        regex = re.compile(expr)
        flist = []
        while 1:
            # #OK# -> there is more data. OK -> end of data.
            (i, r) = self.recv(['#OK#\r\n', 'OK\r\n'], 0.5)
            for l in r.strip().split('\n'):
                try: flist.append(regex.match(l.strip()).groups() + addtup)
                except: pass
            if i != 0:
                return flist
            self.send('##>\r\n', False)   # Request more data.

    def ls(self, dir = None):
        """ List files at dir. Returns a list of (name, size, timestamp) tuples.
        Directories will have (size, timestamp) == (0, 0). """
        if dir == None: dir = self.__curdir__
        if (len(dir) != 1) and (dir[-1:] == '/'): dir = dir[:-1]
        # Perhaps directories are listed by AT+FSDI command.
        self.send('AT+FSDI="' + dir + '"\r\n')
        flist1 = self.__get__flist__('^\\+FSDI[^,]+,[^"]*"([^"]+)"', (0, 0))
        # Perhaps files are listed by AT+FSDL command.
        self.send('AT+FSDL="' + dir + '"\r\n')
        flist2 = self.__get__flist__('^\\+FSDL[^,]+,[^"]*"([^"]+)"[^,]*,[^,]+,[^,]+,[^,]+,[^,]+,([0-9]+)[^,]*,[^,]+,[^"]*"([^"]+)"')
        # Convert date & time to timestamp.
        re_time = re.compile('([0-9]+)/([0-9]+)/([0-9]+)\\s+([0-9]+):([0-9]+)([AP])M')
        for i in range(len(flist2)):
            (fname, fsize, fdate) = flist2[i]
            try:
                (d,m,y,th,tm,ampm) = re_time.match(fdate).groups()
                if ampm == 'P': th = int(th) + 12
                fdate = int(time.mktime((int(y),int(m),int(d),int(th),int(tm),0,0,0,0)))
            except:
                fdate = 0
            flist2[i] = (fname, int(fsize), fdate)
        # Concatenate two lists and return.
        return flist1 + flist2

    def fileinfo(self, path):
        """ Returns a tuple (size, timestamp) for file at path. Directories will
        have (size, timestamp) == (0, 0). """
        if path[-1:] == '/': path = path[:-1]
        if path == '': return (0, 0)   # Root dir.
        # Separate parent directory and filename.
        (dir, filename) = sepdirfname(path)
        # Check cache. Fill cache if empty or timeouted.
        if (self.__fileinfo_cache__.has_key(dir)) and (time.time() - self.__fileinfo_cache__[dir][0] < self.__fileinfo_cache_timeout__):
            flist = self.__fileinfo_cache__[dir][1]
        else:
            flist = {}
            for (fname, fsize, fdate) in self.ls(dir):
                flist[fname] = (fsize, fdate)
            self.__fileinfo_cache__[dir] = (time.time(), flist)
        # If not in cache, file does not exist.
        if not flist.has_key(filename):
            raise IOError, 'File not found.'
        # Return file info.
        return flist[filename]
        

    def rm(self, filename):
        """ Removes the file. Perhaps you have to cd() to directory before doing this. """
        self.send('AT+FSFE=0,"' + filename + '"\r\n')
        (i, r) = self.recv(['OK\r\n'])
        if i != 0:
            raise IOError, "Can't remove file."

    def get(self, filename):
        """ Gets the file and returns its contents. Perhaps you have to cd() to directory
        before doing this."""
        self.send('AT+FSFR=-1,"' + filename + '"\r\n')
        (i, r) = self.recv(['#OK#\r\n'])
        if i != 0:
            raise IOError, 'File not found.'
        self.send('##>\r\n', False)   # Request more data.
        buf = ''
        while 1:
            # #OK# -> there is more data. OK -> end of data.
            (i, r) = self.recv(['#OK#\r\n', '\r\nOK\r\n'])
            if i != 0:
                return buf
            r = r.split(',', 3)
            # Mobile phone sends signed CRC32 of data of current packet. Check it.
            if int(r[2]) != zlib.crc32(r[3]):
                raise IOError, 'CRC32 Error.'
            # Add received data to buffer.
            buf += r[3]
            # Request more data.
            self.send('##>\r\n', False)

    def put(self, filename, data):
        """ Puts given data in the file. Perhaps you have to cd() to directory before doing
        this. It will fail if file already exists. """
        self.send('AT+FSFW=-1,"%s",0,"",%d\r\n' % (filename, len(data)))
        (i, r) = self.recv(['##>\r\n'])
        if i != 0:
            raise IOError, "Can't write file."
        while len(data) > 0:
            # XXX Mobile phone will not treat this as a stream. It will treat it as blocks, as
            # XXX received via USB. Should read or set wMaxPacketSize for USB device.
            # XXX Perhaps 64 is the default (at least for Linux).
            # XXX Any OS-independent way?
            # The CRC32 max length is 11.
            blocksize = 64 - 11
            block = data[:blocksize]
            data  = data[blocksize:]
            # It uses unsigned CRC32 for sending data. Convert it.
            crc = '%u' % struct.unpack('I', struct.pack('i', zlib.crc32(block)))[0]
            # Construct block.
            block = crc + ',' + block
            self.send(block, False)
            # XXX How to check response? It couldn't be received via tty, even it being possible
            # XXX to sniff it via usbmon. Data simply disappears!
            self.recv(['##>\r\n', 'OK\r\n'], 0.1)
            

class SamsungFS(fuse.Fuse, Samsung):
    """ Samsung FUSE File System. Yes, I know, it's a crappy piece of code.
    But Python-FUSE is already very crappy and has very poor documentation. """

    # Put here because it was at documentation (can I call it documentation?)
    flags = 0
    multithreaded = 0
    
    in_use = False         # True if filesystem is in use. Used for locks.
    last_created = None    # Path of the last created file.
    opened_file = None     # Path of the current opened file.
    
    def __lock_fs__(self):
        if self.in_use: return False
        self.in_use = True
        return True

    def __unlock_fs__(self):
        self.in_use = False

    def __init__(self, *args, **kw):
        fuse.Fuse.__init__(self, *args, **kw)
        dev = '/dev/ttyACM0'
        if self.optdict.has_key('dev'): dev = self.optdict['dev']
        Samsung.__init__(self, dev)
    
    def getattr(self, path):
        """
        - st_mode (protection bits)
        - st_ino (inode number)
        - st_dev (device)
        - st_nlink (number of hard links)
        - st_uid (user ID of owner)
        - st_gid (group ID of owner)
        - st_size (size of file, in bytes)
        - st_atime (time of most recent access)
        - st_mtime (time of most recent content modification)
        - st_ctime (platform dependent; time of most recent metadata change on Unix,
                    or the time of creation on Windows).
        """
        if not self.__lock_fs__():
            return -errno.EBUSY
        if path == self.last_created:
            finfo = (0, int(time.time()))
        else:
            try: finfo = self.fileinfo(path)
            except: finfo = None
        self.__unlock_fs__()
        if finfo == None:
            return -errno.ENOENT
        if finfo == (0, 0):
            mode = 0750 | stat.S_IFDIR
        else:
            mode = 0640 | stat.S_IFREG
        return (mode,zlib.crc32(path),0,1,os.getuid(),os.getgid(),finfo[0],finfo[1],finfo[1],finfo[1])

    def getdir(self, path):
        """
        return: [('file1', 0), ('file2', 0), ... ]
        """
        if not self.__lock_fs__():
            return -errno.EBUSY
        flist = [(x[0], 0) for x in self.ls(path)]
        self.__unlock_fs__()
        return flist

    def mythread(self):
        -errno.ENOSYS

    def chmod(self, path, mode):
        -errno.ENOSYS

    def chown(self, path, uid, gid):
        -errno.ENOSYS

    def fsync(self, path, isFsyncFile):
        -errno.ENOSYS

    def link(self, targetPath, linkPath):
        -errno.ENOSYS

    def mkdir(self, path, mode):
        -errno.ENOSYS

    def mknod(self, path, mode, dev):
        if not self.__lock_fs__():
            return -errno.EBUSY
        self.last_created = path
        self.__unlock_fs__()
        return 0

    def open(self, path, flags):
        if self.opened_file != None: # Max 1 file.
            return -errno.ENFILE
        if not self.__lock_fs__():
            return -errno.EBUSY
        self.opened_file = path
        self.opened_file_changed = False
        (dir, filename) = sepdirfname(path)
        try:
            self.cd(dir)
            self.file_buf = self.get(filename)
        except:
            self.file_buf = ''
        self.__unlock_fs__()
        return 0

    def read(self, path, length, offset):
        if path != self.opened_file:
            return -errno.EBADF
        return self.file_buf[offset:offset+length]

    def readlink(self, path):
        -errno.ENOSYS

    def release(self, path, flags):
        if path != self.opened_file:
            return -errno.EBADF
        if not self.__lock_fs__():
            return -errno.EBUSY
        if path == self.last_created:
            self.last_created = None
        if self.opened_file_changed:
            (dir, filename) = sepdirfname(path)
            self.cd(dir)
            try: self.rm(filename)
            except: pass
            self.put(filename, self.file_buf)
            self.opened_file_changed = False
        self.opened_file = None
        del self.file_buf
        self.__unlock_fs__()
        return 0

    def rename(self, oldPath, newPath):
        # XXX Implement rename. Needs protocol work.
        -errno.ENOSYS

    def rmdir(self, path):
        -errno.ENOSYS

    def statfs(self):
        -errno.ENOSYS

    def symlink(self, targetPath, linkPath):
        -errno.ENOSYS

    def truncate(self, path, size):
        -errno.ENOSYS

    def unlink(self, path):
        (dir, filename) = sepdirfname(path)
        if not self.__lock_fs__():
            return -errno.EBUSY
        try:
            self.cd(dir)
            self.rm(filename)
            self.__unlock_fs__()
            return 0
        except:
            self.__unlock_fs__()
            return -errno.ENOENT

    def utime(self, path, times):
        -errno.ENOSYS

    def write(self, path, buf, offset):
        if path != self.opened_file:
            return -errno.EBADF
        self.file_buf = self.file_buf[:offset] + buf
        self.opened_file_changed = True
        return len(buf)



if __name__ == '__main__':
    copyright()
    if len(sys.argv) < 2:
        usage()
    
    if os.fork() == 0:
        fs = SamsungFS()
        print
        print "Mobile phone model: %s" % fs.model
        print "Type 'fusermount -u mount-point' to umount."
        print
        # Detach.
        os.setsid()
        os.close(0)
        os.close(1)
        os.close(2)
        # Main loop.
        fs.main()
