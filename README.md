# pyLogTailWatcher
Extended version from https://code.activestate.com/recipes/577968-log-watcher-tail-f-log/ to support

1. trailing only new lines

2. supporting files rotation (to prevent missing tail of rotated file)

### Why another tail in python?
Well, none of the promising modules I tried (pygtail, tailchaser) worked on Windows 10 with python 2.7. Mainly for these resons: 

1. Files were identified by st_ino from os.stat which does not work reliably on Windows ([pygtail](https://github.com/bgreenlee/pygtail/blob/bd93577fc760d22ad7207621ce8dd9f7f76a6a67/pygtail/core.py#L190))
2. Even if files signature was used instead of st_ino I faced problems with reliable handling lines from files which were just rolled
and although tailing problem is not a rocket science implementation based on generators/yielding did not help understanding
what was going on in the code in reasonable time like 15 min.
3. Even if checkpoints were used to save the last offset of the tailed file, every module I have seen used file-based 
storage for the checkpoint data which may be what you want if the tail is supposed to run as periodically executed command
but it was not my intended purpose - to stream new lines to whatever python-compatible endpoint (rest, redis, database). 
When tailing module is a part of a log watching process a persistent storage is not necessary required.  

### Example
In terminal 1 run `python run_test_rotate.py`

In terminal 2 run `python run_log_watcher.py`

expected result:

```bash
...
This is test log line 42        (M:\02_documents\_repos\eds_repo\eds\pyLogTailWatcher\test.log)
This is test log line 43        (M:\02_documents\_repos\eds_repo\eds\pyLogTailWatcher\test.log.1)
This is test log line 44        (M:\02_documents\_repos\eds_repo\eds\pyLogTailWatcher\test.log.1)
This is test log line 45        (M:\02_documents\_repos\eds_repo\eds\pyLogTailWatcher\test.log)
This is test log line 46        (M:\02_documents\_repos\eds_repo\eds\pyLogTailWatcher\test.log)
This is test log line 47        (M:\02_documents\_repos\eds_repo\eds\pyLogTailWatcher\test.log)
This is test log line 48        (M:\02_documents\_repos\eds_repo\eds\pyLogTailWatcher\test.log)
...
```