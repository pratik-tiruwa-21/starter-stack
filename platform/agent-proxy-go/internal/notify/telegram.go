package notify

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"
)

// Telegram sends notifications via Telegram Bot API.
type Telegram struct {
	botToken string
	chatID   string
}

// NewTelegram creates a Telegram channel.
func NewTelegram(botToken, chatID string) *Telegram {
	return &Telegram{botToken: botToken, chatID: chatID}
}

func (t *Telegram) Name() string  { return "telegram" }
func (t *Telegram) Enabled() bool { return t.botToken != "" && t.chatID != "" }

func (t *Telegram) Send(event Event) error {
	title, body := FormatEvent(event)
	icon := telegramIcon(event)
	text := fmt.Sprintf("%s *%s*\n\n%s", icon, title, body)
	payload := map[string]any{
		"chat_id":    t.chatID,
		"text":       text,
		"parse_mode": "Markdown",
	}
	return t.post("sendMessage", payload)
}

func (t *Telegram) SendHITL(event Event) (string, error) {
	title, body := FormatEvent(event)
	text := fmt.Sprintf("*HUMAN APPROVAL REQUIRED*\n\n*%s*\n%s\n\nApproval ID: _%s_", title, body, event.ApprovalID)
	keyboard := map[string]any{
		"inline_keyboard": [][]map[string]any{
			{
				{"text": "Approve", "callback_data": "approve:" + event.ApprovalID},
				{"text": "Deny", "callback_data": "deny:" + event.ApprovalID},
			},
		},
	}
	payload := map[string]any{
		"chat_id":      t.chatID,
		"text":         text,
		"parse_mode":   "Markdown",
		"reply_markup": keyboard,
	}
	err := t.post("sendMessage", payload)
	return event.ApprovalID, err
}

// AnswerCallback responds to a Telegram callback query.
func (t *Telegram) AnswerCallback(callbackID, text string) error {
	payload := map[string]any{
		"callback_query_id": callbackID,
		"text":              text,
	}
	return t.post("answerCallbackQuery", payload)
}

func (t *Telegram) post(method string, payload any) error {
	url := fmt.Sprintf("https://api.telegram.org/bot%s/%s", t.botToken, method)
	b, err := json.Marshal(payload)
	if err != nil {
		return fmt.Errorf("telegram marshal: %w", err)
	}
	client := &http.Client{Timeout: 10 * time.Second}
	resp, err := client.Post(url, "application/json", bytes.NewReader(b))
	if err != nil {
		return fmt.Errorf("telegram send: %w", err)
	}
	defer resp.Body.Close()
	io.Copy(io.Discard, resp.Body)
	if resp.StatusCode >= 400 {
		return fmt.Errorf("telegram status %d", resp.StatusCode)
	}
	return nil
}

func telegramIcon(e Event) string {
	switch e.Decision {
	case "DENY":
		return "\xe2\x9b\x94"
	case "HUMAN_GATE":
		return "\xe2\x9a\xa0\xef\xb8\x8f"
	default:
		return "\xe2\x9c\x85"
	}
}
