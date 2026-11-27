package main

// The moves themselves: same rules and cleanup.log format as g-drive-cleanup.py.

import (
	"errors"
	"fmt"
	"os"
	"os/user"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

const (
	archiveDir = "0_ARCHIVE___"
	trashDir   = "0_TRASH___"
	logName    = "cleanup.log"
)

var markers = []string{archiveDir, trashDir}

var windows = runtime.GOOS == "windows"

func pathEqual(a, b string) bool {
	if windows {
		return strings.EqualFold(a, b)
	}
	return a == b
}

func isDir(path string) bool {
	fi, err := os.Stat(path)
	return err == nil && fi.IsDir()
}

func exists(path string) bool {
	_, err := os.Lstat(path)
	return err == nil
}

func isMarker(name string) bool {
	for _, m := range markers {
		if strings.EqualFold(name, m) {
			return true
		}
	}
	return false
}

// findRoot returns the nearest folder from start upwards that holds both marker folders.
func findRoot(start string) (string, error) {
	dir := start
	for {
		if isDir(filepath.Join(dir, archiveDir)) && isDir(filepath.Join(dir, trashDir)) {
			return dir, nil
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			return "", fmt.Errorf("%s is not on a drive with %s and %s folders at the top", start, archiveDir, trashDir)
		}
		dir = parent
	}
}

// below returns path relative to root ("" for root itself), or false if path is outside root.
// Plain text comparison: paths are never resolved, so G:\ stays G:\ and never turns into \\server\share.
func below(path, root string) (string, bool) {
	if pathEqual(path, root) {
		return "", true
	}
	prefix := strings.TrimRight(root, `\/`) + string(filepath.Separator)
	if len(path) > len(prefix) && pathEqual(path[:len(prefix)], prefix) {
		return path[len(prefix):], true
	}
	return "", false
}

// splitFirst splits "a\b\c" into "a" and "b\c".
func splitFirst(rel string) (string, string) {
	first, rest, _ := strings.Cut(rel, string(filepath.Separator))
	return first, rest
}

func relative(path, root string) (string, error) {
	rel, ok := below(path, root)
	if !ok {
		return "", fmt.Errorf("%s is outside the drive top %s", path, root)
	}
	if rel == "" {
		return "", fmt.Errorf("refusing to move the drive top %s itself", root)
	}
	if first, _ := splitFirst(rel); isMarker(first) {
		return "", fmt.Errorf("%s is already in %s", path, first)
	}
	return rel, nil
}

// freeName returns path, or path with a timestamp added if something already has that name.
func freeName(path string) string {
	if !exists(path) {
		return path
	}
	stamped := path + "." + time.Now().Format("2006-01-02T150405")
	for i := 2; exists(stamped); i++ {
		stamped = fmt.Sprintf("%s.%s-%d", path, time.Now().Format("2006-01-02T150405"), i)
	}
	return stamped
}

func move(src, dst string) error {
	if err := os.MkdirAll(filepath.Dir(dst), 0o777); err != nil {
		return fmt.Errorf("could not create %s: %w", filepath.Dir(dst), unwrapPath(err))
	}
	// os.Rename fails instead of copying when src and dst are on different drives,
	// so a move is always a quick rename on the server
	if err := os.Rename(src, dst); err != nil {
		return fmt.Errorf("could not move %s: %w", src, unwrapPath(err))
	}
	return nil
}

// unwrapPath drops the "rename a b:" prefix that would repeat the paths already in the message.
func unwrapPath(err error) error {
	var le *os.LinkError
	var pe *os.PathError
	switch {
	case errors.As(err, &le):
		return le.Err
	case errors.As(err, &pe):
		return pe.Err
	}
	return err
}

func userName() string {
	for _, env := range []string{"USERNAME", "USER"} {
		if name := os.Getenv(env); name != "" {
			return name
		}
	}
	if u, err := user.Current(); err == nil {
		return u.Username
	}
	return "?"
}

func writeLog(markerDir, op, src, dst, root string) error {
	srcRel, _ := below(src, root)
	dstRel, _ := below(dst, root)
	line := strings.Join([]string{time.Now().Format("2006-01-02T15:04:05"), userName(), op,
		filepath.ToSlash(srcRel), filepath.ToSlash(dstRel)}, "\t")
	newline := "\n"
	if windows {
		newline = "\r\n" // what the Python version writes on Windows
	}
	f, err := os.OpenFile(filepath.Join(markerDir, logName), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o666)
	if err != nil {
		return err
	}
	_, err = f.WriteString(line + newline)
	if cerr := f.Close(); err == nil {
		err = cerr
	}
	return err
}

// putAway moves path into marker (archiveDir or trashDir), keeping its path below the drive top.
func putAway(path, root, marker string) (string, error) {
	rel, err := relative(path, root)
	if err != nil {
		return "", err
	}
	if !exists(path) {
		return "", fmt.Errorf("%s does not exist", path)
	}
	dst := freeName(filepath.Join(root, marker, rel))
	if err := move(path, dst); err != nil {
		return "", err
	}
	if err := writeLog(filepath.Join(root, marker), strings.Trim(marker, "0_"), path, dst, root); err != nil {
		return dst, fmt.Errorf("moved %s, but could not write %s: %w", path, logName, err)
	}
	return dst, nil
}

// restore moves an item back out of the archive or trash. path is either the item inside
// 0_ARCHIVE___ or 0_TRASH___ (as picked in Explorer), or the place it was moved away from.
func restore(path, root string) (string, error) {
	rel, ok := below(path, root)
	if !ok {
		return "", fmt.Errorf("%s is outside the drive top %s", path, root)
	}
	if rel == "" {
		return "", fmt.Errorf("refusing to move the drive top %s itself", root)
	}
	var src, dst, markerDir string
	if first, inner := splitFirst(rel); isMarker(first) {
		if inner == "" {
			return "", fmt.Errorf("pick something inside %s, not the folder itself", first)
		}
		src, dst, markerDir = path, filepath.Join(root, inner), filepath.Join(root, first)
	} else {
		var found []string
		for _, m := range markers {
			if exists(filepath.Join(root, m, rel)) {
				found = append(found, m)
			}
		}
		switch len(found) {
		case 0:
			return "", fmt.Errorf("%s is in neither %s nor %s", filepath.ToSlash(rel), archiveDir, trashDir)
		case 2:
			return "", fmt.Errorf("%s is in both %s and %s; restore it from inside one of them",
				filepath.ToSlash(rel), archiveDir, trashDir)
		}
		markerDir = filepath.Join(root, found[0])
		src, dst = filepath.Join(markerDir, rel), path
	}
	if !exists(src) {
		return "", fmt.Errorf("%s does not exist", src)
	}
	dst = freeName(dst)
	if err := move(src, dst); err != nil {
		return "", err
	}
	pruneEmpty(filepath.Dir(src), markerDir)
	if err := writeLog(markerDir, "RESTORE", src, dst, root); err != nil {
		return dst, fmt.Errorf("restored %s, but could not write %s: %w", dst, logName, err)
	}
	return dst, nil
}

// pruneEmpty drops the now empty parent folders that were only made to hold the moved item.
func pruneEmpty(dir, stop string) {
	for !pathEqual(dir, stop) {
		if _, ok := below(dir, stop); !ok || os.Remove(dir) != nil {
			return
		}
		dir = filepath.Dir(dir)
	}
}
