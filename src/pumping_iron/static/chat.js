// Chat page: sends the conversation to POST /api/chat and renders the streamed reply.
// The reply is NDJSON: one {"type": "text" | "notice" | "error", "value": string} per line.

const log = document.getElementById("chat-log");
const empty = document.getElementById("chat-empty");
const form = document.getElementById("chat-form");
const input = document.getElementById("chat-input");
const sendButton = document.getElementById("chat-send");

// Conversation history in the {role, content} shape the API expects; the server keeps none.
// Only the user's messages and the model's own text go in: server notices and errors never do.
const history = [];

// True while a reply is streaming; blocks a second send (Enter bypasses the disabled button).
let sending = false;

/** Append a message bubble to the log and return its element (text is set via textContent, never HTML). */
function addBubble(role, text) {
  empty.hidden = true;
  const bubble = document.createElement("div");
  bubble.className = `bubble bubble-${role}`;
  bubble.textContent = text;
  log.appendChild(bubble);
  log.scrollTop = log.scrollHeight;
  return bubble;
}

/** Split a fetch response body into parsed NDJSON events, tolerating chunks that end mid-line. */
async function* readEvents(response) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop(); // the last piece may be an incomplete line
    for (const line of lines) {
      if (line.trim()) yield JSON.parse(line);
    }
  }
}

/** Send the user's text, then fill the assistant bubble with the reply as it streams in. */
async function send(text) {
  sending = true;
  history.push({ role: "user", content: text });
  addBubble("user", text);
  const bubble = addBubble("assistant", "…");
  sendButton.disabled = true;

  let reply = ""; // the model's own text: this is what goes into the history
  let textSpan = null; // the span currently receiving streamed text
  let failed = false;
  /** Replace the "…" placeholder with the first real content. */
  const clearPlaceholder = () => { if (bubble.textContent === "…") bubble.textContent = ""; };

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: history }),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);

    for await (const { type, value } of readEvents(response)) {
      clearPlaceholder();
      if (type === "text") {
        reply += value;
        if (!textSpan) {
          textSpan = document.createElement("span");
          bubble.appendChild(textSpan);
        }
        textSpan.append(value);
      } else {
        // A notice or an error ends the current text run; later text starts a new span.
        textSpan = null;
        const note = document.createElement("div");
        note.className = type === "error" ? "notice notice-error" : "notice";
        note.textContent = value;
        bubble.appendChild(note);
        failed = failed || type === "error";
      }
      log.scrollTop = log.scrollHeight;
    }
  } catch (error) {
    clearPlaceholder();
    bubble.textContent = `Не удалось получить ответ: ${error.message}`;
    failed = true;
  } finally {
    if (reply.trim() && !failed) {
      history.push({ role: "assistant", content: reply });
    } else {
      history.pop(); // no usable answer: drop the question so a retry does not duplicate it
    }
    if (bubble.textContent === "…") bubble.textContent = "Пустой ответ модели.";
    sending = false;
    sendButton.disabled = false;
    input.focus();
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = input.value.trim();
  if (!text || sending) return;
  input.value = "";
  send(text);
});

// Enter sends, Shift+Enter inserts a newline.
input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});
