package main

import (
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"time"
)

// Inverter speaks Modbus-RTU-over-TCP with Easun inverters. The inverter is the
// TCP *client*: we listen on localIP:port and tell it where via a UDP packet.
type Inverter struct {
	invIP, localIP string
	port           int
	ln             net.Listener
	connCh         chan net.Conn
	conn           net.Conn
	everConnected  bool
	txID           uint16
}

func NewInverter(invIP, localIP string, port int) *Inverter {
	return &Inverter{invIP: invIP, localIP: localIP, port: port, txID: 0x0772}
}

func crc16(data []byte) uint16 {
	crc := uint16(0xFFFF)
	for _, b := range data {
		crc ^= uint16(b)
		for i := 0; i < 8; i++ {
			if crc&1 != 0 {
				crc = crc>>1 ^ 0xA001
			} else {
				crc >>= 1
			}
		}
	}
	return crc // low byte is sent first
}

// buildRequest mirrors easunpy.modbusclient.create_request (function 0x03).
func buildRequest(tx uint16, start, count uint16) []byte {
	rtu := []byte{0x00, 0x03, byte(start >> 8), byte(start), byte(count >> 8), byte(count)}
	c := crc16(rtu)
	rtu = append(rtu, byte(c), byte(c>>8))
	rtu = append([]byte{0xFF, 0x04}, rtu...)
	req := make([]byte, 6, 6+len(rtu))
	binary.BigEndian.PutUint16(req[0:], tx)
	binary.BigEndian.PutUint16(req[2:], 0x0001)
	binary.BigEndian.PutUint16(req[4:], uint16(len(rtu)))
	return append(req, rtu...)
}

func (i *Inverter) listen() error {
	for p := i.port; p < i.port+20; p++ {
		ln, err := net.Listen("tcp", fmt.Sprintf("%s:%d", i.localIP, p))
		if err != nil {
			continue
		}
		i.ln, i.port = ln, p
		i.connCh = make(chan net.Conn, 1)
		go func() {
			for {
				c, err := ln.Accept()
				if err != nil {
					return
				}
				log.Printf("inverter connected from %s", c.RemoteAddr())
				// keep only the newest connection
				select {
				case old := <-i.connCh:
					old.Close()
				default:
				}
				i.connCh <- c
			}
		}()
		log.Printf("TCP server listening on %s:%d", i.localIP, p)
		return nil
	}
	return errors.New("no free TCP port")
}

func (i *Inverter) sendUDP() {
	c, err := net.Dial("udp", net.JoinHostPort(i.invIP, "58899"))
	if err != nil {
		log.Printf("udp: %v", err)
		return
	}
	defer c.Close()
	c.Write([]byte(fmt.Sprintf("set>server=%s:%d;", i.localIP, i.port)))
}

func (i *Inverter) reset() {
	i.dropConn()
	if i.ln != nil {
		i.ln.Close()
		i.ln = nil
	}
	i.everConnected = false // force UDP next time
}

func (i *Inverter) dropConn() {
	if i.conn != nil {
		i.conn.Close()
		i.conn = nil
	}
}

func (i *Inverter) ensureConn() error {
	if i.conn != nil {
		return nil
	}
	if i.ln == nil {
		if err := i.listen(); err != nil {
			return err
		}
	}
	if i.everConnected { // inverter remembers our address; just wait
		select {
		case c := <-i.connCh:
			i.conn = c
			return nil
		case <-time.After(30 * time.Second):
			i.reset()
			return errors.New("inverter did not reconnect within 30s")
		}
	}
	for attempt := 0; attempt < 3; attempt++ {
		log.Printf("sending UDP set>server (attempt %d)", attempt+1)
		i.sendUDP()
		select {
		case c := <-i.connCh:
			i.conn, i.everConnected = c, true
			return nil
		case <-time.After(15 * time.Second):
		}
	}
	i.reset()
	return errors.New("inverter did not connect after UDP discovery")
}

// ReadRegisters reads count holding registers starting at start (signed int16).
func (i *Inverter) ReadRegisters(start, count uint16) ([]int16, error) {
	if err := i.ensureConn(); err != nil {
		return nil, err
	}
	i.txID++
	_ = i.conn.SetDeadline(time.Now().Add(5 * time.Second))
	if _, err := i.conn.Write(buildRequest(i.txID, start, count)); err != nil {
		i.dropConn()
		return nil, err
	}
	hdr := make([]byte, 6)
	if _, err := io.ReadFull(i.conn, hdr); err != nil {
		i.dropConn()
		return nil, err
	}
	n := int(binary.BigEndian.Uint16(hdr[4:]))
	if n < 5 || n > 1024 {
		i.dropConn()
		return nil, fmt.Errorf("bad response length %d", n)
	}
	body := make([]byte, n)
	if _, err := io.ReadFull(i.conn, body); err != nil {
		i.dropConn()
		return nil, err
	}
	// Same offsets as easunpy.decode_modbus_response: data length at [4], data from [5].
	nb := int(body[4])
	if nb < int(count)*2 || len(body) < 5+int(count)*2 {
		i.dropConn()
		return nil, fmt.Errorf("short response: %d data bytes for %d registers", nb, count)
	}
	vals := make([]int16, count)
	for k := range vals {
		vals[k] = int16(binary.BigEndian.Uint16(body[5+2*k:]))
	}
	return vals, nil
}

func (i *Inverter) Close() { i.reset() }
