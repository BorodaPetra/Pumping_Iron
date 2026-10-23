// Chat page: sends the conversation to POST /api/chat and renders the streamed reply.

const log = document.getElementById("chat-log");
const empty = document.getElementById("chat-empty");
const form = document.getElementById("chat-form");
const input = document.getElementById("chat-input");
const sendButton = document.getElementById("chat-send");

// Conversation history in the {role, content} shape the API expects; the server keeps none.
const history = [];

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

/** Send the user's text, then fill the assistant bubble with the reply as it streams in. */
async function send(text) {
  history.push({ role: "user", content: text });
  addBubble("user", text);
  const bubble = addBubble("assistant", "…");
  sendButton.disabled = true;

  let reply = "";
  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: history }),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      reply += decoder.decode(value, { stream: true });
      // PERF: append only the new chunk (bubble.append(chunk)) instead of rewriting the whole reply per token.
      bubble.textContent = reply;
      log.scrollTop = log.scrollHeight;
    }
    history.push({ role: "assistant", content: reply });
  } catch (error) {
    bubble.textContent = `[Не удалось получить ответ: ${error.message}]`;
    history.pop(); // drop the unanswered question so a retry does not duplicate it
  } finally {
    sendButton.disabled = false;
    input.focus();
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = input.value.trim();
  if (!text) return;
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
