import os
import time
import logging

from LogWatcher import LogWatcher

logging.basicConfig(level=logging.ERROR)
log = logging.getLogger("main")

formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
log.propagate = False


def callback(filename, lines, filter_file_name=None):
    log.debug(">>> %s" % filename)
    prev_line_number = None

    for line in lines:

        print("%s \t(%s)" % (line.strip(), filename))

        line_number = int(line.strip().split(" ")[-1])
        if prev_line_number is None:
            prev_line_number = line_number
            continue
        lines_diff = line_number - prev_line_number
        if lines_diff> 1:
            print("MISSING LINES: %s" % lines_diff)
        prev_line_number = line_number

    log.debug("<<< %s" % filename)


watcher = LogWatcher(os.path.dirname("./"), callback, ["log"], persistent_checkpoint=False)


while True:
    try:
        log.debug("iterating")
        log.debug(">> log tail watcher loop")
        watcher.loop(blocking=False)
        log.debug("<< log tail watcher loop")
        log.debug("<< log modification timeout check")
        time.sleep(.2)
    except KeyboardInterrupt:
        break
