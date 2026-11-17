package main

import (
	"errors"
	"io"
	"net"
	"sync"
	"time"
)

// Minimal MQTT 3.1.1 publisher (QoS 0, optional LWT). No dependencies.
type MQTT struct {
	addr, user, pass, clientID string
	willTopic                  string
	mu                         sync.Mutex
	conn                       net.Conn
	gen                        int // increments on every successful (re)connect
}

func (m *MQTT) Generation() int {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.gen
}

func str(s string) []byte { return append([]byte{byte(len(s) >> 8), byte(len(s))}, s...) }

func remLen(n int) []byte {
	var b []byte
	for {
		d := byte(n % 128)
		n /= 128
		if n > 0 {
			d |= 0x80
		}
		b = append(b, d)
		if n == 0 {
			return b
		}
	}
}

func (m *MQTT) connect() error {
	c, err := net.DialTimeout("tcp", m.addr, 10*time.Second)
	if err != nil {
		return err
	}
	flags := byte(0x02) // clean session
	payload := str(m.clientID)
	if m.willTopic != "" {
		flags |= 0x04 | 0x20 // will + will retain
		payload = append(payload, str(m.willTopic)...)
		payload = append(payload, str("offline")...)
	}
	if m.user != "" {
		flags |= 0x80
		payload = append(payload, str(m.user)...)
		if m.pass != "" {
			flags |= 0x40
			payload = append(payload, str(m.pass)...)
		}
	}
	vh := append(str("MQTT"), 0x04, flags, 0x00, 60) // keepalive 60s
	pkt := append([]byte{0x10}, remLen(len(vh)+len(payload))...)
	pkt = append(append(pkt, vh...), payload...)
	c.SetDeadline(time.Now().Add(10 * time.Second))
	if _, err := c.Write(pkt); err != nil {
		c.Close()
		return err
	}
	ack := make([]byte, 4)
	if _, err := io.ReadFull(c, ack); err != nil {
		c.Close()
		return err
	}
	if ack[0] != 0x20 || ack[3] != 0 {
		c.Close()
		return errors.New("MQTT connection refused (check credentials)")
	}
	c.SetDeadline(time.Time{})
	m.conn = c
	m.gen++
	// drain incoming (PINGRESP) and detect disconnects; ping to keep alive
	go func() {
		buf := make([]byte, 256)
		for {
			if _, err := c.Read(buf); err != nil {
				m.mu.Lock()
				if m.conn == c {
					m.conn = nil
				}
				m.mu.Unlock()
				c.Close()
				return
			}
		}
	}()
	go func() {
		for {
			time.Sleep(30 * time.Second)
			m.mu.Lock()
			if m.conn != c {
				m.mu.Unlock()
				return
			}
			c.Write([]byte{0xC0, 0x00})
			m.mu.Unlock()
		}
	}()
	return nil
}

// Publish connects lazily. A dead connection is detected on write, so it reconnects and
// retries once before giving up (the next call reconnects again).
func (m *MQTT) Publish(topic, payload string, retain bool) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	hdr := byte(0x30)
	if retain {
		hdr |= 1
	}
	body := append(str(topic), payload...)
	pkt := append(append([]byte{hdr}, remLen(len(body))...), body...)
	var err error
	for attempt := 0; attempt < 2; attempt++ {
		if m.conn == nil {
			if err = m.connect(); err != nil {
				return err
			}
		}
		m.conn.SetWriteDeadline(time.Now().Add(10 * time.Second))
		if _, err = m.conn.Write(pkt); err == nil {
			return nil
		}
		m.conn.Close()
		m.conn = nil
	}
	return err
}
