#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
SIDEcar_PID=""
TAVILY_PID=""

cleanup() {
  if [[ -n "${SIDEcar_PID}" ]] && kill -0 "${SIDEcar_PID}" >/dev/null 2>&1; then
    kill "${SIDEcar_PID}" >/dev/null 2>&1 || true
    wait "${SIDEcar_PID}" 2>/dev/null || true
  fi
  if [[ -n "${TAVILY_PID}" ]] && kill -0 "${TAVILY_PID}" >/dev/null 2>&1; then
    kill "${TAVILY_PID}" >/dev/null 2>&1 || true
    wait "${TAVILY_PID}" 2>/dev/null || true
  fi
  rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

if [[ -f "${ROOT_DIR}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${ROOT_DIR}/.env"
  set +a
fi

if [[ -z "${BLUEPRINT_DEEPSEEK_API_KEY:-}" ]]; then
  echo "BLUEPRINT_DEEPSEEK_API_KEY is required for manager sidecar smoke." >&2
  exit 1
fi

wait_for_http() {
  local url="$1"
  for _ in $(seq 1 40); do
    if curl -fsS "${url}" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.25
  done
  echo "Timed out waiting for ${url}" >&2
  return 1
}

start_sidecar() {
  local port="$1"
  local web_enabled="$2"
  local tavily_key="$3"
  local tavily_base_url="$4"
  (
    cd "${ROOT_DIR}/manager-agent"
    MANAGER_AGENT_HOST=127.0.0.1 \
    MANAGER_AGENT_PORT="${port}" \
    MANAGER_AGENT_PROVIDER=deepseek \
    MANAGER_AGENT_MODEL=deepseek-v4-pro \
    MANAGER_AGENT_TIMEOUT_MS=600000 \
    BLUEPRINT_DEEPSEEK_API_KEY="${BLUEPRINT_DEEPSEEK_API_KEY}" \
    MANAGER_WEBSEARCH_ENABLED="${web_enabled}" \
    TAVILY_API_KEY="${tavily_key}" \
    TAVILY_BASE_URL="${tavily_base_url}" \
    node src/server.js >"${TMP_DIR}/sidecar_${port}.log" 2>&1
  ) &
  SIDEcar_PID="$!"
  wait_for_http "http://127.0.0.1:${port}/healthz"
}

stop_sidecar() {
  if [[ -n "${SIDEcar_PID}" ]] && kill -0 "${SIDEcar_PID}" >/dev/null 2>&1; then
    kill "${SIDEcar_PID}" >/dev/null 2>&1 || true
    wait "${SIDEcar_PID}" 2>/dev/null || true
  fi
  SIDEcar_PID=""
}

start_fake_tavily() {
  local port="$1"
  local log_path="$2"
  python3 - <<PY >"${TMP_DIR}/fake_tavily.log" 2>&1 &
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

log_path = ${log_path@Q}

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(f"{self.path}\\n{body}\\n")
        if self.path == "/search":
            payload = {
                "answer": "OpenAI released a fake feature for smoke testing.",
                "results": [
                    {
                        "title": "Fake OpenAI Update",
                        "url": "https://example.test/openai-update",
                        "content": "OpenAI released a fake feature for smoke testing on 2026-05-23.",
                    }
                ],
            }
        elif self.path == "/extract":
            payload = {
                "results": [
                    {
                        "url": "https://example.test/openai-update",
                        "raw_content": "OpenAI released a fake feature for smoke testing on 2026-05-23.",
                    }
                ]
            }
        else:
            self.send_response(404)
            self.end_headers()
            return
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_args):
        return

server = HTTPServer(("127.0.0.1", ${port}), Handler)
server.serve_forever()
PY
  TAVILY_PID="$!"
  sleep 0.5
}

run_stream_case() {
  local port="$1"
  local prompt="$2"
  python3 - <<PY
import json, urllib.request

req = urllib.request.Request(
    "http://127.0.0.1:${port}/chat-stream",
    data=json.dumps(
        {
            "project_id": "smoke",
            "message": ${prompt@Q},
            "context": {},
            "thinking_effort": "low",
            "messages": [],
            "session_messages": [],
            "backend_api_base_url": "http://127.0.0.1:18001/api",
            "internal_tool_token": "smoke",
        }
    ).encode("utf-8"),
    headers={"content-type": "application/json"},
)
with urllib.request.urlopen(req, timeout=180) as resp:
    text = resp.read().decode("utf-8")
events = []
for line in text.splitlines():
    if line.startswith("data:"):
        payload = line[5:].strip()
        if payload:
            events.append(json.loads(payload))
web_events = [event for event in events if event.get("type") in {"tool_start", "tool_end"} and event.get("tool_name") in {"web_search", "web_extract"}]
response = next((event for event in events if event.get("type") == "response"), {})
print(json.dumps({
    "event_count": len(events),
    "web_tool_events": len(web_events),
    "message_preview": ((response.get("response") or {}).get("message") or "")[:240],
}, ensure_ascii=False))
PY
}

run_compact_case() {
  local port="$1"
  python3 - <<PY
import json, urllib.request

messages = []
for index in range(16):
    blob = ("RNA-seq planning and results summary " + str(index) + " ") * 160
    messages.append(
        {
            "id": f"user_{index}",
            "role": "user",
            "content": blob,
            "proposal": None,
            "thinking": None,
            "attachments": [],
            "state": "done",
            "timeline": [{"id": f"user_{index}_text", "kind": "text", "content": blob, "label": None, "tool_name": None, "status": "done", "started_at": None, "ended_at": None}],
            "token_usage": None,
        }
    )
    messages.append(
        {
            "id": f"assistant_{index}",
            "role": "manager",
            "content": blob,
            "proposal": None,
            "thinking": None,
            "attachments": [],
            "state": "done",
            "timeline": [{"id": f"assistant_{index}_text", "kind": "text", "content": blob, "label": None, "tool_name": None, "status": "done", "started_at": None, "ended_at": None}],
            "token_usage": None,
        }
    )

req = urllib.request.Request(
    "http://127.0.0.1:${port}/compact",
    data=json.dumps(
        {
            "project_id": "smoke",
            "message": "/compact",
            "context": {},
            "thinking_effort": "low",
            "messages": [],
            "session_messages": messages,
            "backend_api_base_url": "http://127.0.0.1:18001/api",
            "internal_tool_token": "smoke",
        }
    ).encode("utf-8"),
    headers={"content-type": "application/json"},
)
result = json.load(urllib.request.urlopen(req, timeout=600))
print(json.dumps({
    "compact_id": result.get("compact_id"),
    "summary_chars": len(result.get("summary") or ""),
    "tokens_before": result.get("tokens_before"),
    "tokens_after": result.get("tokens_after"),
    "provider": result.get("provider"),
    "model": result.get("model"),
}, ensure_ascii=False))
PY
}

echo "[1/3] compact smoke"
start_sidecar 18022 false "" "https://api.tavily.com"
run_compact_case 18022
stop_sidecar

echo "[2/3] websearch disabled smoke"
start_sidecar 18023 false "" "https://api.tavily.com"
run_stream_case 18023 "请给我一个最近新闻类问题的简短回答：OpenAI 最近有什么动态？如果不能联网就直接说明并基于已有知识回答。"
stop_sidecar

echo "[3/3] websearch enabled smoke"
FAKE_TAVILY_LOG="${TMP_DIR}/fake_tavily_requests.log"
start_fake_tavily 18123 "${FAKE_TAVILY_LOG}"
start_sidecar 18024 true "smoke-key" "http://127.0.0.1:18123"
run_stream_case 18024 "请查询 OpenAI 今天的最新动态，并基于搜索结果简短总结。"
grep -q "/search" "${FAKE_TAVILY_LOG}"
stop_sidecar

echo "manager sidecar smoke passed"
