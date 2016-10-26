# -----------------------------------------------------------------------------
"""

linenoise for python

See: http://github.com/antirez/linenoise

"""
# -----------------------------------------------------------------------------

import os
import stat
import sys
import atexit
import termios
import struct
import fcntl
import string

# -----------------------------------------------------------------------------

DEFAULT_HISTORY_MAX_LEN = 100

STDIN_FILENO = sys.stdin.fileno()
STDOUT_FILENO = sys.stdout.fileno()

# indices within the termios settings
C_IFLAG = 0
C_OFLAG = 1
C_CFLAG = 2
C_LFLAG = 3
C_CC = 6

# key codes
KEY_NULL = 0    # NULL
CTRL_A = 1      # Ctrl+a
CTRL_B = 2      # Ctrl-b
CTRL_C = 3      # Ctrl-c
CTRL_D = 4      # Ctrl-d
CTRL_E = 5      # Ctrl-e
CTRL_F = 6      # Ctrl-f
CTRL_H = 8      # Ctrl-h
TAB = 9         # Tab
CTRL_K = 11     # Ctrl+k
CTRL_L = 12     # Ctrl+l
ENTER = 13      # Enter
CTRL_N = 14     # Ctrl-n
CTRL_P = 16     # Ctrl-p
CTRL_T = 20     # Ctrl-t
CTRL_U = 21     # Ctrl+u
CTRL_W = 23     # Ctrl+w
ESC = 27        # Escape
BACKSPACE = 127 # Backspace

# -----------------------------------------------------------------------------

def get_cursor_position(ifd, ofd):
  """Get the horizontal cursor position"""
  # query the cursor location
  if os.write(ofd, '\x1b[6n') != 4:
    return -1
  # read the response: ESC [ rows ; cols R
  # rows/cols are decimal number strings
  buf = []
  while len(buf) < 32:
    buf.append(os.read(ifd, 1))
    if buf[-1] == 'R':
      break
  # parse it
  if buf[0] != chr(ESC) or buf[1] != '[' or buf[-1] != 'R':
    return -1
  buf = buf[2:-1]
  (rows, cols) = ''.join(buf).split(';')
  # return the cols
  return int(cols, 10)

def get_columns(ifd, ofd):
  """Get the number of columns for the terminal. Assume DEFAULT_COLS if it fails."""
  DEFAULT_COLS = 80
  cols = 0
  # try using the ioctl to get the number of cols
  try:
    t = fcntl.ioctl(STDOUT_FILENO, termios.TIOCGWINSZ, struct.pack('HHHH', 0, 0, 0, 0))
    (rows, cols, _, _) = struct.unpack('HHHH', t)
  except:
    pass
  if cols == 0:
    # the ioctl failed - try using the terminal itself
    start = get_cursor_position(ifd, ofd)
    if start < 0:
      return DEFAULT_COLS
    # Go to right margin and get position
    if os.write(ofd, '\x1b[999C') != 6:
      return DEFAULT_COLS
    cols = get_cursor_position(ifd, ofd)
    if cols < 0:
      return DEFAULT_COLS
    # restore the position
    if cols > start:
      os.write(ofd, '\x1b[%dD' % (cols - start))
  return cols

# -----------------------------------------------------------------------------

def unsupported_term():
  """return True if we know we don't support this terminal"""
  unsupported = ('dumb', 'cons25', 'emacs')
  term = os.environ.get('TERM', '')
  return term in unsupported

# -----------------------------------------------------------------------------

class line_state(object):
  def __init__(self, ifd, ofd, prompt):
    self.ifd = ifd                    # Terminal stdin file descriptor
    self.ofd = ofd                    # Terminal stdout file descriptor
    self.buf = []                     # Edited line buffer
    self.prompt = prompt              # Prompt to display
    self.pos = 0                      # Current cursor position
    self.oldpos = 0                   # Previous refresh cursor position
    self.cols = get_columns(ifd, ofd) # Number of columns in terminal
    self.maxrows = 0                  # Maximum num of rows used so far (multiline mode)
    self.history_index = 0            # The history index we are currently editing

# -----------------------------------------------------------------------------

class linenoise(object):

  def __init__(self):
    self.history = [] # list of history strings
    self.history_maxlen = DEFAULT_HISTORY_MAX_LEN # maximum number of history entries
    self.rawmode = False # are we in raw mode?
    self.atexit_registered = False # have we registered a cleanup upon exit function?

  def enable_rawmode(self, fd):
    """Enable raw mode"""
    if not os.isatty(fd):
      return -1
    # cleanup upon disaster
    if not self.atexit_registered:
      atexit.register(self.atexit)
      self.atexit_registered = True
    # modify the original mode
    self.orig_termios = termios.tcgetattr(fd)
    raw = termios.tcgetattr(fd)
    # input modes: no break, no CR to NL, no parity check, no strip char, no start/stop output control.
    raw[C_IFLAG] &= ~(termios.BRKINT | termios.ICRNL | termios.INPCK | termios.ISTRIP | termios.IXON)
    # output modes - disable post processing
    raw[C_OFLAG] &= ~(termios.OPOST)
    # control modes - set 8 bit chars
    raw[C_CFLAG] |= (termios.CS8)
    # local modes - echo off, canonical off, no extended functions, no signal chars (^Z,^C)
    raw[C_LFLAG] &= ~(termios.ECHO | termios.ICANON | termios.IEXTEN | termios.ISIG)
    # control chars - set return condition: min number of bytes and timer.
    # We want read to return every single byte, without timeout.
    raw[C_CC][termios.VMIN] = 1
    raw[C_CC][termios.VTIME] = 0
    # put terminal in raw mode after flushing
    termios.tcsetattr(fd, termios.TCSAFLUSH, raw)
    self.rawmode = True
    return 0

  def disable_rawmode(self, fd):
    """Disable raw mode"""
    if self.rawmode:
      termios.tcsetattr(fd, termios.TCSAFLUSH, self.orig_termios)
      self.rawmode = False

  def atexit(self):
    """Restore STDIN to the orignal mode"""
    sys.stdout.write('\r')
    sys.stdout.flush()
    self.disable_rawmode(STDIN_FILENO)

  def clear_screen(self):
    """Clear the screen"""
    os.write(STDOUT_FILENO, '\x1b[H\x1b[2J')

  def beep(self):
    """Beep"""
    sys.stderr.write('\x07')
    sys.stderr.flush()

  def refresh_show_hints(self):
    pass

  def refresh_single_line(self):
    pass

  def edit_insert(self, l, c):
    """insert a character at the current cursor position"""
    if len(l.buf) == l.pos:
      l.buf.append(c)
      l.pos += 1
      os.write(l.ofd, '%c' % chr(c))

  def edit(self, ifd, ofd, prompt):
    """edit a line in raw mode"""
    l = line_state(ifd, ofd, prompt)
    # The latest history entry is always our current buffer, initially an empty string
    self.history_add('')
    # output the prompt
    if os.write(ofd, prompt) != len(prompt):
      return None
    while True:
      c = ord(os.read(ifd, 1))
      if c == ENTER:
        break
      elif c == CTRL_C:
        return None
      else:
        self.edit_insert(l, c)
    return ''.join([chr(c) for c in l.buf])

  def read_raw(self, prompt):
    """read a line from stdin in raw mode"""
    if self.enable_rawmode(STDIN_FILENO) == -1:
      return None
    s = self.edit(STDIN_FILENO, STDOUT_FILENO, prompt)
    self.disable_rawmode(STDIN_FILENO)
    sys.stdout.write('\r\n')
    return s

  def read_no_tty(self):
    """read a line from a file or pipe"""
    s = sys.stdin.readline().strip('\n')
    # return None on EOF
    return (s, None)[s == '']

  def read_unsupported_term(self, prompt):
    """read a line from an unsupported terminal"""
    try:
      s = raw_input(prompt)
    except EOFError:
      # return None on EOF
      s = None
    return s

  def read(self, prompt):
    """Read a line. Return None on EOF."""
    if not os.isatty(STDIN_FILENO):
      # Not a tty. Read from a file/pipe.
      return self.read_no_tty()
    elif unsupported_term():
      # Not a terminal we know about. So basic line reading.
      return self.read_unsupported_term(prompt)
    else:
      return self.read_raw(prompt)

  def print_keycodes(self):
    """Print scan codes on screen for debugging/development purposes"""
    print("Linenoise key codes debugging mode.")
    print("Press keys to see scan codes. Type 'quit' at any time to exit.")
    if self.enable_rawmode(STDIN_FILENO) != 0:
      return
    quit = [''] * 4
    while True:
      # get a character
      c = os.read(STDIN_FILENO, 1)
      if c == '':
        continue
      # display the character
      if c in string.printable:
        m = {'\r': '\\r', '\n': '\\n', '\t': '\\t'}
        cstr = m.get(c, c)
      else:
        m = {0x1b: 'ESC'}
        cstr = m.get(ord(c), '?')
      sys.stdout.write("'%s' 0x%02x (%d)\r\n" % (cstr, ord(c), ord(c)))
      sys.stdout.flush()
      # check for quit
      quit = quit[1:]
      quit.append(c)
      if ''.join(quit) == 'quit':
        break
    # restore the original mode
    self.disable_rawmode(STDIN_FILENO)

  def history_add(self, line):
    """Add a new entry to the history"""
    if self.history_maxlen == 0:
      return
    # don't add duplicated lines
    for prev_line in self.history:
      if line == prev_line:
        return
    # add the line to the history
    if len(self.history) == self.history_maxlen:
      # remove the first entry
      self.history = self.history[1:]
    self.history.append(line)

  def history_set_maxlen(self, n):
    """Set the maximum length for the history. Truncate the current history if needed."""
    if n < 0:
      return
    self.history_maxlen = n
    current_length = len(self.history)
    if current_length > self.history_maxlen:
      # truncate and retain the latest history
      self.history = self.history[current_length - self.history_maxlen:]

  def history_save(self, fname):
    """Save the history to a file"""
    old_umask = os.umask(stat.S_IXUSR | stat.S_IRWXG | stat.S_IRWXO)
    f = open(fname, 'w')
    os.umask(old_umask)
    os.chmod(fname, stat.S_IRUSR | stat.S_IWUSR)
    f.write('\n'.join(self.history))
    f.close()

  def history_load(self, fname):
    """Load history from a file"""
    self.history = []
    if os.path.isfile(fname):
      f = open(fname, 'r')
      x = f.readlines()
      f.close()
      self.history = [l.strip() for l in x]

# -----------------------------------------------------------------------------



