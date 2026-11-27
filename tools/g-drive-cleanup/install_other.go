//go:build !windows

package main

import "errors"

var errNotWindows = errors.New("the right-click menu is only available on Windows")

func menuInstalled() bool  { return false }
func installMenu() error   { return errNotWindows }
func uninstallMenu() error { return errNotWindows }
