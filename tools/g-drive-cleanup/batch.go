package main

// Explorer starts one process per selected item. Each process drops its paths in a spool
// folder; the one that gets the lock waits until no more arrive and then handles them all,
// so the user is asked once. The others exit right away.

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

const (
	spoolStep  = 100 * time.Millisecond
	spoolQuiet = 600 * time.Millisecond // no new items for this long: all processes have reported
	spoolLimit = 10 * time.Second
)

// collectBatch returns all paths to handle in this process, or none if another process handles them.
func collectBatch(verb string, paths []string) ([]string, error) {
	dir := filepath.Join(os.TempDir(), "g-drive-cleanup", verb)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return nil, err
	}
	if err := spool(dir, paths); err != nil {
		return nil, err
	}
	var batch []string
	for {
		unlock, ok := tryLock(dir + ".lock")
		if !ok {
			break // another process has the lock and will take what is in the spool
		}
		waitQuiet(dir)
		items, err := takeAll(dir)
		unlock()
		if err != nil {
			return nil, err
		}
		batch = append(batch, items...)
		// A process that wrote after takeAll but failed to get the lock has exited; pick up its items
		if pending(dir) == 0 {
			break
		}
	}
	return dedupe(batch), nil
}

func spool(dir string, paths []string) error {
	name := filepath.Join(dir, fmt.Sprintf("%d-%d", os.Getpid(), time.Now().UnixNano()))
	// Write then rename, so a half-written file is never read
	if err := os.WriteFile(name+".tmp", []byte(strings.Join(paths, "\n")), 0o600); err != nil {
		return err
	}
	return os.Rename(name+".tmp", name+".txt")
}

func pending(dir string) int {
	entries, _ := os.ReadDir(dir)
	return len(entries)
}

func waitQuiet(dir string) {
	last, still := -1, time.Duration(0)
	for waited := time.Duration(0); waited < spoolLimit && still < spoolQuiet; waited += spoolStep {
		time.Sleep(spoolStep)
		if n := pending(dir); n == last {
			still += spoolStep
		} else {
			last, still = n, 0
		}
	}
}

func takeAll(dir string) ([]string, error) {
	files, err := filepath.Glob(filepath.Join(dir, "*.txt"))
	if err != nil {
		return nil, err
	}
	var paths []string
	for _, f := range files {
		data, err := os.ReadFile(f)
		if err != nil {
			return nil, err
		}
		os.Remove(f)
		for _, p := range strings.Split(string(data), "\n") {
			if p != "" {
				paths = append(paths, p)
			}
		}
	}
	return paths, nil
}

func dedupe(paths []string) []string {
	sort.Strings(paths)
	out := paths[:0]
	for i, p := range paths {
		if i == 0 || !pathEqual(p, paths[i-1]) {
			out = append(out, p)
		}
	}
	return out
}
