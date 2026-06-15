const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const STORAGE_KEY = "guicha_conversations_v1";

let conversations = loadConversations();
let currentConversationId = null;

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
  })[char]);
}

function inlineMarkup(value) {
  return escapeHtml(value)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\[(\d+)\]/g, "<sup>[$1]</sup>");
}

function tableCells(line) {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map(cell => cell.trim());
}

function isTableSeparator(line) {
  return /^\|?\s*:?-{3,}/.test(line) && line.includes("|");
}

function renderTable(lines) {
  const rows = lines.filter(line => !isTableSeparator(line)).map(tableCells);
  if (rows.length < 2) return "";
  const width = Math.max(...rows.map(row => row.length));
  const normalized = rows.map(row => [...row, ...Array(width - row.length).fill("")]);
  const head = normalized[0].map(cell => `<th>${inlineMarkup(cell)}</th>`).join("");
  const body = normalized.slice(1).map(row =>
    `<tr>${row.map(cell => `<td>${inlineMarkup(cell)}</td>`).join("")}</tr>`
  ).join("");
  return `<div class="answer-table-wrap"><table class="answer-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function answerMarkup(text) {
  const lines = String(text || "").split("\n").map(line => line.trim()).filter(Boolean);
  let notice = "内容仅供辅助参考，请以主管部门正式文件为准。";
  if (lines.length && /不替代|正式解释|审批意见/.test(lines.at(-1))) notice = lines.pop();
  const parts = [];
  for (let index = 0; index < lines.length;) {
    const line = lines[index];
    if (line.startsWith("|") && line.includes("|")) {
      const tableLines = [];
      while (index < lines.length && lines[index].startsWith("|") && lines[index].includes("|")) {
        tableLines.push(lines[index]);
        index += 1;
      }
      parts.push(renderTable(tableLines));
      continue;
    }
    if (/^[-*]\s+/.test(line)) {
      const items = [];
      while (index < lines.length && /^[-*]\s+/.test(lines[index])) {
        items.push(`<li>${inlineMarkup(lines[index].replace(/^[-*]\s+/, ""))}</li>`);
        index += 1;
      }
      parts.push(`<ul>${items.join("")}</ul>`);
      continue;
    }
    if (/^#{1,4}\s+/.test(line)) {
      parts.push(`<h3>${inlineMarkup(line.replace(/^#{1,4}\s+/, ""))}</h3>`);
    } else {
      parts.push(`<p>${inlineMarkup(line)}</p>`);
    }
    index += 1;
  }
  return `${parts.join("")}<div class="notice"><p>${escapeHtml(notice)}</p></div>`;
}

function evidenceType(metadata) {
  return ({table: "表格", figure: "图示", body: "正文"})[metadata.content_type] || "法规";
}

function cleanExcerpt(item) {
  const metadata = item.metadata || {};
  if (metadata.content_type === "table") return "已定位到相关数据表，请结合表格原文核验具体数值与适用条件。";
  if (metadata.content_type === "figure") return "已定位到相关技术图示，请结合图示原文核验具体关系与适用条件。";
  return String(item.content || "")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/\|[- :|]+\|/g, " ")
    .replace(/\|/g, " ")
    .replace(/\btable_id\s*=\s*[\w-]+/gi, "")
    .replace(/\b[0-9a-f]{12,}-p\d{4}(?:-[tf]\d+)?\b/gi, "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 150);
}

function sourceHeading(item) {
  const title = String(item.title || "");
  const section = String(item.section || "");
  return title.includes(section) ? `《${title}》` : `《${title}》${section}`;
}

function loadConversations() {
  try {
    const value = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
    return Array.isArray(value) ? value : [];
  } catch {
    return [];
  }
}

function saveConversations() {
  conversations = conversations.slice(0, 30);
  localStorage.setItem(STORAGE_KEY, JSON.stringify(conversations));
  renderHistory();
}

function newId() {
  return globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function currentConversation() {
  return conversations.find(item => item.id === currentConversationId);
}

function createConversation(firstQuery) {
  const conversation = {
    id: newId(),
    title: firstQuery.length > 22 ? `${firstQuery.slice(0, 22)}…` : firstQuery,
    updatedAt: Date.now(),
    messages: []
  };
  conversations.unshift(conversation);
  currentConversationId = conversation.id;
  return conversation;
}

function renderHistory() {
  $("#historyList").innerHTML = conversations.length ? conversations.map(item => `
    <div class="history-row">
      <button class="history-item ${item.id === currentConversationId ? "active" : ""}" data-history-id="${escapeHtml(item.id)}">${escapeHtml(item.title)}</button>
      <button class="history-delete" data-delete-id="${escapeHtml(item.id)}" aria-label="删除历史记录">×</button>
    </div>
  `).join("") : '<div class="history-empty">暂无历史查询</div>';
}

function renderConversation() {
  const conversation = currentConversation();
  $("#emptyState").classList.toggle("hidden", Boolean(conversation?.messages.length));
  $("#conversationTitle").textContent = conversation?.title || "规划法规助手";
  $("#messageList").innerHTML = conversation?.messages.map((message, index) => {
    if (message.role === "user") {
      return `<div class="message user-message"><div class="user-bubble">${escapeHtml(message.content)}</div></div>`;
    }
    return `
      <div class="message assistant-message">
        <span class="assistant-avatar">规</span>
        <div class="assistant-body">
          ${answerMarkup(message.content)}
          <div class="message-tools">
            <button data-copy-message="${index}">复制回答</button>
            ${message.results?.length ? `<button data-evidence-message="${index}">查看 ${message.results.length} 条依据</button>` : ""}
          </div>
        </div>
      </div>
    `;
  }).join("") || "";
  scrollConversation();
}

function renderSources(results = []) {
  $("#evidenceCount").textContent = results.length;
  $("#resultCount").textContent = `${results.length} 条相关依据`;
  $("#evidenceButton").classList.toggle("hidden", !results.length);
  $("#retrievalResults").innerHTML = results.length ? results.map((item, index) => {
    const metadata = item.metadata || {};
    const excerpt = cleanExcerpt(item);
    const sourceLink = metadata.source_url
      ? `<a href="${escapeHtml(metadata.source_url)}" target="_blank" rel="noreferrer">查看原文</a>`
      : "";
    return `
      <article class="source-item">
        ${index === 0 ? '<span class="source-type">主要依据</span>' : ""}
        <strong>${escapeHtml(sourceHeading(item))}</strong>
        <p>${escapeHtml(excerpt)}${excerpt.length >= 150 ? "…" : ""}</p>
        <footer>
          <span>${escapeHtml(evidenceType(metadata))}</span>
          ${metadata.status ? `<span>${escapeHtml(metadata.status)}</span>` : ""}
          ${metadata.page ? `<span>第 ${escapeHtml(metadata.page)} 页</span>` : ""}
          ${sourceLink}
        </footer>
      </article>
    `;
  }).join("") : '<div class="source-empty">当前回答没有可展示的法规依据。</div>';
}

function openEvidence(results) {
  renderSources(results);
  $("#chatWorkspace").classList.add("evidence-open");
}

function closeEvidence() {
  $("#chatWorkspace").classList.remove("evidence-open");
}

function renderLoading() {
  $("#messageList").insertAdjacentHTML("beforeend", `
    <div id="loadingMessage" class="message assistant-message">
      <span class="assistant-avatar">规</span>
      <div class="loading-dots"><i></i><i></i><i></i></div>
    </div>
  `);
  scrollConversation();
}

function scrollConversation() {
  requestAnimationFrame(() => {
    $("#conversation").scrollTop = $("#conversation").scrollHeight;
  });
}

function autoResize() {
  const input = $("#question");
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 150)}px`;
}

async function ask(queryOverride = "") {
  const input = $("#question");
  const query = (queryOverride || input.value).trim();
  if (query.length < 2 || $("#askButton").disabled) return;
  const conversation = currentConversation() || createConversation(query);
  conversation.messages.push({role: "user", content: query});
  conversation.updatedAt = Date.now();
  input.value = "";
  autoResize();
  $("#askButton").disabled = true;
  $("#emptyState").classList.add("hidden");
  renderConversation();
  renderLoading();
  saveConversations();

  try {
    const response = await fetch("/ask", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({query, top_k: 3, jurisdiction: "北京市"})
    });
    if (!response.ok) throw new Error("request failed");
    const data = await response.json();
    conversation.messages.push({role: "assistant", content: data.answer, results: data.results});
    conversation.updatedAt = Date.now();
    conversations = [conversation, ...conversations.filter(item => item.id !== conversation.id)];
    saveConversations();
    renderConversation();
    if (data.results.length) openEvidence(data.results);
  } catch {
    conversation.messages.push({role: "assistant", content: "查询暂时失败，请稍后重试。", results: []});
    saveConversations();
    renderConversation();
  } finally {
    $("#loadingMessage")?.remove();
    $("#askButton").disabled = false;
    input.focus();
  }
}

function startNewChat() {
  currentConversationId = null;
  closeEvidence();
  renderSources([]);
  renderConversation();
  renderHistory();
  $("#question").value = "";
  $("#question").focus();
  closeSidebar();
}

function restoreConversation(id) {
  currentConversationId = id;
  closeEvidence();
  renderConversation();
  renderHistory();
  const conversation = currentConversation();
  const latest = [...(conversation?.messages || [])].reverse().find(item => item.role === "assistant" && item.results?.length);
  renderSources(latest?.results || []);
  closeSidebar();
}

function deleteConversation(id) {
  if (!window.confirm("删除这条历史记录？")) return;
  conversations = conversations.filter(item => item.id !== id);
  if (currentConversationId === id) currentConversationId = null;
  saveConversations();
  renderConversation();
  renderSources([]);
  closeEvidence();
}

function openSidebar() {
  document.body.classList.add("sidebar-open");
}

function closeSidebar() {
  document.body.classList.remove("sidebar-open");
}

function showToast() {
  $("#toast").classList.add("show");
  setTimeout(() => $("#toast").classList.remove("show"), 1500);
}

$("#newChatButton").addEventListener("click", startNewChat);
$("#askButton").addEventListener("click", () => ask());
$("#question").addEventListener("input", autoResize);
$("#question").addEventListener("keydown", event => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    ask();
  }
});
$$(".suggestion").forEach(button => button.addEventListener("click", () => ask(button.querySelector("strong").textContent)));
$("#historyList").addEventListener("click", event => {
  const historyButton = event.target.closest("[data-history-id]");
  const deleteButton = event.target.closest("[data-delete-id]");
  if (historyButton) restoreConversation(historyButton.dataset.historyId);
  if (deleteButton) deleteConversation(deleteButton.dataset.deleteId);
});
$("#messageList").addEventListener("click", async event => {
  const copyButton = event.target.closest("[data-copy-message]");
  const evidenceMessage = event.target.closest("[data-evidence-message]");
  const conversation = currentConversation();
  if (copyButton && conversation) {
    await navigator.clipboard.writeText(conversation.messages[Number(copyButton.dataset.copyMessage)].content);
    showToast();
  }
  if (evidenceMessage && conversation) {
    openEvidence(conversation.messages[Number(evidenceMessage.dataset.evidenceMessage)].results || []);
  }
});
$("#evidenceButton").addEventListener("click", () => {
  $("#chatWorkspace").classList.toggle("evidence-open");
});
$("#closeEvidenceButton").addEventListener("click", closeEvidence);
$("#openSidebarButton").addEventListener("click", openSidebar);
$("#closeSidebarButton").addEventListener("click", closeSidebar);
$("#sidebarBackdrop").addEventListener("click", closeSidebar);

renderHistory();
renderConversation();
renderSources([]);
autoResize();

const sharedQuery = new URLSearchParams(window.location.search).get("q");
if (sharedQuery) ask(sharedQuery);
