package main

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	maxbot "github.com/max-messenger/max-bot-api-client-go"
)

type fakeMAX struct {
	mu       sync.Mutex
	sent     []map[string]any
	edits    map[string]map[string]any
	deleted  []string
	commands []byte
}

func newFakeMAX(t *testing.T) (*fakeMAX, *httptest.Server) {
	fake := &fakeMAX{edits: map[string]map[string]any{}}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		fake.mu.Lock()
		defer fake.mu.Unlock()
		raw, _ := io.ReadAll(r.Body)
		var body map[string]any
		_ = json.Unmarshal(raw, &body)
		w.Header().Set("Content-Type", "application/json")
		switch {
		case r.Method == http.MethodPost && r.URL.Path == "/messages":
			fake.sent = append(fake.sent, body)
			mid := "mid." + string(rune('a'+len(fake.sent)-1))
			_, _ = io.WriteString(w, `{"message":{"body":{"mid":"`+mid+`","text":"x"}}}`)
		case r.Method == http.MethodPut && r.URL.Path == "/messages":
			fake.edits[r.URL.Query().Get("message_id")] = body
			_, _ = io.WriteString(w, `{"success":true}`)
		case r.Method == http.MethodDelete && r.URL.Path == "/messages":
			fake.deleted = append(fake.deleted, r.URL.Query().Get("message_id"))
			_, _ = io.WriteString(w, `{"success":true}`)
		case r.Method == http.MethodPatch && r.URL.Path == "/me/commands":
			fake.commands = raw
			_, _ = io.WriteString(w, `{"commands":[]}`)
		default:
			t.Errorf("unexpected request %s %s", r.Method, r.URL.String())
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	t.Cleanup(server.Close)
	return fake, server
}

func newTestMessenger(t *testing.T, server *httptest.Server) *Messenger {
	api, err := maxbot.New("token", maxbot.WithBaseURL(server.URL+"/"))
	if err != nil {
		t.Fatal(err)
	}
	return NewMessenger(api, Settings{MaxBotToken: "token", MaxAPIURL: server.URL})
}

// payloads возвращает callback-payload всех кнопок из attachments сообщения.
func payloads(body map[string]any) []string {
	var result []string
	attachments, _ := body["attachments"].([]any)
	for _, attachment := range attachments {
		payload := attachment.(map[string]any)["payload"].(map[string]any)
		for _, row := range payload["buttons"].([]any) {
			for _, button := range row.([]any) {
				b := button.(map[string]any)
				if p, ok := b["payload"].(string); ok {
					result = append(result, p)
				} else {
					result = append(result, "link:"+b["url"].(string))
				}
			}
		}
	}
	return result
}

func TestNewDialogButtonMovesToLastMessage(t *testing.T) {
	fake, server := newFakeMAX(t)
	m := newTestMessenger(t, server)
	ctx := context.Background()

	answer := [][]InlineButton{
		{{Text: "Источник", URL: "https://utmn.ru"}},
		{{Text: "❤️", CallbackData: "feedback:like:1"}, {Text: "👎", CallbackData: "feedback:dislike:1"}},
	}
	if err := m.Send(ctx, 0, 10, "Ответ 1", answer); err != nil {
		t.Fatal(err)
	}
	if err := m.Send(ctx, 0, 10, "Рад помочь", nil); err != nil {
		t.Fatal(err)
	}
	// Сообщение в другой чат не должно трогать кнопки чата 10.
	if err := m.Send(ctx, 0, 20, "Другой чат", nil); err != nil {
		t.Fatal(err)
	}

	got := strings.Join(payloads(fake.sent[0]), ",")
	if got != "link:https://utmn.ru,feedback:like:1,feedback:dislike:1,dialog:start_new" {
		t.Fatalf("first message buttons: %s", got)
	}
	if got := strings.Join(payloads(fake.sent[1]), ","); got != "dialog:start_new" {
		t.Fatalf("second message buttons: %s", got)
	}
	if len(fake.edits) != 1 {
		t.Fatalf("expected exactly one edit, got %v", fake.edits)
	}
	edit := fake.edits["mid.a"]
	if got := strings.Join(payloads(edit), ","); got != "link:https://utmn.ru,feedback:like:1,feedback:dislike:1" {
		t.Fatalf("edited buttons: %s", got)
	}
	if edit["text"] != "Ответ 1" || edit["notify"] != false {
		t.Fatalf("unexpected edit body: %v", edit)
	}
}

func TestEditRemovesKeyboardWithEmptyAttachments(t *testing.T) {
	fake, server := newFakeMAX(t)
	m := newTestMessenger(t, server)
	ctx := context.Background()

	_ = m.Send(ctx, 5, 0, "История сброшена", nil)
	_ = m.Send(ctx, 5, 0, "Ответ", nil)

	attachments, ok := fake.edits["mid.a"]["attachments"].([]any)
	if !ok || len(attachments) != 0 {
		t.Fatalf("attachments must be an empty array, got %#v", fake.edits["mid.a"]["attachments"])
	}
}

func TestStartMenuIsNotDuplicated(t *testing.T) {
	rows := [][]InlineButton{
		{{Text: "📋 Помощь", CallbackData: "menu:help"}},
		{{Text: newDialogLabel, CallbackData: newDialogCallback}},
	}
	if got := withNewDialogButton(rows); len(got) != 2 {
		t.Fatalf("button duplicated: %v", got)
	}
}

func TestTemporaryMessageAndCommands(t *testing.T) {
	fake, server := newFakeMAX(t)
	m := newTestMessenger(t, server)
	ctx := context.Background()

	mid := m.SendTemporary(ctx, 0, 10, "Ищу ответ...")
	if len(payloads(fake.sent[0])) != 0 {
		t.Fatal("temporary message must have no buttons")
	}
	m.Delete(ctx, mid)
	if len(fake.deleted) != 1 || fake.deleted[0] != mid {
		t.Fatalf("deleted: %v", fake.deleted)
	}
	_ = m.Send(ctx, 0, 10, "Ответ", nil)
	if len(fake.edits) != 0 {
		t.Fatal("temporary message must not be tracked as last message")
	}

	m.RegisterCommands(ctx)
	if !strings.Contains(string(fake.commands), `"name":"new"`) {
		t.Fatalf("commands not registered: %s", fake.commands)
	}
}

func TestFeedbackIntentWithID(t *testing.T) {
	if detectButtonIntent("feedback:like:12") != "positive" || detectButtonIntent("feedback:dislike:12") != "negative" {
		t.Fatal("feedback buttons lost their color")
	}
}
