package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"sync"
	"time"

	maxbot "github.com/max-messenger/max-bot-api-client-go"
	"github.com/max-messenger/max-bot-api-client-go/schemes"
)

// В MAX нет клавиатуры у поля ввода (как reply-клавиатура в Telegram): кнопки
// бывают только inline, внутри сообщения. Поэтому кнопка «Новый диалог»
// прикрепляется к каждому сообщению бота и снимается с предыдущего — так она
// всегда остаётся под последним сообщением. Дополнительно регистрируются
// команды бота (/new, /help), которые MAX показывает в подсказках при вводе «/».

const (
	newDialogCallback = "dialog:start_new"
	newDialogLabel    = "🔄 Новый диалог"
)

// botCommands — команды из меню бота; их обрабатывает bot-core.
var botCommands = []schemes.BotCommand{
	{Name: "new", Description: "Начать новый диалог"},
	{Name: "help", Description: "Помощь и контакты"},
}

// sentMessage — последнее сообщение бота в чате, с которого нужно снять кнопку.
type sentMessage struct {
	mid     string
	text    string
	buttons [][]InlineButton
}

// Messenger отправляет сообщения в MAX и следит, чтобы кнопка «Новый диалог»
// была только под последним сообщением бота в каждом чате.
type Messenger struct {
	api    *maxbot.Api
	http   *http.Client
	apiURL string
	token  string

	mu        sync.Mutex
	last      map[string]sentMessage
	chatLocks map[string]*sync.Mutex
}

// NewMessenger создаёт отправителя сообщений.
func NewMessenger(api *maxbot.Api, settings Settings) *Messenger {
	return &Messenger{
		api:       api,
		http:      &http.Client{Timeout: 30 * time.Second},
		apiURL:    strings.TrimRight(settings.MaxAPIURL, "/"),
		token:     settings.MaxBotToken,
		last:      make(map[string]sentMessage),
		chatLocks: make(map[string]*sync.Mutex),
	}
}

func chatKey(userID, chatID int64) string {
	if chatID != 0 {
		return "c" + strconv.FormatInt(chatID, 10)
	}
	return "u" + strconv.FormatInt(userID, 10)
}

func (m *Messenger) lockChat(key string) func() {
	m.mu.Lock()
	lock, ok := m.chatLocks[key]
	if !ok {
		lock = &sync.Mutex{}
		m.chatLocks[key] = lock
	}
	m.mu.Unlock()
	lock.Lock()
	return lock.Unlock
}

// Send отправляет сообщение с кнопкой «Новый диалог» и снимает её с предыдущего
// сообщения бота в этом чате.
func (m *Messenger) Send(
	ctx context.Context,
	userID int64,
	chatID int64,
	text string,
	buttons [][]InlineButton,
) error {
	if text == "" {
		return fmt.Errorf("пустой текст сообщения для отправки")
	}
	key := chatKey(userID, chatID)
	unlock := m.lockChat(key)
	defer unlock()

	buttons = withNewDialogButton(buttons)
	sent, err := m.send(ctx, userID, chatID, text, buttons)
	if err != nil {
		return err
	}

	m.mu.Lock()
	previous, hasPrevious := m.last[key]
	if sent.Body.Mid != "" {
		m.last[key] = sentMessage{mid: sent.Body.Mid, text: text, buttons: buttons}
	}
	m.mu.Unlock()

	if hasPrevious && previous.mid != sent.Body.Mid {
		remaining := withoutNewDialogButton(previous.buttons)
		if err := m.editButtons(ctx, previous.mid, previous.text, remaining); err != nil {
			// Не критично: на старом сообщении просто останется лишняя кнопка.
			log.Printf("не удалось снять кнопку «Новый диалог» с %s: %v", previous.mid, err)
		}
	}
	return nil
}

// SendTemporary отправляет служебное сообщение без кнопок (например, «ищу ответ»)
// и возвращает его id, чтобы потом удалить.
func (m *Messenger) SendTemporary(ctx context.Context, userID, chatID int64, text string) string {
	sent, err := m.send(ctx, userID, chatID, text, nil)
	if err != nil {
		log.Printf("не удалось отправить временное сообщение MAX: %v", err)
		return ""
	}
	return sent.Body.Mid
}

// Delete удаляет сообщение бота.
func (m *Messenger) Delete(ctx context.Context, mid string) {
	if mid == "" {
		return
	}
	if _, err := m.api.Messages.DeleteMessage(ctx, mid); err != nil {
		log.Printf("не удалось удалить сообщение MAX %s: %v", mid, err)
	}
}

func (m *Messenger) send(
	ctx context.Context,
	userID int64,
	chatID int64,
	text string,
	buttons [][]InlineButton,
) (*schemes.Message, error) {
	message := maxbot.NewMessage()
	message.SetText(text)
	addKeyboardToMessage(message, buttons)

	switch {
	case chatID != 0:
		message.SetChat(chatID)
	case userID != 0:
		message.SetUser(userID)
	default:
		return nil, fmt.Errorf("не указан chat_id или user_id для отправки")
	}
	return m.api.Messages.SendWithResult(ctx, message)
}

// editButtons заменяет клавиатуру сообщения. Прямой запрос PUT /messages нужен,
// потому что убрать все кнопки можно только пустым массивом attachments, а
// клиентская библиотека в этом случае отправляет null («без изменений»).
func (m *Messenger) editButtons(ctx context.Context, mid, text string, buttons [][]InlineButton) error {
	body := map[string]any{
		"text":        text,
		"attachments": keyboardAttachments(buttons),
		"notify":      false,
	}
	endpoint := m.apiURL + "/messages?message_id=" + url.QueryEscape(mid)
	return m.request(ctx, http.MethodPut, endpoint, body)
}

// RegisterCommands регистрирует команды бота в меню MAX.
func (m *Messenger) RegisterCommands(ctx context.Context) {
	err := m.request(ctx, http.MethodPatch, m.apiURL+"/me/commands", map[string]any{
		"commands": botCommands,
	})
	if err == nil {
		log.Printf("команды бота MAX зарегистрированы")
		return
	}
	log.Printf("PATCH /me/commands не удался (%v), пробую PATCH /me", err)
	if _, err := m.api.Bots.PatchBot(ctx, &schemes.BotPatch{Commands: botCommands}); err != nil {
		log.Printf("не удалось зарегистрировать команды бота MAX: %v", err)
	}
}

func (m *Messenger) request(ctx context.Context, method, endpoint string, payload any) error {
	raw, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	request, err := http.NewRequestWithContext(ctx, method, endpoint, bytes.NewReader(raw))
	if err != nil {
		return err
	}
	request.Header.Set("Authorization", m.token)
	request.Header.Set("Content-Type", "application/json")

	response, err := m.http.Do(request)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	answer, _ := io.ReadAll(io.LimitReader(response.Body, 2048))
	if response.StatusCode >= 300 {
		return fmt.Errorf("MAX API %s %d: %s", method, response.StatusCode, strings.TrimSpace(string(answer)))
	}
	var result struct {
		Success *bool  `json:"success"`
		Message string `json:"message"`
	}
	if json.Unmarshal(answer, &result) == nil && result.Success != nil && !*result.Success {
		return fmt.Errorf("MAX API %s: %s", method, result.Message)
	}
	return nil
}

func isNewDialogButton(button InlineButton) bool {
	return button.CallbackData == newDialogCallback || button.CallbackData == "menu:new_dialog"
}

// withNewDialogButton добавляет строку с кнопкой «Новый диалог», если её ещё нет.
func withNewDialogButton(rows [][]InlineButton) [][]InlineButton {
	for _, row := range rows {
		for _, button := range row {
			if isNewDialogButton(button) {
				return rows
			}
		}
	}
	result := make([][]InlineButton, 0, len(rows)+1)
	result = append(result, rows...)
	return append(result, []InlineButton{{Text: newDialogLabel, CallbackData: newDialogCallback}})
}

// withoutNewDialogButton убирает кнопку «Новый диалог», остальные кнопки сохраняет.
func withoutNewDialogButton(rows [][]InlineButton) [][]InlineButton {
	result := make([][]InlineButton, 0, len(rows))
	for _, row := range rows {
		kept := make([]InlineButton, 0, len(row))
		for _, button := range row {
			if !isNewDialogButton(button) {
				kept = append(kept, button)
			}
		}
		if len(kept) > 0 {
			result = append(result, kept)
		}
	}
	return result
}

// keyboardAttachments собирает attachments для PUT /messages; пустой список
// кнопок даёт пустой массив, то есть удаление клавиатуры.
func keyboardAttachments(rows [][]InlineButton) []any {
	if len(rows) == 0 {
		return []any{}
	}
	buttonRows := make([][]map[string]any, 0, len(rows))
	for _, row := range rows {
		buttonRow := make([]map[string]any, 0, len(row))
		for _, button := range row {
			if button.URL != "" {
				buttonRow = append(buttonRow, map[string]any{
					"type": "link", "text": button.Text, "url": button.URL,
				})
				continue
			}
			buttonRow = append(buttonRow, map[string]any{
				"type":    "callback",
				"text":    button.Text,
				"payload": button.CallbackData,
				"intent":  string(detectButtonIntent(button.CallbackData)),
			})
		}
		buttonRows = append(buttonRows, buttonRow)
	}
	return []any{map[string]any{
		"type":    "inline_keyboard",
		"payload": map[string]any{"buttons": buttonRows},
	}}
}
