// Chat page: sends the conversation, the chosen model and the ticked progress photos to POST /api/chat
// and renders the streamed reply.
// The reply is NDJSON: one {"type": "text" | "notice" | "error", "value": string} per line.
// The conversation is kept in sessionStorage, so it survives a visit to another page of the app.

const log = document.getElementById("chat-log");
const empty = document.getElementById("chat-empty");
const form = document.getElementById("chat-form");
const input = document.getElementById("chat-input");
const sendButton = document.getElementById("chat-send");

const providerInputs = document.querySelectorAll('input[name="provider"]');

/** The model chosen in the picker: "local" (LM Studio) or "claude" (Anthropic API). */
function currentProvider() {
  return document.querySelector('input[name="provider"]:checked').value;
}

// Remember the choice between visits; storage may be blocked, then the default (local) stays.
try {
  const saved = localStorage.getItem("trainer-provider");
  const input = [...providerInputs].find((i) => i.value === saved && !i.disabled);
  if (input) input.checked = true;
} catch (error) { /* storage unavailable */ }
providerInputs.forEach((input) => input.addEventListener("change", () => {
  try { localStorage.setItem("trainer-provider", currentProvider()); } catch (error) { /* storage unavailable */ }
}));

// Conversation history in the {role, content} shape the API expects; the server keeps none.
// Only the user's messages and the model's own text go in: server notices and errors never do.
const history = [];

// What is on screen, to redraw it after the page was left and opened again:
// {role: "user", text, photos: [{label}]} or {role: "assistant", parts: [{type, value}], pending}.
const view = [];

// True while a reply is streaming; blocks a second send (Enter bypasses the disabled button).
let sending = false;

const STORE_KEY = "trainer-chat";
const INTERRUPTED = "Ответ прервался: страница была закрыта до конца ответа. Отправь вопрос ещё раз.";

/** Save the history and the screen state for the next visit (a tab-bound store; may be blocked). */
function saveChat() {
  try { sessionStorage.setItem(STORE_KEY, JSON.stringify({ history, view })); } catch (error) { /* storage unavailable */ }
}

// Progress photos: ticked boxes in the picker are attached to the next message only.
const photoPick = document.getElementById("photo-pick");
const photoBoxes = photoPick ? [...photoPick.querySelectorAll("input[type=checkbox]")] : [];
const photoCount = document.getElementById("photo-count");

/** Keep at most data-max boxes ticked (the rest are disabled) and show how many are chosen. */
function updatePhotoPick() {
  if (!photoPick) return;
  const chosen = photoBoxes.filter((box) => box.checked).length;
  const max = Number(photoPick.dataset.max);
  photoBoxes.forEach((box) => { box.disabled = !box.checked && chosen >= max; });
  photoCount.textContent = chosen ? `(выбрано: ${chosen})` : "";
}
photoBoxes.forEach((box) => box.addEventListener("change", updatePhotoPick));

/** Return the ticked photos as [{id, label}] and untick them: they belong to one message. */
function takePhotos() {
  const taken = photoBoxes.filter((box) => box.checked).map((box) => ({ id: Number(box.value), label: box.dataset.label }));
  photoBoxes.forEach((box) => { box.checked = false; });
  updatePhotoPick();
  return taken;
}

/** Append a message bubble to the log and return its element (text is set via textContent, never HTML). */
function addBubble(role, text, photos = []) {
  empty.hidden = true;
  const bubble = document.createElement("div");
  bubble.className = `bubble bubble-${role}`;
  bubble.textContent = text;
  if (photos.length) {
    const note = document.createElement("div");
    note.className = "attach";
    note.textContent = `Фото: ${photos.map((p) => p.label).join("; ")}`;
    bubble.appendChild(note);
  }
  log.appendChild(bubble);
  log.scrollTop = log.scrollHeight;
  return bubble;
}

/** Draw one piece of an assistant reply: text goes into a span (consecutive text shares it), notices into a line. */
function drawPart(bubble, { type, value }) {
  if (type === "text") {
    let span = bubble.lastElementChild;
    if (!span || span.tagName !== "SPAN") {
      span = document.createElement("span");
      bubble.appendChild(span);
    }
    span.append(value);
  } else {
    const note = document.createElement("div");
    note.className = type === "error" ? "notice notice-error" : "notice";
    note.textContent = value;
    bubble.appendChild(note);
  }
}

/** Add a piece to the reply record (text merges with the previous text) and draw it. */
function addPart(record, bubble, part) {
  const last = record.parts[record.parts.length - 1];
  if (part.type === "text" && last && last.type === "text") last.value += part.value;
  else record.parts.push({ ...part });
  drawPart(bubble, part);
  log.scrollTop = log.scrollHeight;
}

// Redraw the conversation saved on the previous visit. A reply that was still streaming when the
// page was left is cut short: what arrived stays, a note says so, and its question leaves the history.
try {
  const saved = JSON.parse(sessionStorage.getItem(STORE_KEY));
  if (saved) {
    history.push(...saved.history);
    for (const item of saved.view) {
      view.push(item);
      if (item.role === "user") {
        addBubble("user", item.text, item.photos);
        continue;
      }
      const bubble = addBubble("assistant", "");
      item.parts.forEach((part) => drawPart(bubble, part));
      if (item.pending) {
        item.pending = false;
        addPart(item, bubble, { type: "error", value: INTERRUPTED });
        history.pop();
      }
    }
    saveChat();
  }
} catch (error) { /* nothing saved or storage unavailable */ }

// A reply may be streaming now: save the partial state as the page goes away. The aborted fetch then
// fails with "network error"; `leaving` keeps that from overwriting the saved state (see send).
let leaving = false;
window.addEventListener("pagehide", () => { leaving = true; saveChat(); });

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
  const photos = takePhotos();
  view.push({ role: "user", text, photos: photos.map(({ label }) => ({ label })) });
  addBubble("user", text, photos);
  const record = { role: "assistant", parts: [], pending: true };
  view.push(record);
  const bubble = addBubble("assistant", "…");
  sendButton.disabled = true;
  saveChat();

  let reply = ""; // the model's own text: this is what goes into the history
  let failed = false;
  /** Replace the "…" placeholder with the first real content. */
  const clearPlaceholder = () => { if (bubble.textContent === "…") bubble.textContent = ""; };

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: history, provider: currentProvider(), photo_ids: photos.map((p) => p.id) }),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);

    for await (const { type, value } of readEvents(response)) {
      clearPlaceholder();
      if (type === "text") reply += value;
      failed = failed || type === "error";
      addPart(record, bubble, { type, value });
    }
  } catch (error) {
    if (leaving) return; // the page is going away: the saved state is marked pending, the next visit explains
    clearPlaceholder();
    addPart(record, bubble, { type: "error", value: `Не удалось получить ответ: ${error.message}` });
    failed = true;
  } finally {
    if (leaving) return;
    if (reply.trim() && !failed) {
      history.push({ role: "assistant", content: reply });
    } else {
      history.pop(); // no usable answer: drop the question so a retry does not duplicate it
    }
    if (bubble.textContent === "…") {
      bubble.textContent = "";
      addPart(record, bubble, { type: "error", value: "Пустой ответ модели." });
    }
    record.pending = false;
    saveChat();
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
