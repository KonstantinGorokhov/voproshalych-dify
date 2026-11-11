package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"os/signal"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	maxbot "github.com/max-messenger/max-bot-api-client-go"
	"github.com/max-messenger/max-bot-api-client-go/schemes"
)

// Settings хранит настройки запуска MAX-адаптера.
type Settings struct {
	MaxBotToken               string
	BotCoreURL                string
	BotCoreTimeoutSeconds     time.Duration
	MaxAPIURL                 string
	MaxConcurrentUpdates      int
	RequestDelayAfterFailures time.Duration
}

// IncomingMessage описывает JSON-контракт входящего сообщения для core.
type IncomingMessage struct {
	Platform    string         `json:"platform"`
	MessageType string         `json:"message_type"`
	UserID      string         `json:"user_id"`
	ChatID      string         `json:"chat_id"`
	Text        string         `json:"text"`
	MessageID   string         `json:"message_id,omitempty"`
	Timestamp   string         `json:"timestamp,omitempty"`
	Metadata    map[string]any `json:"metadata"`
}

// CallbackEvent описывает JSON-контракт callback-события для core.
type CallbackEvent struct {
	Platform     string         `json:"platform"`
	UserID       string         `json:"user_id"`
	ChatID       string         `json:"chat_id"`
	CallbackData string         `json:"callback_data"`
	MessageID    string         `json:"message_id,omitempty"`
	Metadata     map[string]any `json:"metadata"`
}

// OutgoingAction описывает действие, которое должен выполнить адаптер.
type OutgoingAction struct {
	Type          string                  `json:"type"`
	Text          string                  `json:"text,omitempty"`
	ParseMode     string                  `json:"parse_mode,omitempty"`
	Buttons       [][]InlineButton        `json:"buttons,omitempty"`
	ReplyKeyboard [][]ReplyKeyboardButton `json:"reply_keyboard,omitempty"`
	Metadata      map[string]any          `json:"metadata,omitempty"`
}

// BotResponse описывает ответ core-сервиса.
type BotResponse struct {
	Actions []OutgoingAction `json:"actions"`
}

// InlineButton описывает inline-кнопку из ответа core.
type InlineButton struct {
	Text         string `json:"text"`
	CallbackData string `json:"callback_data"`
	URL          string `json:"url,omitempty"`
}

// ReplyKeyboardButton описывает кнопку reply-клавиатуры.
type ReplyKeyboardButton struct {
	Text string `json:"text"`
}

// CoreClient отправляет нормализованные сообщения в общий core.
type CoreClient struct {
	baseURL string
	client  *http.Client
}

// NewSettings считывает настройки из переменных окружения.
func NewSettings() Settings {
	timeoutSeconds := 300 * time.Second
	if rawTimeout := os.Getenv("BOT_CORE_TIMEOUT_SECONDS"); rawTimeout != "" {
		if parsedTimeout, err := time.ParseDuration(rawTimeout + "s"); err == nil {
			timeoutSeconds = parsedTimeout
		}
	}

	return Settings{
		MaxBotToken:               os.Getenv("MAX_BOT_TOKEN"),
		BotCoreURL:                valueOrDefault(os.Getenv("BOT_CORE_URL"), "http://127.0.0.1:8000"),
		BotCoreTimeoutSeconds:     timeoutSeconds,
		MaxAPIURL:                 valueOrDefault(os.Getenv("MAX_API_URL"), "https://platform-api.max.ru"),
		MaxConcurrentUpdates:      32,
		RequestDelayAfterFailures: 2 * time.Second,
	}
}

// NewCoreClient создает HTTP-клиент для вызовов core-сервиса.
func NewCoreClient(settings Settings) *CoreClient {
	return &CoreClient{
		baseURL: settings.BotCoreURL,
		client: &http.Client{
			Timeout: settings.BotCoreTimeoutSeconds,
		},
	}
}

// ProcessMessage отправляет сообщение в core и возвращает список действий.
func (c *CoreClient) ProcessMessage(ctx context.Context, payload IncomingMessage) (BotResponse, error) {
	body, err := json.Marshal(payload)
	if err != nil {
		return BotResponse{}, fmt.Errorf("не удалось сериализовать запрос в core: %w", err)
	}

	request, err := http.NewRequestWithContext(
		ctx,
		http.MethodPost,
		c.baseURL+"/messages",
		bytes.NewReader(body),
	)
	if err != nil {
		return BotResponse{}, fmt.Errorf("не удалось создать запрос в core: %w", err)
	}

	request.Header.Set("Content-Type", "application/json")

	response, err := c.client.Do(request)
	if err != nil {
		return BotResponse{}, fmt.Errorf("не удалось отправить запрос в core: %w", err)
	}
	defer response.Body.Close()

	if response.StatusCode >= http.StatusBadRequest {
		responseBody, _ := io.ReadAll(response.Body)
		return BotResponse{}, fmt.Errorf(
			"core вернул статус %d: %s",
			response.StatusCode,
			string(responseBody),
		)
	}

	var botResponse BotResponse
	if err := json.NewDecoder(response.Body).Decode(&botResponse); err != nil {
		return BotResponse{}, fmt.Errorf("не удалось разобрать ответ core: %w", err)
	}

	return botResponse, nil
}

// ProcessCallback отправляет callback-событие в core и возвращает список действий.
func (c *CoreClient) ProcessCallback(ctx context.Context, payload CallbackEvent) (BotResponse, error) {
	body, err := json.Marshal(payload)
	if err != nil {
		return BotResponse{}, fmt.Errorf("не удалось сериализовать callback в core: %w", err)
	}

	request, err := http.NewRequestWithContext(
		ctx,
		http.MethodPost,
		c.baseURL+"/callbacks",
		bytes.NewReader(body),
	)
	if err != nil {
		return BotResponse{}, fmt.Errorf("не удалось создать callback запрос в core: %w", err)
	}

	request.Header.Set("Content-Type", "application/json")

	response, err := c.client.Do(request)
	if err != nil {
		return BotResponse{}, fmt.Errorf("не удалось отправить callback в core: %w", err)
	}
	defer response.Body.Close()

	if response.StatusCode >= http.StatusBadRequest {
		responseBody, _ := io.ReadAll(response.Body)
		return BotResponse{}, fmt.Errorf(
			"core вернул статус %d для callback: %s",
			response.StatusCode,
			string(responseBody),
		)
	}

	var botResponse BotResponse
	if err := json.NewDecoder(response.Body).Decode(&botResponse); err != nil {
		return BotResponse{}, fmt.Errorf("не удалось разобрать callback ответ core: %w", err)
	}

	return botResponse, nil
}

func main() {
	settings := NewSettings()
	if settings.MaxBotToken == "" {
		log.Fatal("MAX_BOT_TOKEN is not set")
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, os.Interrupt)
	defer stop()

	api, err := maxbot.New(settings.MaxBotToken, maxbot.WithBaseURL(strings.TrimRight(settings.MaxAPIURL, "/")+"/"))
	if err != nil {
		log.Fatalf("не удалось создать MAX API клиент: %v", err)
	}

	coreClient := NewCoreClient(settings)
	messenger := NewMessenger(api, settings)
	messenger.RegisterCommands(ctx)

	// Обновления обрабатываются параллельно: ответ Dify может идти десятки
	// секунд, и один долгий вопрос не должен задерживать остальных пользователей.
	// Очерёдность вопросов одного пользователя обеспечивает bot-core.
	slots := make(chan struct{}, settings.MaxConcurrentUpdates)
	var wg sync.WaitGroup
	for update := range api.GetUpdates(ctx) {
		slots <- struct{}{}
		wg.Add(1)
		go func(update any) {
			defer wg.Done()
			defer func() { <-slots }()
			if err := handleUpdate(ctx, messenger, coreClient, update); err != nil {
				log.Printf("ошибка обработки MAX update: %v", err)
				time.Sleep(settings.RequestDelayAfterFailures)
			}
		}(update)
	}
	wg.Wait()
}

// handleUpdate обрабатывает только текстовые сообщения и пересылает их в core.
func handleUpdate(
	ctx context.Context,
	messenger *Messenger,
	coreClient *CoreClient,
	update any,
) error {
	if callbackUpdate, ok := update.(*schemes.MessageCallbackUpdate); ok {
		return handleCallbackUpdate(ctx, messenger, coreClient, callbackUpdate)
	}

	messageUpdate, ok := update.(*schemes.MessageCreatedUpdate)
	if !ok {
		return nil
	}

	payload, err := buildIncomingMessage(messageUpdate)
	if err != nil {
		return err
	}

	pendingMID := ""
	if shouldShowPendingMessage(payload) {
		var chatID int64
		var userID int64
		switch {
		case messageUpdate.Message.Recipient.ChatId != 0:
			chatID = messageUpdate.Message.Recipient.ChatId
		case messageUpdate.Message.Sender.UserId != 0:
			userID = messageUpdate.Message.Sender.UserId
		}

		if userID != 0 || chatID != 0 {
			pendingMID = messenger.SendTemporary(ctx, userID, chatID,
				"Сейчас я попробую ответить на этот вопрос, это может занять какое-то время...",
			)
		}
	}

	response, err := coreClient.ProcessMessage(ctx, payload)
	messenger.Delete(ctx, pendingMID)
	if err != nil {
		if sendErr := sendFallbackMessage(ctx, messenger, messageUpdate); sendErr != nil {
			return errors.Join(err, sendErr)
		}
		return err
	}

	for _, action := range response.Actions {
		if action.Type != "send_text" || action.Text == "" {
			continue
		}

		text := action.Text
		if action.ParseMode == "HTML" {
			text = stripHTMLToPlain(text)
		}

		var chatID int64
		var userID int64
		switch {
		case messageUpdate.Message.Recipient.ChatId != 0:
			chatID = messageUpdate.Message.Recipient.ChatId
		case messageUpdate.Message.Sender.UserId != 0:
			userID = messageUpdate.Message.Sender.UserId
		default:
			return fmt.Errorf("в MAX update нет chat_id и user_id для ответа")
		}

		buttons := action.Buttons
		if len(action.ReplyKeyboard) > 0 {
			buttons = convertReplyToInline(action.ReplyKeyboard)
		}

		if err := messenger.Send(ctx, userID, chatID, text, buttons); err != nil {
			return fmt.Errorf("не удалось отправить ответ в MAX: %w", err)
		}
	}

	return nil
}

func handleCallbackUpdate(
	ctx context.Context,
	messenger *Messenger,
	coreClient *CoreClient,
	update *schemes.MessageCallbackUpdate,
) error {
	callbackEvent, err := buildCallbackEvent(update)
	if err != nil {
		return err
	}

	response, err := coreClient.ProcessCallback(ctx, callbackEvent)
	if err != nil {
		return err
	}

	userID, err := parseOptionalInt64(callbackEvent.UserID)
	if err != nil {
		return err
	}
	chatID, err := parseOptionalInt64(callbackEvent.ChatID)
	if err != nil {
		return err
	}

	for _, action := range response.Actions {
		if action.Type != "send_text" || action.Text == "" {
			continue
		}

		text := action.Text
		if action.ParseMode == "HTML" {
			text = stripHTMLToPlain(text)
		}

		if err := messenger.Send(ctx, userID, chatID, text, action.Buttons); err != nil {
			return fmt.Errorf("не удалось отправить callback ответ в MAX: %w", err)
		}
	}

	return nil
}

// buildIncomingMessage нормализует MAX update к контракту core.
func buildIncomingMessage(update *schemes.MessageCreatedUpdate) (IncomingMessage, error) {
	message := update.Message

	var userID string
	if message.Sender.UserId != 0 {
		userID = fmt.Sprintf("%d", message.Sender.UserId)
	}

	var chatID string
	switch {
	case message.Recipient.ChatId != 0:
		chatID = fmt.Sprintf("%d", message.Recipient.ChatId)
	case message.Recipient.UserId != 0:
		chatID = fmt.Sprintf("%d", message.Recipient.UserId)
	}

	if userID == "" || chatID == "" {
		return IncomingMessage{}, fmt.Errorf("в MAX update не хватает user_id или chat_id")
	}

	incoming := IncomingMessage{
		Platform:    "max",
		MessageType: detectMessageType(message),
		UserID:      userID,
		ChatID:      chatID,
		Text:        message.Body.Text,
		Metadata:    map[string]any{},
	}

	if message.Timestamp != 0 {
		incoming.Timestamp = formatUnixTimestamp(message.Timestamp)
	}

	incoming.MessageID = message.Body.Mid
	incoming.Metadata["sender_name"] = message.Sender.Name

	return incoming, nil
}

// buildCallbackEvent нормализует MAX callback update к контракту core.
func buildCallbackEvent(update *schemes.MessageCallbackUpdate) (CallbackEvent, error) {
	rawUpdate, err := json.Marshal(update)
	if err != nil {
		return CallbackEvent{}, fmt.Errorf("не удалось сериализовать MAX callback update: %w", err)
	}

	var payload map[string]any
	if err := json.Unmarshal(rawUpdate, &payload); err != nil {
		return CallbackEvent{}, fmt.Errorf("не удалось разобрать MAX callback update: %w", err)
	}

	callbackMap, _ := payload["callback"].(map[string]any)
	messageMap, _ := payload["message"].(map[string]any)
	recipientMap, _ := messageMap["recipient"].(map[string]any)
	bodyMap, _ := messageMap["body"].(map[string]any)
	userMap, _ := callbackMap["user"].(map[string]any)

	userID := extractNumericString(userMap["user_id"])
	chatID := extractNumericString(recipientMap["chat_id"])
	if chatID == "" {
		chatID = extractNumericString(recipientMap["user_id"])
	}

	callbackData := extractString(callbackMap["payload"])
	messageID := extractString(bodyMap["mid"])
	if userID == "" || chatID == "" || callbackData == "" {
		return CallbackEvent{}, fmt.Errorf("в MAX callback update не хватает user_id, chat_id или callback_data")
	}

	return CallbackEvent{
		Platform:     "max",
		UserID:       userID,
		ChatID:       chatID,
		CallbackData: callbackData,
		MessageID:    messageID,
		Metadata: map[string]any{
			"callback_id": callbackMap["callback_id"],
		},
	}, nil
}

// sendFallbackMessage отправляет короткий ответ, если core временно недоступен.
func sendFallbackMessage(
	ctx context.Context,
	messenger *Messenger,
	update *schemes.MessageCreatedUpdate,
) error {
	var chatID int64
	var userID int64
	switch {
	case update.Message.Recipient.ChatId != 0:
		chatID = update.Message.Recipient.ChatId
	case update.Message.Sender.UserId != 0:
		userID = update.Message.Sender.UserId
	default:
		return fmt.Errorf("некуда отправить fallback-сообщение MAX")
	}

	if err := messenger.Send(ctx, userID, chatID, "Сервис временно недоступен.", nil); err != nil {
		return fmt.Errorf("не удалось отправить fallback-сообщение MAX: %w", err)
	}

	return nil
}

func parseOptionalInt64(value string) (int64, error) {
	if value == "" {
		return 0, nil
	}
	parsed, err := strconv.ParseInt(value, 10, 64)
	if err != nil {
		return 0, fmt.Errorf("не удалось преобразовать значение %q в int64: %w", value, err)
	}
	return parsed, nil
}

func extractNumericString(value any) string {
	switch typed := value.(type) {
	case float64:
		return fmt.Sprintf("%.0f", typed)
	case int64:
		return fmt.Sprintf("%d", typed)
	case int:
		return fmt.Sprintf("%d", typed)
	case json.Number:
		return typed.String()
	case string:
		return typed
	default:
		return ""
	}
}

func extractString(value any) string {
	switch typed := value.(type) {
	case string:
		return typed
	default:
		if typed == nil {
			return ""
		}
		raw, err := json.Marshal(typed)
		if err != nil {
			return ""
		}
		var asString string
		if err := json.Unmarshal(raw, &asString); err == nil {
			return asString
		}
		return string(raw)
	}
}

func valueOrDefault(value string, defaultValue string) string {
	if value == "" {
		return defaultValue
	}
	return value
}

// formatUnixTimestamp нормализует timestamp из MAX в RFC3339.
func formatUnixTimestamp(timestamp int64) string {
	switch {
	case timestamp >= 1_000_000_000_000_000:
		return time.UnixMicro(timestamp).UTC().Format(time.RFC3339)
	case timestamp >= 1_000_000_000_000:
		return time.UnixMilli(timestamp).UTC().Format(time.RFC3339)
	default:
		return time.Unix(timestamp, 0).UTC().Format(time.RFC3339)
	}
}

// detectMessageType определяет платформенно-независимый тип сообщения MAX.
func detectMessageType(message schemes.Message) string {
	if message.Body.Text != "" {
		return "text"
	}
	return "unknown"
}

// addKeyboardToMessage добавляет inline-кнопки к сообщению MAX.
func addKeyboardToMessage(message *maxbot.Message, rows [][]InlineButton) {
	if len(rows) == 0 {
		return
	}

	keyboard := &maxbot.Keyboard{}
	for _, row := range rows {
		if len(row) == 0 {
			continue
		}

		keyboardRow := keyboard.AddRow()
		for _, button := range row {
			if button.URL != "" {
				keyboardRow.AddLink(
					button.Text,
					schemes.DEFAULT,
					button.URL,
				)
			} else {
				keyboardRow.AddCallback(
					button.Text,
					detectButtonIntent(button.CallbackData),
					button.CallbackData,
				)
			}
		}
	}

	message.AddKeyboard(keyboard)
}

// detectButtonIntent подбирает визуальный intent для inline-кнопки.
func detectButtonIntent(callbackData string) schemes.Intent {
	switch {
	case strings.HasPrefix(callbackData, "feedback:like"):
		return schemes.POSITIVE
	case strings.HasPrefix(callbackData, "feedback:dislike"):
		return schemes.NEGATIVE
	default:
		return schemes.DEFAULT
	}
}

// shouldShowPendingMessage определяет, нужно ли показывать сообщение ожидания.
func shouldShowPendingMessage(msg IncomingMessage) bool {
	if msg.MessageType != "text" {
		return false
	}

	normalized := strings.TrimSpace(strings.ToLower(msg.Text))
	excluded := map[string]bool{
		"/start":         true,
		"start":          true,
		"начать":         true,
		"📋 помощь":       true,
		"🔄 новый диалог": true,
		"/new":           true,
		"/help":          true,
	}

	return !excluded[normalized]
}

// stripHTMLToPlain преобразует HTML-разметку в обычный текст.
func stripHTMLToPlain(text string) string {
	linkRegex := regexp.MustCompile(`<a href="([^"]+)">([^<]+)</a>`)
	text = linkRegex.ReplaceAllString(text, "$2\n— $1")
	return text
}

// convertReplyToInline преобразует reply-кнопки в inline-кнопки
// с корректными callback_data для обработки в core.
func convertReplyToInline(rows [][]ReplyKeyboardButton) [][]InlineButton {
	buttonMapping := map[string]string{
		"📋 Помощь":       "menu:help",
		"🔄 Новый диалог": "menu:new_dialog",
	}

	var result [][]InlineButton
	for _, row := range rows {
		var buttonRow []InlineButton
		for _, btn := range row {
			callbackData := btn.Text
			if mapped, ok := buttonMapping[btn.Text]; ok {
				callbackData = mapped
			}
			buttonRow = append(buttonRow, InlineButton{
				Text:         btn.Text,
				CallbackData: callbackData,
			})
		}
		result = append(result, buttonRow)
	}
	return result
}
