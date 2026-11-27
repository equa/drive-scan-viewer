package main

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"testing"
)

// drive makes <tmp>/drive with both marker folders and the given files (dirs end in "/").
func drive(t *testing.T, items ...string) string {
	t.Helper()
	root := filepath.Join(t.TempDir(), "drive")
	for _, item := range append([]string{archiveDir + "/", trashDir + "/"}, items...) {
		p := filepath.Join(root, filepath.FromSlash(item))
		if strings.HasSuffix(item, "/") {
			must(t, os.MkdirAll(p, 0o777))
		} else {
			must(t, os.MkdirAll(filepath.Dir(p), 0o777))
			must(t, os.WriteFile(p, []byte(item), 0o666))
		}
	}
	return root
}

func must(t *testing.T, err error) {
	t.Helper()
	if err != nil {
		t.Fatal(err)
	}
}

func at(root, rel string) string { return filepath.Join(root, filepath.FromSlash(rel)) }

func wantExists(t *testing.T, path string, want bool) {
	t.Helper()
	if exists(path) != want {
		t.Errorf("exists(%s) = %v, want %v", path, !want, want)
	}
}

func TestFindRoot(t *testing.T) {
	root := drive(t, "Users/x/A/cfd/run/f")
	for _, start := range []string{at(root, "Users/x/A/cfd/run/f"), at(root, "Users/x/A/gone"), root} {
		got, err := findRoot(start)
		if err != nil || got != root {
			t.Errorf("findRoot(%s) = %q, %v", start, got, err)
		}
	}
	if _, err := findRoot(filepath.Dir(root)); err == nil {
		t.Error("findRoot above the drive should fail")
	}
	// One marker alone does not make a top
	must(t, os.MkdirAll(at(root, "Users/x/"+trashDir), 0o777))
	if got, _ := findRoot(at(root, "Users/x/A")); got != root {
		t.Errorf("single marker taken as top: %s", got)
	}
}

func TestPutAwayAndLog(t *testing.T) {
	root := drive(t, "Users/x/A/cfd/run/f", "Users/x/A/docs/")
	dst, err := putAway(at(root, "Users/x/A/cfd"), root, archiveDir)
	must(t, err)
	if want := at(root, archiveDir+"/Users/x/A/cfd"); dst != want {
		t.Errorf("dst = %s, want %s", dst, want)
	}
	wantExists(t, at(root, archiveDir+"/Users/x/A/cfd/run/f"), true)
	wantExists(t, at(root, "Users/x/A/cfd"), false)

	log, err := os.ReadFile(at(root, archiveDir+"/"+logName))
	must(t, err)
	fields := strings.Split(strings.TrimSpace(string(log)), "\t")
	if len(fields) != 5 || fields[2] != "ARCHIVE" || fields[3] != "Users/x/A/cfd" ||
		fields[4] != archiveDir+"/Users/x/A/cfd" {
		t.Errorf("log line = %q", log)
	}

	// Same name again: both are kept
	must(t, os.MkdirAll(at(root, "Users/x/A/cfd"), 0o777))
	dst, err = putAway(at(root, "Users/x/A/cfd"), root, archiveDir)
	must(t, err)
	if !strings.HasPrefix(filepath.Base(dst), "cfd.") {
		t.Errorf("second archive went to %s", dst)
	}
	wantExists(t, at(root, archiveDir+"/Users/x/A/cfd/run/f"), true)
}

func TestPutAwayRefuses(t *testing.T) {
	root := drive(t, "Users/x/A/")
	for _, p := range []string{root, at(root, trashDir), at(root, archiveDir+"/Users"),
		at(root, "Users/x/nope"), filepath.Dir(root)} {
		if _, err := putAway(p, root, trashDir); err == nil {
			t.Errorf("putAway(%s) should fail", p)
		}
	}
	wantExists(t, at(root, trashDir), true)
}

func TestRestore(t *testing.T) {
	root := drive(t, "Users/x/A/cfd/f", "Users/x/A/docs/g")
	_, err := putAway(at(root, "Users/x/A/cfd"), root, trashDir)
	must(t, err)
	_, err = putAway(at(root, "Users/x/A/docs"), root, archiveDir)
	must(t, err)

	// Picked inside the trash, as from Explorer
	dst, err := restore(at(root, trashDir+"/Users/x/A/cfd"), root)
	must(t, err)
	if dst != at(root, "Users/x/A/cfd") {
		t.Errorf("restored to %s", dst)
	}
	wantExists(t, at(root, "Users/x/A/cfd/f"), true)
	wantExists(t, at(root, trashDir+"/Users"), false) // emptied parents removed
	wantExists(t, at(root, trashDir+"/"+logName), true)

	// Given as the original place
	_, err = restore(at(root, "Users/x/A/docs"), root)
	must(t, err)
	wantExists(t, at(root, "Users/x/A/docs/g"), true)

	if _, err := restore(at(root, "Users/x/A/nothing"), root); err == nil {
		t.Error("restoring something never moved should fail")
	}
	if _, err := restore(at(root, trashDir), root); err == nil {
		t.Error("restoring the trash folder itself should fail")
	}
}

func TestRestoreAmbiguous(t *testing.T) {
	root := drive(t, "Users/x/A/f", "Users/x/B/g")
	_, err := putAway(at(root, "Users/x/A/f"), root, trashDir)
	must(t, err)
	_, err = putAway(at(root, "Users/x/B/g"), root, archiveDir)
	must(t, err)
	// Users/x exists in both as a parent folder
	if _, err := restore(at(root, "Users/x"), root); err == nil || !strings.Contains(err.Error(), "both") {
		t.Errorf("want 'in both' error, got %v", err)
	}
}

func TestDedupe(t *testing.T) {
	got := dedupe([]string{"b", "a", "b", "c", "a"})
	if strings.Join(got, ",") != "a,b,c" {
		t.Errorf("dedupe = %v", got)
	}
}

// TestBatch starts several processes at once, as Explorer does for a multi-selection,
// and checks that exactly one of them gets all the items.
func TestBatch(t *testing.T) {
	if os.Getenv("BATCH_CHILD") != "" {
		t.Skip("child")
	}
	exe, err := os.Executable()
	must(t, err)
	tmp := t.TempDir()
	const n = 8
	outs := make([]string, n)
	var wg sync.WaitGroup
	for i := 0; i < n; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			cmd := exec.Command(exe, "-test.run=^TestBatchChild$")
			cmd.Env = append(os.Environ(), "BATCH_CHILD="+string(rune('a'+i)), "TMPDIR="+tmp)
			out, err := cmd.CombinedOutput()
			if err != nil {
				t.Errorf("child %d: %v\n%s", i, err, out)
			}
			outs[i] = string(out)
		}(i)
	}
	wg.Wait()
	var leaders []string
	for _, out := range outs {
		for _, line := range strings.Split(out, "\n") {
			if strings.HasPrefix(line, "BATCH ") {
				leaders = append(leaders, line)
			}
		}
	}
	if len(leaders) != 1 || leaders[0] != "BATCH a,b,c,d,e,f,g,h" {
		t.Errorf("want one batch with all items, got %q", leaders)
	}
}

func TestBatchChild(t *testing.T) {
	item := os.Getenv("BATCH_CHILD")
	if item == "" {
		t.Skip("only run from TestBatch")
	}
	batch, err := collectBatch("test", []string{item})
	must(t, err)
	if len(batch) > 0 {
		os.Stdout.WriteString("BATCH " + strings.Join(batch, ",") + "\n")
	}
}
