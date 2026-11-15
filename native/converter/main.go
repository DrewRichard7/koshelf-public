// KoShelf's offline entry point for the pinned kindle-cli AZW3 writer.
package main

import (
	"fmt"
	"os"

	"github.com/seapy/kindle-cli/internal/azw3"
	"github.com/seapy/kindle-cli/internal/epub"
)

func convert(input, output string) error {
	book, err := epub.Read(input)
	if err != nil {
		return err
	}
	if err := azw3.CheckImageBudget(book); err != nil {
		return err
	}
	metadata := epub.ResolveMetadata(book, input, "", "")
	_, err = azw3.Write(book, output, azw3.Options{
		Title: metadata.Title, Author: metadata.Author, DocType: "PDOC",
	})
	return err
}

func main() {
	if len(os.Args) != 3 {
		fmt.Fprintln(os.Stderr, "Usage: koshelf-kindle-converter input.epub output.azw3")
		os.Exit(2)
	}
	if err := convert(os.Args[1], os.Args[2]); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
