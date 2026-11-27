//go:build !windows

package main

// Terminal stand-ins for the Windows dialogs, so the tool can be tried on Linux.

import (
	"bufio"
	"fmt"
	"os"
	"strings"
)

func ask(text string) bool {
	fmt.Fprintf(os.Stderr, "%s\n\n[y/N] ", text)
	answer, _ := bufio.NewReader(os.Stdin).ReadString('\n')
	return strings.EqualFold(strings.TrimSpace(answer), "y")
}

func showError(text string) { fmt.Fprintln(os.Stderr, "error: "+text) }
func showInfo(text string)  { fmt.Fprintln(os.Stderr, text) }
