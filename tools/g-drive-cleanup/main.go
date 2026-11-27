// g-drive-cleanup moves files and folders into the drive's archive or trash, keeping their
// path, from the Explorer right-click menu. Same rules as g-drive-cleanup.py.
//
// The drive top is the nearest folder upwards that holds both 0_ARCHIVE___ and 0_TRASH___.
// Archiving <top>\Users\x\Projects\A\cfd moves it to <top>\0_ARCHIVE___\Users\x\Projects\A\cfd.
//
// Usage:
//
//	g-drive-cleanup.exe                       ask to add or remove the right-click menu
//	g-drive-cleanup.exe install | uninstall   the same without asking
//	g-drive-cleanup.exe [-y] archive|trash|restore PATH...
//
// -y skips the question before moving.
package main

import (
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

const appName = "G-drive cleanup"

var verbs = []struct{ name, label string }{
	{"archive", "Move to archive"},
	{"trash", "Move to trash"},
	{"restore", "Restore to original place"},
}

const usage = `Usage:
  g-drive-cleanup.exe                       add or remove the right-click menu
  g-drive-cleanup.exe install | uninstall
  g-drive-cleanup.exe [-y] archive|trash|restore PATH...`

const maxListed = 12

func main() {
	os.Exit(run())
}

func run() int {
	yes := flag.Bool("y", false, "do not ask before moving")
	flag.Usage = func() { showInfo(usage) }
	flag.Parse()
	args := flag.Args()

	if len(args) == 0 {
		return setup()
	}
	verb, paths := args[0], args[1:]
	switch verb {
	case "install":
		return report(installMenu(), installedText)
	case "uninstall":
		return report(uninstallMenu(), "The right-click menu has been removed.")
	case "archive", "trash", "restore":
	default:
		showError("unknown command " + verb + "\n\n" + usage)
		return 2
	}
	if len(paths) == 0 {
		showError("no files or folders given\n\n" + usage)
		return 2
	}

	for i, p := range paths {
		abs, err := filepath.Abs(p)
		if err != nil {
			showError(err.Error())
			return 1
		}
		paths[i] = abs
	}
	// Explorer may start us inside a folder we are about to move, which would block the move
	os.Chdir(os.TempDir())

	batch, err := collectBatch(verb, paths)
	if err != nil {
		showError(err.Error())
		return 1
	}
	if len(batch) == 0 {
		return 0 // another process handles these
	}
	if !*yes && !ask(question(verb, batch)) {
		return 0
	}

	var failed []string
	for _, p := range batch {
		if err := apply(verb, p); err != nil {
			failed = append(failed, err.Error())
		}
	}
	if failed != nil {
		showError(fmt.Sprintf("%d of %d could not be moved:\n\n%s", len(failed), len(batch),
			strings.Join(failed, "\n\n")))
		return 1
	}
	return 0
}

func apply(verb, path string) error {
	// Start at path itself: a folder that holds the marker folders is a drive top and is refused
	root, err := findRoot(path)
	if err != nil {
		return err
	}
	switch verb {
	case "archive":
		_, err = putAway(path, root, archiveDir)
	case "trash":
		_, err = putAway(path, root, trashDir)
	case "restore":
		_, err = restore(path, root)
	}
	return err
}

func question(verb string, paths []string) string {
	var head, tail string
	switch verb {
	case "archive":
		head, tail = "Move to the archive?", "They are moved to "+archiveDir+" at the top of the drive, in the same folders as now."
	case "trash":
		head, tail = "Move to the trash?", "They are moved to "+trashDir+" at the top of the drive, in the same folders as now."
	case "restore":
		head, tail = "Restore to the original place?", "If something there already has the same name, a date is added to the restored name."
	}
	var list []string
	for i, p := range paths {
		if i == maxListed {
			list = append(list, fmt.Sprintf("... and %d more", len(paths)-maxListed))
			break
		}
		list = append(list, "    "+filepath.Base(p))
	}
	return head + "\n\n" + strings.Join(list, "\n") + "\n\n" + tail
}

const installedText = `The right-click menu has been added.

Right-click a file or folder on the drive and choose "` + appName + `".
On Windows 11, choose "Show more options" first.`

// setup is what a double-click on the exe does.
func setup() int {
	if menuInstalled() {
		if ask("The \"" + appName + "\" right-click menu is installed.\n\nRemove it?") {
			return report(uninstallMenu(), "The right-click menu has been removed.")
		}
		return 0
	}
	if ask("Add \"" + appName + "\" to the right-click menu for files and folders?\n\n" +
		"It lets you move them to the archive or trash of the drive, and back.") {
		return report(installMenu(), installedText)
	}
	return 0
}

func report(err error, done string) int {
	if err != nil {
		showError(err.Error())
		return 1
	}
	showInfo(done)
	return 0
}
