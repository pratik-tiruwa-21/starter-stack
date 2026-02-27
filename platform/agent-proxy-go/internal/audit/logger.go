// Package audit provides an immutable hash-chained JSONL audit logger.
package audit

import (
	"bufio"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"time"

	"github.com/ClawdContextOS/agent-proxy/internal/models"
)

// Logger maintains an immutable hash-chained audit log.
type Logger struct {
	mu       sync.Mutex
	file     *os.File
	filepath string
	prevHash string
	entries  []models.AuditEntry
	count    int64
}

// NewLogger opens (or creates) the audit JSONL file and loads existing entries.
func NewLogger(path string) (*Logger, error) {
	// Ensure directory exists
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0755); err != nil {
		return nil, fmt.Errorf("audit: mkdir %s: %w", dir, err)
	}

	l := &Logger{
		filepath: path,
		prevHash: "GENESIS",
	}

	// Load existing chain
	if err := l.loadExisting(); err != nil {
		return nil, fmt.Errorf("audit: load: %w", err)
	}

	// Open for appending
	f, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
	if err != nil {
		return nil, fmt.Errorf("audit: open %s: %w", path, err)
	}
	l.file = f

	return l, nil
}

// loadExisting reads the JSONL file and rebuilds the hash chain state.
func (l *Logger) loadExisting() error {
	f, err := os.Open(l.filepath)
	if err != nil {
		if os.IsNotExist(err) {
			return nil
		}
		return err
	}
	defer f.Close()

	scanner := bufio.NewScanner(f)
	// Max line size 1 MiB for large entries
	scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)

	for scanner.Scan() {
		line := scanner.Bytes()
		if len(line) == 0 {
			continue
		}
		var entry models.AuditEntry
		if err := json.Unmarshal(line, &entry); err != nil {
			continue // Skip malformed
		}
		l.entries = append(l.entries, entry)
		l.prevHash = entry.EntryHash
		l.count++
	}

	return scanner.Err()
}

// Log records a new audit entry and returns its hash.
func (l *Logger) Log(skill, tool, decision, reason string, checks map[string]any) string {
	l.mu.Lock()
	defer l.mu.Unlock()

	ts := time.Now().UTC().Format(time.RFC3339Nano)
	payload := fmt.Sprintf("%s|%s|%s|%s|%s", ts, skill, tool, decision, l.prevHash)
	hash := fmt.Sprintf("%x", sha256.Sum256([]byte(payload)))[:16]

	entry := models.AuditEntry{
		Timestamp: ts,
		Skill:     skill,
		Tool:      tool,
		Decision:  decision,
		Reason:    reason,
		Checks:    checks,
		PrevHash:  l.prevHash,
		EntryHash: hash,
	}

	l.entries = append(l.entries, entry)
	l.prevHash = hash
	l.count++

	// Write to file
	if l.file != nil {
		b, _ := json.Marshal(entry)
		l.file.Write(b)
		l.file.Write([]byte("\n"))
	}

	return hash
}

// Recent returns the last N entries.
func (l *Logger) Recent(limit int) []models.AuditEntry {
	l.mu.Lock()
	defer l.mu.Unlock()

	if limit <= 0 || limit > len(l.entries) {
		limit = len(l.entries)
	}
	start := len(l.entries) - limit
	result := make([]models.AuditEntry, limit)
	copy(result, l.entries[start:])
	return result
}

// VerifyChain validates the full hash chain and returns status.
func (l *Logger) VerifyChain() models.ChainStatus {
	l.mu.Lock()
	defer l.mu.Unlock()

	if len(l.entries) == 0 {
		return models.ChainStatus{Valid: true, Total: 0, Verified: 0}
	}

	prev := "GENESIS"
	for i, entry := range l.entries {
		if entry.PrevHash != prev {
			return models.ChainStatus{
				Valid:      false,
				Total:      len(l.entries),
				Verified:   i,
				FirstEntry: l.entries[0].Timestamp,
				LastEntry:  l.entries[len(l.entries)-1].Timestamp,
			}
		}
		prev = entry.EntryHash
	}

	return models.ChainStatus{
		Valid:      true,
		Total:      len(l.entries),
		Verified:   len(l.entries),
		FirstEntry: l.entries[0].Timestamp,
		LastEntry:  l.entries[len(l.entries)-1].Timestamp,
	}
}

// Count returns the total number of audit entries.
func (l *Logger) Count() int64 {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.count
}

// Close flushes and closes the audit file.
func (l *Logger) Close() error {
	l.mu.Lock()
	defer l.mu.Unlock()
	if l.file != nil {
		return l.file.Close()
	}
	return nil
}
