package main

// Right-click menu under HKEY_CURRENT_USER, so no admin rights are needed. On Windows 11
// it shows under "Show more options".

import (
	"fmt"
	"io"
	"os"
	"path/filepath"
	"syscall"
	"unsafe"
)

const menuKey = `Software\Classes\AllFilesystemObjects\shell\GDriveCleanup`

var (
	advapi32        = syscall.NewLazyDLL("advapi32.dll")
	regCreateKeyExW = advapi32.NewProc("RegCreateKeyExW")
	regSetValueExW  = advapi32.NewProc("RegSetValueExW")
	regDeleteTreeW  = advapi32.NewProc("RegDeleteTreeW")
)

const errorFileNotFound = 2

func menuInstalled() bool {
	var h syscall.Handle
	key, _ := syscall.UTF16PtrFromString(menuKey)
	if syscall.RegOpenKeyEx(syscall.HKEY_CURRENT_USER, key, 0, syscall.KEY_READ, &h) != nil {
		return false
	}
	syscall.RegCloseKey(h)
	return true
}

// setValues creates key and sets its string values; the name "" is the key's default value.
func setValues(key string, values map[string]string) error {
	var h syscall.Handle
	k, _ := syscall.UTF16PtrFromString(key)
	if r, _, _ := regCreateKeyExW.Call(uintptr(syscall.HKEY_CURRENT_USER), uintptr(unsafe.Pointer(k)),
		0, 0, 0, uintptr(syscall.KEY_WRITE), 0, uintptr(unsafe.Pointer(&h)), 0); r != 0 {
		return fmt.Errorf("registry %s: %w", key, syscall.Errno(r))
	}
	defer syscall.RegCloseKey(h)
	for name, value := range values {
		var n *uint16
		if name != "" {
			n, _ = syscall.UTF16PtrFromString(name)
		}
		data, _ := syscall.UTF16FromString(value)
		if r, _, _ := regSetValueExW.Call(uintptr(h), uintptr(unsafe.Pointer(n)), 0, syscall.REG_SZ,
			uintptr(unsafe.Pointer(&data[0])), uintptr(len(data)*2)); r != 0 {
			return fmt.Errorf("registry %s\\%s: %w", key, name, syscall.Errno(r))
		}
	}
	return nil
}

// installCopy copies this exe to the user's programs folder, so the menu does not depend on
// the drive it was started from and Windows does not warn about a program from the network.
func installCopy() (string, error) {
	self, err := os.Executable()
	if err != nil {
		return "", err
	}
	dst := filepath.Join(os.Getenv("LOCALAPPDATA"), "Programs", "g-drive-cleanup", "g-drive-cleanup.exe")
	if pathEqual(self, dst) {
		return dst, nil
	}
	if err := os.MkdirAll(filepath.Dir(dst), 0o777); err != nil {
		return "", err
	}
	in, err := os.Open(self)
	if err != nil {
		return "", err
	}
	defer in.Close()
	out, err := os.Create(dst)
	if err != nil {
		return "", fmt.Errorf("could not replace %s (is it running?): %w", dst, unwrapPath(err))
	}
	if _, err := io.Copy(out, in); err != nil {
		out.Close()
		return "", err
	}
	return dst, out.Close()
}

func installMenu() error {
	exe, err := installCopy()
	if err != nil {
		return err
	}
	if err := setValues(menuKey, map[string]string{"MUIVerb": appName, "SubCommands": ""}); err != nil {
		return err
	}
	for i, v := range verbs {
		// Sub-menu entries are listed in key name order, hence the number
		key := fmt.Sprintf(`%s\shell\%d%s`, menuKey, i+1, v.name)
		// Player: allow more than 15 selected items; Explorer still starts one process per item
		if err := setValues(key, map[string]string{"MUIVerb": v.label, "MultiSelectModel": "Player"}); err != nil {
			return err
		}
		if err := setValues(key+`\command`, map[string]string{"": fmt.Sprintf(`"%s" %s "%%1"`, exe, v.name)}); err != nil {
			return err
		}
	}
	return nil
}

func uninstallMenu() error {
	k, _ := syscall.UTF16PtrFromString(menuKey)
	r, _, _ := regDeleteTreeW.Call(uintptr(syscall.HKEY_CURRENT_USER), uintptr(unsafe.Pointer(k)))
	if r != 0 && r != errorFileNotFound {
		return fmt.Errorf("registry %s: %w", menuKey, syscall.Errno(r))
	}
	return nil
}
