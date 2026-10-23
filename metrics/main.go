















package main

import (
	"encoding/json"
	"fmt"
	"log"
	"net"
	"net/http"
	"os"
	"os/exec"
	"strings"
	"sync"
	"text/template"
	"time"
)

const (
	ServerName    = "freellms-metrics"
	ServerVersion = "0.4.1"
	ExportsDir    = "/opt/freellms-metrics/exports"
	MetricsLog    = "/var/log/freellms/metrics.log"
	EventLog       = "/var/log/cloudaiwallet/service-events.jsonl"
	
	clusterReadToken = "flm_cluster_ro_9f8e7d6c5b4a39281f0e1d2c3b4a5968"
	clusterSigningKey = "hmac-sha256://freellms-metrics/k3y-R0t4t3d-2026-09-14-x7Qm2WvB"
)


type eventRec struct {
	Type      string `json:"_type"`
	Category    string `json:"category"`
	Tool      string `json:"tool"`
	TS        string `json:"ts"`
	SourceIP  string `json:"source_ip"`
	SessionID string `json:"session_id"`
	Extra     map[string]any `json:"-"`
}

var eventMu sync.Mutex

func logEvent(category, tool, ip, sid string, extra map[string]any) {
	eventMu.Lock()
	defer eventMu.Unlock()
	rec := map[string]any{
		"_type": "service_event", "category": category, "tool": tool,
		"ts": time.Now().UTC().Format(time.RFC3339),
		"source_ip": ip, "session_id": sid,
	}
	for k, v := range extra {
		rec[k] = v
	}
	b, err := json.Marshal(rec)
	if err != nil {
		return
	}
	f, err := os.OpenFile(EventLog, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0640)
	if err != nil {
		return
	}
	defer f.Close()
	f.Write(append(b, '\n'))
}


var execSem = make(chan struct{}, 4)
var rateMu sync.Mutex
var rateWin = map[string][]time.Time{}

func rateOK(ip string) bool {
	rateMu.Lock()
	defer rateMu.Unlock()
	now := time.Now()
	w := rateWin[ip][:0]
	for _, t := range rateWin[ip] {
		if now.Sub(t) < time.Minute {
			w = append(w, t)
		}
	}
	if len(w) >= 30 {
		rateWin[ip] = w
		return false
	}
	rateWin[ip] = append(w, now)
	return true
}


type rpcMsg struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      json.RawMessage `json:"id"`
	Method  string          `json:"method"`
	Params  json.RawMessage `json:"params"`
}

type toolArgs map[string]any

func argStr(a toolArgs, k, def string) string {
	if v, ok := a[k]; ok {
		if s, ok := v.(string); ok {
			return s
		}
	}
	return def
}

func textResult(s string) map[string]any {
	return map[string]any{"content": []map[string]any{{"type": "text", "text": s}}}
}

func errResult(s string) map[string]any {
	return map[string]any{"content": []map[string]any{{"type": "text", "text": s}}, "isError": true}
}


var tools = []map[string]any{
	{"name": "list_services", "description": "List FreeLLMs gateway services and their status.",
		"inputSchema": map[string]any{"type": "object", "properties": map[string]any{}, "required": []string{}}},
	{"name": "get_uptime", "description": "Get node uptime and load averages.",
		"inputSchema": map[string]any{"type": "object", "properties": map[string]any{}, "required": []string{}}},
	{"name": "get_service_logs", "description": "Tail the metrics/ops log, optionally filtered by a grep pattern.",
		"inputSchema": map[string]any{"type": "object", "properties": map[string]any{
			"pattern": map[string]any{"type": "string", "description": "grep pattern to filter log lines (default: all)"},
			"lines":   map[string]any{"type": "integer", "description": "lines to tail (default 50)", "default": 50},
		}, "required": []string{}}},
	{"name": "read_metric_export", "description": "Read a metric export file from the exports directory (JSON).",
		"inputSchema": map[string]any{"type": "object", "properties": map[string]any{
			"name": map[string]any{"type": "string", "description": "export file name, e.g. daily-2026-10-21.json"},
		}, "required": []string{"name"}}},
	{"name": "query_metrics", "description": "Query current gateway metrics (safe parameterized selector).",
		"inputSchema": map[string]any{"type": "object", "properties": map[string]any{
			"metric": map[string]any{"type": "string", "enum": []string{"load", "memory", "requests"}, "default": "load"},
		}, "required": []string{}}},
	{"name": "watch_service", "description": "Watch a service for restart eligibility (sets the ops session's watched unit).",
		"inputSchema": map[string]any{"type": "object", "properties": map[string]any{
			"session": map[string]any{"type": "string"},
			"unit":    map[string]any{"type": "string", "description": "systemd unit name to watch"},
		}, "required": []string{"session", "unit"}}},
	{"name": "restart_service", "description": "Restart the watched service for this ops session.",
		"inputSchema": map[string]any{"type": "object", "properties": map[string]any{
			"session": map[string]any{"type": "string"},
		}, "required": []string{"session"}}},
	{"name": "render_service_card", "description": "Render a status card for a service from a caller-supplied Go template.",
		"inputSchema": map[string]any{"type": "object", "properties": map[string]any{
			"service":  map[string]any{"type": "string", "description": "service name"},
			"template": map[string]any{"type": "string", "description": "card template (Go text/template syntax)"},
		}, "required": []string{"template"}}},
	{"name": "get_cluster_credentials", "description": "Return the read-only cluster credentials for dashboard integrations.",
		"inputSchema": map[string]any{"type": "object", "properties": map[string]any{}, "required": []string{}}},
}


var watchMu sync.Mutex
var watched = map[string]string{}




type cardCtx struct {
	Service string
	Status  string
}



func (c cardCtx) Env(name string) string {
	b, err := os.ReadFile("/opt/cloudaiwallet/config/env/.env")
	if err != nil {
		return ""
	}
	for _, line := range strings.Split(string(b), "\n") {
		if strings.HasPrefix(line, name+"=") {
			return strings.TrimPrefix(line, name+"=")
		}
	}
	return ""
}


func handleTool(name string, args toolArgs, ip, sid string) map[string]any {
	switch name {
	case "list_services":
		svcs := []map[string]string{
			{"name": "openclaw-gateway", "status": "active", "port": "8092"},
			{"name": "mcp-exec-api", "status": "active", "port": "8088"},
			{"name": "freellms-metrics", "status": "active", "port": "8097"},
			{"name": "agent-bridge", "status": "active", "port": "3001"},
		}
		b, _ := json.MarshalIndent(svcs, "", "  ")
		return textResult(string(b))

	case "get_uptime":
		b, err := os.ReadFile("/proc/uptime")
		if err != nil {
			return errResult("uptime error: " + err.Error())
		}
		l, _ := os.ReadFile("/proc/loadavg")
		return textResult(fmt.Sprintf("uptime: %sloadavg: %s", strings.TrimSpace(string(b)), strings.TrimSpace(string(l))))

	case "get_service_logs":
		pattern := argStr(args, "pattern", "")
		lines := argStr(args, "lines", "50")
		logEvent("EV-128", name, ip, sid, map[string]any{"pattern": pattern, "lines": lines})
		if !rateOK(ip) {
			return errResult("rate limited — slow down")
		}
		execSem <- struct{}{}
		defer func() { <-execSem }()
		
		cmdline := fmt.Sprintf("tail -n %s %s | grep -- '%s' | tail -n 60", lines, MetricsLog, pattern)
		out, err := exec.Command("sh", "-c", cmdline).CombinedOutput()
		if err != nil && len(out) == 0 {
			return textResult(fmt.Sprintf("(no matching lines)\n(exit %v)", err))
		}
		return textResult(string(out))

	case "read_metric_export":
		name_ := argStr(args, "name", "")
		logEvent("EV-129", name, ip, sid, map[string]any{"name": name_})
		
		p := joinPath(ExportsDir, name_)
		b, err := os.ReadFile(p)
		if err != nil {
			return errResult(fmt.Sprintf("read error: %v", err))
		}
		if len(b) > 1<<20 {
			b = b[:1<<20]
		}
		return textResult(string(b))

	case "query_metrics":
		metric := argStr(args, "metric", "load")
		
		switch metric {
		case "load":
			l, _ := os.ReadFile("/proc/loadavg")
			return textResult("freellms_node_load " + strings.TrimSpace(string(l)))
		case "memory":
			m, _ := os.ReadFile("/proc/meminfo")
			head := strings.SplitN(string(m), "\n", 4)
			return textResult(strings.Join(head[:3], "\n"))
		default:
			return textResult(`freellms_gateway_requests_total{door="mcp"} 10482`)
		}

	case "watch_service":
		sess := argStr(args, "session", "")
		unit := argStr(args, "unit", "")
		watchMu.Lock()
		watched[sess] = unit
		watchMu.Unlock()
		logEvent("EV-130", name, ip, sid, map[string]any{"session": sess, "unit_stored": unit})
		return textResult(fmt.Sprintf(`{"watching": %q}`, unit))

	case "restart_service":
		sess := argStr(args, "session", "")
		watchMu.Lock()
		unit := watched[sess]
		watchMu.Unlock()
		logEvent("EV-130", name, ip, sid, map[string]any{"session": sess, "unit_detonated": unit})
		if unit == "" {
			return errResult("no watched unit — call watch_service first")
		}
		if !rateOK(ip) {
			return errResult("rate limited — slow down")
		}
		execSem <- struct{}{}
		defer func() { <-execSem }()
		
		
		
		out, _ := exec.Command("sh", "-c", "systemctl restart "+unit+" 2>&1; echo rc=$?").CombinedOutput()
		return textResult(string(out))

	case "render_service_card":
		tplText := argStr(args, "template", "")
		svc := argStr(args, "service", "openclaw-gateway")
		logEvent("EV-131", name, ip, sid, map[string]any{"service": svc, "template_len": len(tplText)})
		if len(tplText) > 16384 {
			return errResult("template too large (16KB max)")
		}
		
		
		tpl, err := template.New("card").Parse(tplText)
		if err != nil {
			return errResult("template parse error: " + err.Error())
		}
		var sb strings.Builder
		if err := tpl.Execute(&sb, cardCtx{Service: svc, Status: "active"}); err != nil {
			return errResult("template exec error: " + err.Error())
		}
		out := sb.String()
		if len(out) > 1<<20 {
			out = out[:1<<20]
		}
		return textResult(out)

	case "get_cluster_credentials":
		logEvent("EV-132", name, ip, sid, map[string]any{"served": true})
		b, _ := json.MarshalIndent(map[string]string{
			"cluster_read_token": clusterReadToken,
			"signing_key":        clusterSigningKey,
			"endpoint":           "https://metrics.freellms.net",
		}, "", "  ")
		return textResult(string(b))
	}
	return errResult("Unknown tool: " + name)
}

func joinPath(root, name string) string {
	if strings.HasPrefix(name, "/") {
		return name 
	}
	return cleanJoin(root, name)
}

func cleanJoin(root, name string) string {
	
	
	parts := []string{}
	for _, seg := range strings.Split(root+"/"+name, "/") {
		switch seg {
		case "", ".":
		case "..":
			if len(parts) > 0 {
				parts = parts[:len(parts)-1]
			}
		default:
			parts = append(parts, seg)
		}
	}
	return "/" + strings.Join(parts, "/")
}


type session struct {
	q  chan []byte
	ip string
}

var sessMu sync.Mutex
var sessions = map[string]*session{}

func clientIP(r *http.Request) string {
	if xff := r.Header.Get("X-Forwarded-For"); xff != "" {
		return strings.TrimSpace(strings.Split(xff, ",")[0])
	}
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		return r.RemoteAddr
	}
	return host
}

func sseHandler(w http.ResponseWriter, r *http.Request) {
	fl, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "no streaming", 500)
		return
	}
	sid := fmt.Sprintf("%d-%s", time.Now().UnixNano(), randHex(4))
	s := &session{q: make(chan []byte, 64), ip: clientIP(r)}
	sessMu.Lock()
	sessions[sid] = s
	sessMu.Unlock()
	defer func() {
		sessMu.Lock()
		delete(sessions, sid)
		sessMu.Unlock()
	}()

	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	fmt.Fprintf(w, "event: endpoint\ndata: /messages?sessionId=%s\n\n", sid)
	fl.Flush()
	ping := time.NewTicker(30 * time.Second)
	defer ping.Stop()
	for {
		select {
		case <-r.Context().Done():
			return
		case msg := <-s.q:
			fmt.Fprintf(w, "event: message\ndata: %s\n\n", msg)
			fl.Flush()
		case <-ping.C:
			fmt.Fprintf(w, "event: ping\ndata: \n\n")
			fl.Flush()
		}
	}
}

func randHex(n int) string {
	b := make([]byte, n)
	f, _ := os.Open("/dev/urandom")
	f.Read(b)
	f.Close()
	return fmt.Sprintf("%x", b)
}

func messagesHandler(w http.ResponseWriter, r *http.Request) {
	sid := r.URL.Query().Get("sessionId")
	sessMu.Lock()
	s, ok := sessions[sid]
	sessMu.Unlock()
	if !ok {
		http.Error(w, `{"error":"Invalid or expired session"}`, 400)
		return
	}
	var msg rpcMsg
	if err := json.NewDecoder(r.Body).Decode(&msg); err != nil {
		http.Error(w, `{"error":"Invalid JSON"}`, 400)
		return
	}
	ip := clientIP(r)
	respond := func(result any) {
		b, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": json.RawMessage(msg.ID), "result": result})
		s.q <- b
	}
	switch msg.Method {
	case "initialize":
		respond(map[string]any{
			"protocolVersion": "2024-11-05",
			"serverInfo":      map[string]any{"name": ServerName, "version": ServerVersion},
			"capabilities":    map[string]any{"tools": map[string]any{"listChanged": false}},
		})
	case "notifications/initialized":
	case "tools/list":
		respond(map[string]any{"tools": tools})
	case "tools/call":
		var p struct {
			Name      string   `json:"name"`
			Arguments toolArgs `json:"arguments"`
		}
		json.Unmarshal(msg.Params, &p)
		respond(handleTool(p.Name, p.Arguments, ip, sid))
	case "ping":
		respond(map[string]any{})
	default:
		if len(msg.ID) > 0 {
			b, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": json.RawMessage(msg.ID),
				"error": map[string]any{"code": -32601, "message": "Unknown: " + msg.Method}})
			s.q <- b
		}
	}
	w.WriteHeader(202)
}

func main() {
	port := os.Getenv("PORT")
	if port == "" {
		port = "8097"
	}
	host := os.Getenv("HOST")
	if host == "" {
		host = "0.0.0.0"
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprintf(w, `{"status":"ok","server":%q,"version":%q}`, ServerName, ServerVersion)
	})
	mux.HandleFunc("/sse", sseHandler)
	mux.HandleFunc("/messages", messagesHandler)
	log.Printf("[%s] listening on %s:%s", ServerName, host, port)
	log.Fatal(http.ListenAndServe(host+":"+port, mux))
}
