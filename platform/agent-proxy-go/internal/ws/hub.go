// Package ws provides a WebSocket hub for real-time dashboard events.
package ws

import (
	"encoding/json"
	"log"
	"net/http"
	"sync"
	"time"

	"github.com/gorilla/websocket"

	"github.com/ClawdContextOS/agent-proxy/internal/models"
)

var upgrader = websocket.Upgrader{
	ReadBufferSize:  1024,
	WriteBufferSize: 4096,
	CheckOrigin:     func(r *http.Request) bool { return true },
}

// Client is a single WebSocket connection.
type Client struct {
	hub  *Hub
	conn *websocket.Conn
	send chan []byte
}

// Hub manages all connected WebSocket clients and broadcasts events.
type Hub struct {
	mu         sync.RWMutex
	clients    map[*Client]bool
	broadcast  chan []byte
	register   chan *Client
	unregister chan *Client
	count      int
}

// NewHub creates a new WebSocket hub and starts its event loop.
func NewHub() *Hub {
	h := &Hub{
		clients:    make(map[*Client]bool),
		broadcast:  make(chan []byte, 256),
		register:   make(chan *Client),
		unregister: make(chan *Client),
	}
	go h.run()
	return h
}

func (h *Hub) run() {
	for {
		select {
		case client := <-h.register:
			h.mu.Lock()
			h.clients[client] = true
			h.count = len(h.clients)
			h.mu.Unlock()

		case client := <-h.unregister:
			h.mu.Lock()
			if _, ok := h.clients[client]; ok {
				delete(h.clients, client)
				close(client.send)
				h.count = len(h.clients)
			}
			h.mu.Unlock()

		case msg := <-h.broadcast:
			h.mu.RLock()
			for client := range h.clients {
				select {
				case client.send <- msg:
				default:
					// Client too slow; disconnect
					go func(c *Client) {
						h.unregister <- c
					}(client)
				}
			}
			h.mu.RUnlock()
		}
	}
}

// Broadcast sends a WSEvent to all connected clients.
func (h *Hub) Broadcast(event models.WSEvent) {
	b, err := json.Marshal(event)
	if err != nil {
		return
	}
	h.broadcast <- b
}

// BroadcastJSON sends raw JSON bytes to all clients.
func (h *Hub) BroadcastJSON(data any) {
	b, err := json.Marshal(data)
	if err != nil {
		return
	}
	h.broadcast <- b
}

// ClientCount returns the number of connected WebSocket clients.
func (h *Hub) ClientCount() int {
	h.mu.RLock()
	defer h.mu.RUnlock()
	return h.count
}

// HandleWebSocket upgrades HTTP to WebSocket and manages the connection.
func (h *Hub) HandleWebSocket(w http.ResponseWriter, r *http.Request) {
	conn, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		log.Printf("[ws] upgrade error: %v", err)
		return
	}

	client := &Client{
		hub:  h,
		conn: conn,
		send: make(chan []byte, 64),
	}
	h.register <- client

	// Send connected event
	connected := map[string]string{"type": "connected"}
	if b, err := json.Marshal(connected); err == nil {
		client.send <- b
	}

	// Writer goroutine
	go func() {
		defer func() {
			conn.Close()
		}()
		for msg := range client.send {
			conn.SetWriteDeadline(time.Now().Add(10 * time.Second))
			if err := conn.WriteMessage(websocket.TextMessage, msg); err != nil {
				return
			}
		}
	}()

	// Reader goroutine (handles ping/pong/close)
	go func() {
		defer func() {
			h.unregister <- client
			conn.Close()
		}()
		conn.SetReadLimit(512)
		conn.SetReadDeadline(time.Now().Add(300 * time.Second))
		conn.SetPongHandler(func(string) error {
			conn.SetReadDeadline(time.Now().Add(300 * time.Second))
			return nil
		})
		for {
			_, msg, err := conn.ReadMessage()
			if err != nil {
				break
			}
			// Handle ping from client
			if string(msg) == "ping" {
				pong := map[string]string{"type": "pong"}
				if b, err := json.Marshal(pong); err == nil {
					select {
					case client.send <- b:
					default:
					}
				}
			} else if string(msg) == "status" {
				// Status requests handled by the server layer
			}
		}
	}()
}
