// Command release-sign generates release signing keys and signs agent binaries.
//
//	release-sign keygen  -out keys/            # writes release.key (secret) and release.pub
//	release-sign sign    -key keys/release.key -version 1.4.0 -platform linux -arch amd64 -file dist/dtsys-agent-linux-amd64
//
// "sign" prints the base64 signature; build-release.sh stores it as <binary>.sig.
package main

import (
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/dtsys/agent/internal/releasesig"
)

func main() {
	if len(os.Args) < 2 {
		usage()
	}
	var err error
	switch os.Args[1] {
	case "keygen":
		err = keygen(os.Args[2:])
	case "sign":
		err = sign(os.Args[2:])
	default:
		usage()
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}
}

func usage() {
	fmt.Fprintln(os.Stderr, "usage: release-sign keygen -out DIR | sign -key FILE -version V -platform P -arch A -file BIN")
	os.Exit(2)
}

func keygen(args []string) error {
	fs := flag.NewFlagSet("keygen", flag.ExitOnError)
	out := fs.String("out", ".", "output directory")
	_ = fs.Parse(args)

	pub, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(*out, 0o700); err != nil {
		return err
	}
	keyPath := filepath.Join(*out, "release.key")
	if _, err := os.Stat(keyPath); err == nil {
		return fmt.Errorf("%s already exists; refusing to overwrite a signing key", keyPath)
	}
	if err := os.WriteFile(keyPath, []byte(base64.StdEncoding.EncodeToString(priv.Seed())+"\n"), 0o600); err != nil {
		return err
	}
	pubB64 := base64.StdEncoding.EncodeToString(pub)
	if err := os.WriteFile(filepath.Join(*out, "release.pub"), []byte(pubB64+"\n"), 0o644); err != nil {
		return err
	}
	fmt.Println(pubB64)
	fmt.Fprintf(os.Stderr, "Secret key: %s (keep offline, never commit)\n", keyPath)
	return nil
}

func sign(args []string) error {
	fs := flag.NewFlagSet("sign", flag.ExitOnError)
	keyFile := fs.String("key", "", "secret key file from keygen")
	version := fs.String("version", "", "release version")
	platform := fs.String("platform", "", "linux|darwin|windows")
	arch := fs.String("arch", "", "amd64|arm64")
	file := fs.String("file", "", "binary to sign")
	_ = fs.Parse(args)
	if *keyFile == "" || *version == "" || *platform == "" || *arch == "" || *file == "" {
		usage()
	}

	raw, err := os.ReadFile(*keyFile)
	if err != nil {
		return err
	}
	seed, err := base64.StdEncoding.DecodeString(strings.TrimSpace(string(raw)))
	if err != nil || len(seed) != ed25519.SeedSize {
		return fmt.Errorf("invalid key file")
	}
	data, err := os.ReadFile(*file)
	if err != nil {
		return err
	}
	digest := sha256.Sum256(data)
	fmt.Println(releasesig.Sign(ed25519.NewKeyFromSeed(seed), *version, *platform, *arch, hex.EncodeToString(digest[:])))
	return nil
}
