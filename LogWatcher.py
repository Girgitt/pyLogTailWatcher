#!/usr/bin/env python

"""
Real-time log files watcher supporting log rotation.
Works with Python >= 2.6 and >= 3.2, on both POSIX and Windows.

Author: Giampaolo Rodola' <g.rodola [AT] gmail [DOT] com>
License: MIT
"""

import os
import time
import errno
import stat
import sys
import copy
import regex
import pickle
import logging
import binascii

SIG_SZ = 64

log = logging.getLogger("watcher")


class LogWatcher(object):
    """Looks for changes in all files of a directory.
    This is useful for watching log file changes in real-time.
    It also supports files rotation.

    Example:

    >>> def callback(filename, lines):
    ...     print(filename, lines)
    ...
    >>> lw = LogWatcher("/var/log/", callback)
    >>> lw.loop()
    """

    def __init__(self, folder, callback, extensions=["log"], tail_lines=0,
                       sizehint=1048576, persistent_checkpoint=False):
        """Arguments:

        (str) @folder:
            the folder to watch

        (callable) @callback:
            a function which is called every time one of the file being
            watched is updated;
            this is called with "filename" and "lines" arguments.

        (list) @extensions:
            only watch files with these extensions

        (int) @tail_lines:
            read last N lines from files being watched before starting

        (int) @sizehint: passed to file.readlines(), represents an
            approximation of the maximum number of bytes to read from
            a file on every ieration (as opposed to load the entire
            file in memory until EOF is reached). Defaults to 1MB.
        """
        self.folder = os.path.realpath(folder)
        self.extensions = extensions
        self._watched_files_map = {}
        self._checkpoint_files_map = {}
        self._callback = callback
        self._sizehint = sizehint
        self._init_checkpoint = ('', 0, 0)  # signature, file_modification_time, last_read_offset
        self._persistent_checkpoint = persistent_checkpoint
        self._volatile_checkpoints = dict()
        assert os.path.isdir(self.folder), self.folder
        assert callable(callback), repr(callback)
        log.info("Started")
        log.info("folder: %s" % self.folder)
        log.info("extensions: %s" % self.extensions)

        self.update_files()
        self.update_checkpoints_to_point_to_end_of_files()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def __del__(self):
        self.close()

    def update_checkpoints_to_point_to_end_of_files(self):
        for id, file in self._watched_files_map.items():
            log.debug("updated checkpoint to point to end of file %s" % file.name)
            with self.open(file.name) as f:
                f.seek(os.path.getsize(file.name))  # EOF
                end_offset = f.tell()
                self.save_checkpoint(file.name, self.get_checkpoint_tuple(file.name, 0, end_offset))

    def get_checkpoint_tuple(self, fname, mtime=0, offset=0):
        signature = self.make_sig(fname)
        out_tuple = (signature, mtime, offset)
        return out_tuple

    @staticmethod
    def slugify(value):
        """
        Parameters
        ----------
        value: str
            the value to slug
        Convert spaces to hyphens.
        Remove characters that aren't alphanumerics, underscores, or hyphens.
        Convert to lowercase. Also strip leading and trailing whitespace.
        """
        value = regex.sub('[^\w\s-]', '', value).strip().lower()
        return regex.sub('[-\s]+', '-', value)

    def load_checkpoint(self, fname):
        checkpoint_filename = self.make_checkpoint_path_from_fname(fname)

        if self._persistent_checkpoint:
            try:
                sig, mtime, offset = pickle.load(open(checkpoint_filename, 'rb'))
                log.debug('loaded: %s %s', checkpoint_filename, (sig, mtime, offset))
                return sig, mtime, offset
            except (IOError, EOFError):
                log.info('failed to load: %s; returning default checkpoint: %s' % (fname, self.get_checkpoint_tuple(fname)))
                #return self._init_checkpoint
                return self.get_checkpoint_tuple(fname)

        try:
            return pickle.loads(self._volatile_checkpoints[checkpoint_filename])
        except KeyError:
            log.info("returning default checkpoint value")
            return self._init_checkpoint

    def save_checkpoint(self, fname, checkpoint):
        checkpoint_filename = self.make_checkpoint_path_from_fname(fname)
        log.debug('dumping %s %s', checkpoint_filename, checkpoint)
        if self._persistent_checkpoint:
            return pickle.dump(checkpoint, open(checkpoint_filename, 'wb'))

        self._volatile_checkpoints[checkpoint_filename] = pickle.dumps(checkpoint)
        return True

    def delete_checkpoint_file(self, fname):
        checkpoint_filename = self.make_checkpoint_path_from_fname(fname)
        if self._persistent_checkpoint:
            try:
                os.unlink(checkpoint_filename)
                return
            except:
                log.info("could not delete checkpoint file for: %s" % fname)
        try:
            del self._volatile_checkpoints[checkpoint_filename]
        except KeyError:
            log.warning("could not delete volatile checkpoint definition for key: %s" % checkpoint_filename)

    def make_checkpoint_path_from_fname(self, fname):
        return self.slugify(fname)+".checkpoint"

    def loop(self, interval=0.1, blocking=True):
        """Start a busy loop checking for file changes every *interval*
        seconds. If *blocking* is False make one loop then return.
        """
        # May be overridden in order to use pyinotify lib and block
        # until the directory being watched is updated.
        # Note that directly calling readlines() as we do is faster
        # than first checking file's last modification times.
        while True:
            self.update_files()
            for fid, file in list(self._watched_files_map.items()):
                log.debug("reading lines from %s" % file.name)
                self.readlines_from_checkpoint(file)
                #self.readlines(file)
            if not blocking:
                return
            time.sleep(interval)

    def log(self, line):
        """Log when a file is un/watched"""
        print(line)

    def listdir(self):
        """List directory and filter files by extension.
        You may want to override this to add extra logic or globbing
        support.
        """
        ls = os.listdir(self.folder)
        #log.debug(ls)
        #log.debug([os.path.splitext(x)[1][1:] for x in ls])
        if self.extensions:
            return [x for x in ls if os.path.splitext(x)[1][1:] in self.extensions]
        else:
            return ls

    @classmethod
    def open(cls, file):
        """Wrapper around open().
        By default files are opened in binary mode and readlines()
        will return bytes on both Python 2 and 3.
        This means callback() will deal with a list of bytes.
        Can be overridden in order to deal with unicode strings
        instead, like this:

          import codecs, locale
          return codecs.open(file, 'r', encoding=locale.getpreferredencoding(),
                             errors='ignore')
        """
        return open(file, 'rb+')

    @classmethod
    def tail(cls, fname, window):
        """Read last N lines from file fname."""
        if window <= 0:
            raise ValueError('invalid window value %r' % window)
        with cls.open(fname) as f:
            f.seek(os.path.getsize(f.name))  # EOF
            BUFSIZ = 1024
            # True if open() was overridden and file was opened in text
            # mode. In that case readlines() will return unicode strings
            # instead of bytes.
            encoded = getattr(f, 'encoding', False)
            CR = '\n' if encoded else b'\n'
            data = '' if encoded else b''
            f.seek(0, os.SEEK_END)
            fsize = f.tell()
            block = -1
            exit = False
            while not exit:
                step = (block * BUFSIZ)
                if abs(step) >= fsize:
                    f.seek(0)
                    newdata = f.read(BUFSIZ - (abs(step) - fsize))
                    exit = True
                else:
                    f.seek(step, os.SEEK_END)
                    newdata = f.read(BUFSIZ)
                data = newdata + data
                if data.count(CR) >= window:
                    break
                else:
                    block -= 1
            return data.splitlines()[-window:]

    def update_files(self):
        ls = []
        for name in self.listdir():
            absname = os.path.realpath(os.path.join(self.folder, name))
            log.debug("found matching file: %s" % absname)
            try:
                st = os.stat(absname)
            except EnvironmentError as err:
                if err.errno != errno.ENOENT:
                    raise
            else:  # no error occurred
                if not stat.S_ISREG(st.st_mode):
                    continue
                for tries_count in range(3):
                    try:
                        fid = self.get_file_id(absname)
                        log.debug("got possibly new file: %s" % absname)
                        ls.append((fid, absname))
                        break
                    except IOError:
                        log.warning("had to repeat getting file ID while adding to list of files to watch")
                        time.sleep(0.1)


        # check existent files
        for fid, file in list(self._watched_files_map.items()):
            try:
                st = os.stat(file.name)
            except EnvironmentError as err:
                if err.errno == errno.ENOENT:
                    log.debug("unwatching file: %s" % file)
                    self.unwatch(file, fid)
                else:
                    raise
            else:  # no error occurred
                if fid != self.get_file_id(file.name):
                    # same name but different file (rotation); reload it.
                    log.info("reloading file due to rotation: %s" % file.name)
                    self.unwatch(file, fid)
                    self.watch(file.name)

        # add new ones
        for fid, fname in ls:
            log.debug("checking if file is not added to _files_map: %s, fid: %s" % (fname, fid))
            log.debug("already added files: %s" % str(self._watched_files_map.keys()))
            if fid not in self._watched_files_map:
                log.debug("adding new file to watch: %s" % fname)
                self.watch(fname)

        log.debug("self._files_map after update: %s" % str(self._watched_files_map))

    def readlines_from_checkpoint(self, file, checkpoint=None):
        """Read file lines since last access until EOF is reached and
        invoke callback.
        """
        try:
            log.debug(">> loading checkpoint")
            if checkpoint is None:
                sig, mtime, offset = self.load_checkpoint(file.name)
            else:
                log.debug(" > used provided kwarg value")
                sig = list(checkpoint)[0]
                mtime = list(checkpoint)[1]
                offset = list(checkpoint)[2]

            log.debug("<< loading checkpoint")

            out_offset = copy.copy(offset)
            log.debug("starting offset: %s" % offset)
            try:
                with self.open(file.name) as f:

                    f.seek(os.path.getsize(file.name))
                    file_size = f.tell()
                    if offset > file_size:
                        log.error("file smaller than last offset; POSSIBLE DATA LOSS - ARE LOGS POLLED FREQUENTLY ENOUGH?")
                        log.debug("offset: %s" % offset)
                        log.debug("tell: %s" % file_size)
                        offset = 0
                        out_offset = 0

                    log.debug("seek to offset: %s" % offset)
                    f.seek(offset)
                    buff = []
                    while True:
                        line = f.readline()
                        if not line:
                            break
                        buff.append(line.decode('utf-8'))
                        out_offset += len(line)

                    if out_offset != offset:

                        if buff[-1].strip() == "":
                            del(buff[-1])
                            out_offset -= 1
                        self.save_checkpoint(file.name, self.get_checkpoint_tuple(file.name, offset=out_offset))
                        self._callback(f.name, buff)
            except IOError:
                log.exception("failed to read input file: %s" % file.name)
            else:  # no error in reading tail
                return len(buff)

        except ValueError:
            log.exception("could not read lines from file: %s" % file.name)
            return 0

    def readlines(self, file):
        """Read file lines since last access until EOF is reached and
        invoke callback.
        """
        try:
            with self.open(file.name) as f:
                while True:
                    lines = f.readlines(self._sizehint)
                    if not lines:
                        break
                    self._callback(f.name, lines)
        except ValueError:
            log.exception("could not read lines from file: %s" % file.name)
            return

    def watch(self, fname):
        log.info(">> watch: %s" % fname)
        try:
            file = self.open(fname)
            fid = self.get_file_id(fname)
        except EnvironmentError as err:
            if err.errno != errno.ENOENT:
                raise
        else:
            log.info("watching logfile %s" % fname)
            self._watched_files_map[fid] = file
            self._watched_files_map[fid].close()

    def unwatch(self, file, fid):
        # File no longer exists. If it has been renamed try to read it
        # for the last time in case we're dealing with a rotating log
        # file.
        log.info("un-watching logfile %s" % file.name)

        # last reading attempt may block log file from appending by the logger itself
        '''
        try:
            with file:
                lines = self.readlines_from_checkpoint(file)
                if lines:
                    self._callback(file.name, lines)
        except ValueError:
            pass
            #log.exception("could not make a final readlines on a possibly closed file")
        '''

        try:
            sig, mtime, offset = self.load_checkpoint(file.name)
            log.info(">> reading rotated file's tail")
            rolled_file_name = file.name+".1"
            rolled_file = self.open(rolled_file_name)
            backfilled_lines_count = 0
            #print("-->")
            if sig == self.make_sig(rolled_file_name):
                backfilled_lines_count = self.readlines_from_checkpoint(rolled_file, checkpoint=(sig, mtime, offset))
            else:
                log.warning("rolled file signature doesn't match")
                del self._watched_files_map[fid]
                return  #FIXME: checkpoint structure should keep the buffer length used to create file signature and readlines should skip non-matching checkpoint files
            #print("<--")
            if os.path.isfile(rolled_file_name):
                self.delete_checkpoint_file(rolled_file_name)
            log.info("<< reading rotated file's tail")
        except IOError:
            pass

        del self._watched_files_map[fid]
        self.delete_checkpoint_file(file.name)

    def get_file_id(self, fname):
        st = os.stat(fname)
        if os.name == 'posix':
            return "%xg%x" % (st.st_dev, st.st_ino)
        else:
            return "%f_%s" % (st.st_ctime, self.make_sig(fname))

    @classmethod
    def make_sig(cls, file_to_check, stat=None):
        with cls.open(file_to_check) as fh:
            return cls.sig(fh)
            # return hashlib.sha224(open(file_to_check).read(SIG_SZ)).hexdigest()

    @classmethod
    def sig(cls, file_to_check_fh, stat=None):
        return str(binascii.crc32(file_to_check_fh.read(SIG_SZ)) & 0xffffffff)

    def close(self):
        for id, file in self._watched_files_map.items():
            file.close()
        self._watched_files_map.clear()


# ===================================================================
# --- tests
# ===================================================================

if __name__ == '__main__':
    import unittest
    import atexit
    import platform

    TESTFN = '$testfile.log'
    TESTFN2 = '$testfile2.log'
    PY3 = sys.version_info[0] == 3

    if PY3:
        def b(s):
            return s.encode("latin-1")
    else:
        def b(s):
            return s

    class TestLogWatcher(unittest.TestCase):

        def setUp(self):
            def callback(filename, lines):
                self.filename.append(filename)
                for line in lines:
                    self.lines.append(line)

            self.filename = []
            self.lines = []
            self.file = open(TESTFN, 'w')
            self.watcher = LogWatcher(os.getcwd(), callback)

        def tearDown(self):
            self.watcher.close()
            self.remove_test_files()

        def write_file(self, data):
            self.file.write(data)
            self.file.flush()

        @staticmethod
        @atexit.register
        def remove_test_files():
            for x in [TESTFN, TESTFN2]:
                try:
                    os.remove(x)
                except EnvironmentError:
                    pass

        def test_no_lines(self):
            self.watcher.loop(blocking=False)

        def test_one_line(self):
            self.write_file('foo')
            self.watcher.loop(blocking=False)
            self.assertEqual(self.lines, [b"foo"])
            self.assertEqual(self.filename, [os.path.abspath(TESTFN)])

        def test_two_lines(self):
            self.write_file('foo\n')
            self.write_file('bar\n')
            self.watcher.loop(blocking=False)
            if platform.system().upper() == 'WINDOWS':
                self.assertEqual(self.lines, [b"foo\r\n", b"bar\r\n"])
            else:
                self.assertEqual(self.lines, [b"foo\n", b"bar\n"])
            self.assertEqual(self.filename, [os.path.abspath(TESTFN)])

        def test_new_file(self):
            with open(TESTFN2, "w") as f:
                f.write("foo")
            self.watcher.loop(blocking=False)
            self.assertEqual(self.lines, [b"foo"])
            self.assertEqual(self.filename, [os.path.abspath(TESTFN2)])

        def test_file_removed(self):
            self.write_file("foo")
            try:
                os.remove(TESTFN)
            except EnvironmentError:  # necessary on Windows
                pass
            self.watcher.loop(blocking=False)
            self.assertEqual(self.lines, [b"foo"])

        def test_tail(self):
            MAX = 10000
            content = '\n'.join([str(x) for x in range(0, MAX)])
            self.write_file(content)
            # input < BUFSIZ (1 iteration)
            lines = self.watcher.tail(self.file.name, 100)
            self.assertEqual(len(lines), 100)
            self.assertEqual(lines, [b(str(x)) for x in range(MAX-100, MAX)])
            # input > BUFSIZ (multiple iterations)
            lines = self.watcher.tail(self.file.name, 5000)
            self.assertEqual(len(lines), 5000)
            self.assertEqual(lines, [b(str(x)) for x in range(MAX-5000, MAX)])
            # input > file's total lines
            lines = self.watcher.tail(self.file.name, MAX + 9999)
            self.assertEqual(len(lines), MAX)
            self.assertEqual(lines, [b(str(x)) for x in range(0, MAX)])
            #
            self.assertRaises(ValueError, self.watcher.tail, self.file.name, 0)
            LogWatcher.tail(self.file.name, 10)

        def test_ctx_manager(self):
            with self.watcher:
                pass


    test_suite = unittest.TestSuite()
    test_suite.addTest(unittest.makeSuite(TestLogWatcher))
    unittest.TextTestRunner(verbosity=2).run(test_suite)
