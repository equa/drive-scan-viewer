package main

import (
	"syscall"
	"unsafe"
)

var messageBoxW = syscall.NewLazyDLL("user32.dll").NewProc("MessageBoxW")

const (
	mbYesNo         = 0x4
	mbIconError     = 0x10
	mbIconQuestion  = 0x20
	mbIconInfo      = 0x40
	mbSetForeground = 0x10000
	mbTopmost       = 0x40000 // started from Explorer without a window, so make sure it is seen
	idYes           = 6
)

func messageBox(text string, flags uintptr) int {
	t, _ := syscall.UTF16PtrFromString(text)
	c, _ := syscall.UTF16PtrFromString(appName)
	r, _, _ := messageBoxW.Call(0, uintptr(unsafe.Pointer(t)), uintptr(unsafe.Pointer(c)),
		flags|mbSetForeground|mbTopmost)
	return int(r)
}

func ask(text string) bool  { return messageBox(text, mbYesNo|mbIconQuestion) == idYes }
func showError(text string) { messageBox(text, mbIconError) }
func showInfo(text string)  { messageBox(text, mbIconInfo) }
