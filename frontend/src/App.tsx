import { useState, useRef, useEffect } from "react";

interface Message {
  role: "user" | "assistant";
  content: string;
}

const styles: Record<string, React.CSSProperties> = {
  page: {
    fontFamily: "system-ui, sans-serif",
    maxWidth: 720,
    margin: "0 auto",
    padding: 24,
    display: "flex",
    flexDirection: "column",
    height: "100vh",
    boxSizing: "border-box",
  },
  header: { marginBottom: 8 },
  sub: { color: "#666", fontSize: 13, marginBottom: 16 },
  log: {
    flex: 1,
    overflowY: "auto",
    border: "1px solid #e0e0e0",
    borderRadius: 8,
    padding: 16,
    background: "#fafafa",
  },
  row: { display: "flex", gap: 8, marginTop: 12 },
  input: { flex: 1, padding: "10px 12px", borderRadius: 8, border: "1px solid #ccc" },
  button: {
    padding: "10px 18px",
    borderRadius: 8,
    border: "none",
    background: "#ff3621",
    color: "white",
    cursor: "pointer",
  },
  bubbleUser: {
    alignSelf: "flex-end",
    background: "#ff3621",
    color: "white",
    padding: "8px 12px",
    borderRadius: 12,
    margin: "4px 0",
    maxWidth: "80%",
  },
  bubbleBot: {
    alignSelf: "flex-start",
    background: "#eee",
    color: "#111",
    padding: "8px 12px",
    borderRadius: 12,
    margin: "4px 0",
    maxWidth: "80%",
  },
};

export default function App() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [threadId, setThreadId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    logRef.current?.scrollTo(0, logRef.current.scrollHeight);
  }, [messages]);

  async function send() {
    const text = input.trim();
    if (!text || loading) return;
    setMessages((m) => [...m, { role: "user", content: text }]);
    setInput("");
    setLoading(true);
    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, thread_id: threadId }),
      });
      const data = await res.json();
      setThreadId(data.thread_id);
      setMessages((m) => [...m, { role: "assistant", content: data.reply }]);
    } catch {
      setMessages((m) => [...m, { role: "assistant", content: "Request failed." }]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={styles.page}>
      <h2 style={styles.header}>LangGraph Sample Agent</h2>
      <div style={styles.sub}>
        ReAct agent + calculator tool. Conversation state persists in Lakebase
        {threadId ? ` (thread ${threadId.slice(0, 8)})` : ""}.
      </div>
      <div style={styles.log} ref={logRef}>
        {messages.map((m, i) => (
          <div key={i} style={{ display: "flex" }}>
            <div style={m.role === "user" ? styles.bubbleUser : styles.bubbleBot}>
              {m.content}
            </div>
          </div>
        ))}
        {loading && <div style={styles.bubbleBot}>…</div>}
      </div>
      <div style={styles.row}>
        <input
          style={styles.input}
          value={input}
          placeholder="Ask something, e.g. what is 23 * 19?"
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
        />
        <button style={styles.button} onClick={send} disabled={loading}>
          Send
        </button>
      </div>
    </div>
  );
}
