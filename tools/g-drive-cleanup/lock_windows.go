package main

import "syscall"

// tryLock opens path with no sharing, so a second open fails until this process
// unlocks or exits (also when it crashes).
func tryLock(path string) (func(), bool) {
	p, err := syscall.UTF16PtrFromString(path)
	if err != nil {
		return nil, false
	}
	h, err := syscall.CreateFile(p, syscall.GENERIC_READ|syscall.GENERIC_WRITE, 0, nil,
		syscall.OPEN_ALWAYS, syscall.FILE_ATTRIBUTE_NORMAL, 0)
	if err != nil {
		return nil, false
	}
	return func() { syscall.CloseHandle(h) }, true
}
