//go:build !windows

package main

import (
	"os"
	"syscall"
)

// tryLock takes an exclusive flock on path; the OS drops it when this process exits.
func tryLock(path string) (func(), bool) {
	f, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		return nil, false
	}
	if syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB) != nil {
		f.Close()
		return nil, false
	}
	return func() { f.Close() }, true
}
