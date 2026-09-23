import subprocess
import sys
import time

import psutil

from aidoc import procs


def test_kill_tree_kills_children():
    code = ("import subprocess,sys,time; c=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
            "print(c.pid, flush=True); time.sleep(60)")
    p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True, **procs.popen_kwargs())
    child_pid = int(p.stdout.readline())
    procs.kill_tree(p.pid)
    p.wait(10)
    for _ in range(50):
        if not psutil.pid_exists(child_pid):
            break
        time.sleep(0.1)
    assert not psutil.pid_exists(child_pid)


def test_is_aidoc_runner_false_for_random_process():
    assert procs.is_aidoc_runner(psutil.Process().pid) is False
