#!/usr/bin/python
#
# Java Uploader for Samsung mobile phones.
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

import sys, os, getopt
import BaseHTTPServer


def copyright():
        print """
Java Uploader for Samsung mobile phones.
Copyright (c) 2007 Paulo Matias

This is a free software licensed under a BSD license AND
HAS NO WARRANTIES. See source code for details.
"""

def usage():
    print """Usage: %s [-l addr] [-p port] files.jad
       -l addr    Address to listen into. Defaults to any.
       -p port    Port to listen into. Defaults to 888.
       files.jad  Java applications you want to upload.
""" % sys.argv[0]
    sys.exit(1)


# Display copyright notice.
copyright()

# Default arguments.
addr = ''
port = 888

# Process options from command line.
try: opts, args = getopt.getopt(sys.argv[1:], "l:p:")
except: usage()
for o, a in opts:
    if o == '-h':
        usage()
    elif o == '-l':
        addr = a
    elif o == '-p':
        port = int(a)
        
# Check if files are provided at command line.
if len(args) == 0:
    usage()
    
# Check if all needed files can be found.
for filename in args:
    f = open(filename)
    for l in f.readlines():
        l = l.strip().split(' ', 1)
        if l[0] == 'MIDlet-Jar-URL:':
            if (l[1].find('://') != -1) or (not os.access(os.path.basename(l[1]), os.R_OK)):
                sys.stderr.write('File "%s" not found in current directory.\n' % l[1])
                sys.exit(1)
    f.close()


# Our request handler.
class http_handler(BaseHTTPServer.BaseHTTPRequestHandler):
    server_version = 'PyJUp/20070801'
    def do_GET(self):
        global args
        basename = self.path[self.path.rindex('/')+1:]
        if basename == 'getNextApp.jad':
            # Read next JAD file.
            f = open(args[0])
            content = f.read()
            f.close()
            # Remove from list.
            args = args[1:]
            # Set content type.
            content_type = 'text/vnd.sun.j2me.app-descriptor'
        else:
            # Read a JAR file.
            f = open(basename)
            content = f.read()
            f.close()
            # Set content type.
            content_type = 'application/java-archive'
        self.send_response(200)
        self.send_header('Connection', 'close')
        self.send_header('Content-type', content_type)
        self.send_header('Content-Length', len(content))
        self.end_headers()
        self.wfile.write(content)
            

# Create HTTP server.
http_server = BaseHTTPServer.HTTPServer((addr, port), http_handler)
http_server.serve_forever()
