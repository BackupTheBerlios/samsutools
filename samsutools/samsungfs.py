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

version = 'SamsungFS 2007-08-01-alpha'


import sys, os, tty, re, time, select, struct, zlib
import fuse, errno, stat
from fuse import Fuse

if not hasattr(fuse, '__version__'):
    raise RuntimeError, 'Please update fuse-python.'

fuse.fuse_python_api = (0, 2)


class ModemDevice:
    timeout_resolution = 0.01   # Resolution in seconds of recv() timeout timer.
    __skip__ = 0                # Data to be skipped in next recv() for cutting echoes.
    
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
        timeout was reached, and r is the data read before the expected value. """
        
        max_time = time.time() + timeout  # Timestamp of the timeout time.
        buf = ''
        
        while 1:
            if len(select.select([self.__fd__], [], [], self.timeout_resolution)[0]) == 1:
                # There is new data, read it.
                buf += os.read(self.__fd__, 1024)
                # Skip data if there is data to skip.
                if self.__skip__ != 0:
                    skip = min(self.__skip__, len(buf))
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
    

def dir_and_basename(path):
    """ Separates dir from base filename. Returns a tuple (dir, filename). """
    return (os.path.dirname(path), os.path.basename(path))

def signed2unsigned(n):
    """ Converts a signed int to an unsigned one. """
    return struct.unpack('I', struct.pack('i', n))[0]

def clear_dir_trailing_slash(dir):
    """ Removes trailing slash from dir if it isn't the root. """
    if (len(dir) != 1) and (dir[-1:] == '/'):
        dir = dir[:-1]
    return dir
    
    
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
        """ Inits Samsung mobile phone device and gathers model name. """
        ModemDevice.__init__(self, modem_device)
        
        self.send('ATZ\r\n')
        (i, r) = self.recv(['OK\r\n'])
        
        self.send('AT+CGMM\r\n')
        (i, r) = self.recv(['OK\r\n'])
        if (i != 0) or (r.upper().find('SAMSUNG') == -1):
            raise UnexpectedResponse, r
        self.model = r.strip()
    
    def cd(self, dir = '/'):
        """ Change current dir. """
        dir = clear_dir_trailing_slash(dir)
        self.send('AT+FSCD="' + dir + '"\r\n')
        (i, r) = self.recv(['OK\r\n'])
        if i != 0:
            raise IOError, "Can't change dir."
        self.__curdir__ = dir
    
    def __yield__flist__(self, expr):
        """ Yields tuples gathered from expr regular expression for each line of the
        file list sent by mobile phone. """
        flist = []
        while 1:
            # #OK# -> there is more data. OK -> end of data.
            (i, r) = self.recv(['#OK#\r\n', 'OK\r\n'], 0.5)
            for l in r.strip().split('\n'):
                try: yield re.search(expr, l.strip()).groups()
                except: pass
            if i != 0:
                break
            self.send('##>\r\n', False)   # Request more data.

    def ls(self, dir = None):
        """ List files at dir. Yields (name, size, timestamp) tuples.
        Directories will have (size, timestamp) == (0, 0). """
        if dir == None: dir = self.__curdir__
        dir = clear_dir_trailing_slash(dir)
        # Perhaps directories are listed by AT+FSDI command.
        self.send('AT+FSDI="' + dir + '"\r\n')
        for tup in self.__yield__flist__(r'^\+FSDI[^,]+,[^"]*"([^"]+)"'):
            yield tup + (0, 0)
        # Perhaps files are listed by AT+FSDL command.
        self.send('AT+FSDL="' + dir + '"\r\n')
        re_flist = r'^\+FSDL[^,]+,[^"]*"([^"]+)"[^,]*,[^,]+,[^,]+,[^,]+,[^,]+,(\d+)[^,]*,[^,]+,[^"]*"([^"]+)"'
        for (fname, fsize, fdate) in self.__yield__flist__(re_flist):
            # Convert date & time to timestamp.
            try:
                (d,m,y,th,tm,ampm) = re.search(r'(\d+)/(\d+)/(\d+)\s+(\d+):(\d+)([AP])M', fdate).groups()
                if ampm == 'P': th = int(th) + 12
                fdate = int(time.mktime((int(y),int(m),int(d),int(th),int(tm),0,0,0,0)))
            except:
                fdate = 0
            yield (fname, int(fsize), fdate)

    def fileinfo(self, path):
        """ Returns a tuple (size, timestamp) for file at path. Directories will
        have (size, timestamp) == (0, 0). """
        path = clear_dir_trailing_slash(path)
        if path == '/': return (0, 0)   # Root dir.
        # Separate parent directory and filename.
        (dir, filename) = dir_and_basename(path)
        # Check cache. Fill cache if empty or timeouted.
        if (dir in self.__fileinfo_cache__) and (time.time() - self.__fileinfo_cache__[dir][0] < self.__fileinfo_cache_timeout__):
            flist = self.__fileinfo_cache__[dir][1]
        else:
            flist = {}
            for (fname, fsize, fdate) in self.ls(dir):
                flist[fname] = (fsize, fdate)
            self.__fileinfo_cache__[dir] = (time.time(), flist)
        # If not in cache, file does not exist.
        if not filename in flist:
            raise IOError, 'File not found.'
        # Return file info.
        return flist[filename]
        
    def rm(self, filename):
        """ Removes the file. File must be in the current directory. """
        self.send('AT+FSFE=0,"' + filename + '"\r\n')
        (i, r) = self.recv(['OK\r\n'])
        if i != 0:
            raise IOError, "Can't remove file."

    def get(self, filename):
        """ Gets the file and returns its contents.
        File must be in the current directory. """
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
        """ Puts given data in the file. File must be in the current directory.
        It will fail if file already exists. """
        self.send('AT+FSFW=-1,"%s",0,"",%d\r\n' % (filename, len(data)))
        (i, r) = self.recv(['##>\r\n'])
        if i != 0:
            raise IOError, "Can't write file."
        while len(data) > 0:
            # XXX Mobile phone will not treat this as a stream. It will treat it as blocks, as
            # XXX received via USB. Should read or set max packet size for USB device.
            # Perhaps 64 is the default max packet size for high speed USB devices.
            # The CRC32 max length is 11.
            blocksize = 64 - 11
            block = data[:blocksize]
            data  = data[blocksize:]
            # It uses unsigned CRC32 for sending data. Convert it.
            crc = '%u' % signed2unsigned(zlib.crc32(block))
            # Construct block.
            block = crc + ',' + block
            self.send(block, False)
            # XXX How can we check response? It couldn't be received via tty, even it being
            # XXX sniffable via usbmon. Data simply disappears at higher levels! O.o
            self.recv(['##>\r\n', 'OK\r\n'], 0.01)
      

# XXX Sadly, Python2.4 does not support "with" statements.
# XXX Port all ObjLock use when Python2.5 becomes widely adopted.
class ObjLock:
    """ Issues a object-wide lock. """
    def __init__(self, obj):
        """ Locks obj. Raises IOError EBUSY if already locked. """
        if hasattr(obj, '__is_locked__') and obj.__is_locked__:
            err = IOError()
            err.errno = errno.EBUSY
            raise err
        obj.__is_locked__ = True
        self.obj = obj
    def __del__(self):
        """ Unlocks obj. """
        try: self.obj.__is_locked__ = False
        except: pass


class SamsungFS_stat(fuse.Stat):
    def __init__(self, finfo):
        """ Constructs a FUSE stat from a fileinfo() tuple. """
        if finfo == (0, 0):
            self.st_mode = 0755 | stat.S_IFDIR
        else:
            self.st_mode = 0644 | stat.S_IFREG
        self.st_ino = 0
        self.st_dev = 0
        self.st_nlink = 1
        self.st_uid = os.getuid()
        self.st_gid = os.getgid()
        self.st_size = finfo[0]
        self.st_atime = finfo[1]
        self.st_mtime = finfo[1]
        self.st_ctime = finfo[1]


class SamsungFS(Fuse, Samsung):
    """ Samsung FUSE File System. """
    
    last_created = None    # Path of the last created file.
    opened_file = None     # Path of the current opened file.
    
    def __init__(self, *args, **kw):
        fuse.Fuse.__init__(self, *args, **kw)
    
    def getattr(self, path):
        lock = ObjLock(self)
        
        if path == self.last_created:
            finfo = (0, int(time.time()))
        else:
            try: finfo = self.fileinfo(path)
            except: finfo = None
            
        del lock
        
        if finfo == None:
            return -errno.ENOENT
        
        return SamsungFS_stat(finfo)

    def readdir(self, path, offset):
        lock = ObjLock(self)
        for (fname, fsize, fdate) in self.ls(path):
            yield fuse.Direntry(fname)
        del lock

    def mknod(self, path, mode, dev):
        lock = ObjLock(self)
        self.last_created = path
        del lock

    def open(self, path, flags):
        if self.opened_file != None:
            # Max 1 file.
            return -errno.ENFILE
        
        lock = ObjLock(self)
        
        self.opened_file = path
        self.opened_file_changed = False
        
        (dir, filename) = dir_and_basename(path)
        try:
            self.cd(dir)
            self.file_buf = self.get(filename)
        except:
            self.file_buf = ''
        
        del lock

    def read(self, path, length, offset):
        if self.opened_file == None:
            # Fix for some buggy implementations.
            self.open(path, os.O_RDWR)
        if path != self.opened_file:
            return -errno.EBADF
        return self.file_buf[offset:offset+length]

    def release(self, path, flags):
        if path != self.opened_file:
            return -errno.EBADF
        
        raise_err = False
        
        lock = ObjLock(self)
        
        if path == self.last_created:
            self.last_created = None
        
        if self.opened_file_changed:
            try:
                (dir, filename) = dir_and_basename(path)
                self.cd(dir)
                try: self.rm(filename)
                except: pass
                self.put(filename, self.file_buf)
            except:
                raise_err = True
            self.opened_file_changed = False
        
        self.opened_file = None

        del self.file_buf
        del lock
        
        if raise_err:
            return -errno.EIO

    def unlink(self, path):
        lock = ObjLock(self)
        try:
            (dir, filename) = dir_and_basename(path)
            self.cd(dir)
            self.rm(filename)
        except:
            del lock
            return -errno.ENOENT
        del lock

    def write(self, path, buf, offset):
        if self.opened_file == None:
            # Fix for some buggy implementations.
            self.open(path, os.O_RDWR)
        if path != self.opened_file:
            return -errno.EBADF
        self.file_buf = self.file_buf[:offset] + buf
        self.opened_file_changed = True
        return len(buf)
    
    def main(self, *a, **kw):
        if self.fuse_args.mount_expected():
            Samsung.__init__(self, self.ttydev)
        return Fuse.main(self, *a, **kw)


def main():
    
    usage = """
File System for Samsung mobile phones.
Copyright (c) 2007 Paulo Matias

This is a free software licensed under a BSD license AND
HAS NO WARRANTIES. See source code for details.

This is a STRONGLY EXPERIMENTAL SOFTWARE. Use it only if you
can't setup your mobile phone for usb-storage or bluetooth.

""" + Fuse.fusage
    
    def_ttydev = '/dev/cuaU0'
    if not os.path.exists(def_ttydev):
        def_ttydev = '/dev/ttyACM0'
    
    fs = SamsungFS(version=version, usage=usage, dash_s_do='setsingle')
    fs.ttydev = def_ttydev
    fs.parser.add_option(mountopt='ttydev', metavar='DEV', default=def_ttydev, 
                     help='tty device the mobile phone is plugged into [default: %default]')
    fs.parse(values=fs, errex=1)
    fs.main()


if __name__ == '__main__':
    main()
    