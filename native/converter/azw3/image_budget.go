package azw3

import (
	"bytes"
	"fmt"
	"image"

	"github.com/seapy/kindle-cli/internal/epub"
)

// CheckImageBudget bounds the images that the writer may keep in decoded form.
// JPEG, PNG and GIF assets that the writer passes through do not use this budget.
func CheckImageBudget(book *epub.Book) error {
	const maxImagePixels = 25_000_000
	const maxDecodedPixels = 64_000_000
	var decodedPixels int64
	for path, data := range book.Images {
		config, _, err := image.DecodeConfig(bytes.NewReader(data))
		if err != nil {
			continue // The writer skips files it cannot decode.
		}
		pixels := int64(config.Width) * int64(config.Height)
		if pixels <= 0 || pixels > maxImagePixels {
			return fmt.Errorf("an EPUB image exceeds the supported dimensions")
		}
		if _, passthrough := classifyImage(data); !passthrough {
			decodedPixels += pixels
		}
		if path == book.CoverPath {
			decodedPixels += pixels // The cover is always decoded separately.
		}
		if decodedPixels > maxDecodedPixels {
			return fmt.Errorf("the EPUB has too many large images that require conversion")
		}
	}
	return nil
}
