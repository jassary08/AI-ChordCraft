const queryForm = document.getElementById("query-form");
const keyInput = document.getElementById("key-input");
const progressionInput = document.getElementById("progression-input");
const limitInput = document.getElementById("limit-input");
const candidateBoard = document.getElementById("candidate-board");
const workspaceTitle = document.getElementById("workspace-title");
const summaryStrip = document.getElementById("summary-strip");
const draftCount = document.getElementById("draft-count");
const statusLine = document.getElementById("status-line");
const saveButton = document.getElementById("save-button");
const exportButton = document.getElementById("export-button");
const candidateTemplate = document.getElementById("candidate-template");

const API_BASE = (() => {
  const elementBase = String(document.body?.dataset?.apiBase || "").trim();
  if (elementBase) return elementBase.replace(/\/+$/, "");
  return "";
})();

const COMMONNESS = [
  { value: 5, label: "首选", status: "preferred" },
  { value: 4, label: "常用", status: "common" },
  { value: 3, label: "可用", status: "usable" },
  { value: 2, label: "少用", status: "rare" },
  { value: 1, label: "不用", status: "rejected" },
];

const TAGS = [
  { group: "styles", value: "pop", label: "Pop" },
  { group: "styles", value: "rock", label: "Rock" },
  { group: "styles", value: "blues", label: "Blues" },
  { group: "styles", value: "rnb", label: "R&B" },
  { group: "styles", value: "funk", label: "Funk" },
  { group: "contexts", value: "beginner", label: "初学" },
  { group: "contexts", value: "open", label: "开放弦" },
  { group: "contexts", value: "barre", label: "横按" },
  { group: "contexts", value: "campfire", label: "弹唱" },
  { group: "contexts", value: "movable", label: "可移动" },
];

let lastPayload = null;
let draft = new Map();

function apiUrl(path) {
  const normalized = path.replace(/^\/+/, "");
  if (API_BASE === ".") return normalized;
  return API_BASE ? `${API_BASE}/${normalized}` : normalized;
}

function escapeHtml(value, fallback = "-") {
  if (value === null || value === undefined) return fallback;
  const text = String(value).trim();
  if (!text) return fallback;
  return text
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function setStatus(text, type = "ready") {
  statusLine.textContent = text;
  statusLine.dataset.type = type;
}

function shapeFromFrets(frets) {
  return frets.map((fret) => (fret < 0 ? "x" : String(fret))).join("");
}

function annotationKey(item) {
  return `${item.symbol}|${item.frets.slice(0, 6).join(",")}`;
}

function defaultAnnotation(item, commonness = 3) {
  const rating = COMMONNESS.find((entry) => entry.value === commonness) || COMMONNESS[2];
  return {
    symbol: item.symbol,
    shape: item.shape || shapeFromFrets(item.frets),
    frets: item.frets,
    commonness: rating.value,
    status: rating.status,
    styles: [],
    contexts: [],
    canonical_rank: null,
    notes: "",
  };
}

function normalizeAnnotation(item, existing) {
  if (!existing) return null;
  return {
    ...defaultAnnotation(item, Number(existing.commonness || 3)),
    ...existing,
    symbol: item.symbol,
    shape: existing.shape || item.shape || shapeFromFrets(item.frets),
    frets: item.frets,
    styles: Array.isArray(existing.styles) ? existing.styles : [],
    contexts: Array.isArray(existing.contexts) ? existing.contexts : [],
    notes: existing.notes || "",
  };
}

function renderFretDiagram(item) {
  const frets = item.frets || [];
  const fingers = item.fingers || [];
  const positive = frets.filter((fret) => fret > 0);
  let base = Number(item.position || 1);
  if (base <= 1 && positive.length && Math.max(...positive) > 5) {
    base = Math.min(...positive);
  }
  const stringXs = [18, 41, 64, 87, 110, 133];
  const fretYs = [34, 58, 82, 106, 130, 154];
  const dotY = (fret) => 34 + (fret - base + 0.5) * 24;
  const nutWidth = base === 1 ? 5 : 2;
  const stringLines = stringXs.map((x) => `<line x1="${x}" y1="34" x2="${x}" y2="154" />`).join("");
  const fretLines = fretYs
    .map((y, index) => `<line class="${index === 0 ? "nut" : ""}" x1="18" y1="${y}" x2="133" y2="${y}" stroke-width="${index === 0 ? nutWidth : 2}" />`)
    .join("");
  const markers = frets
    .map((fret, index) => {
      const x = stringXs[index];
      if (fret < 0) return `<text x="${x}" y="20" text-anchor="middle">x</text>`;
      if (fret === 0) return `<text x="${x}" y="20" text-anchor="middle">o</text>`;
      if (fret < base || fret >= base + 5) return "";
      const finger = fingers[index] || "";
      return `<g><circle cx="${x}" cy="${dotY(fret)}" r="8" /><text class="finger" x="${x}" y="${dotY(fret) + 4}" text-anchor="middle">${finger}</text></g>`;
    })
    .join("");
  const barreMarks = (item.barres || [])
    .map((fret) => {
      if (fret < base || fret >= base + 5) return "";
      const strings = frets.map((value, index) => (value === fret ? index : -1)).filter((index) => index >= 0);
      if (strings.length < 2) return "";
      const x1 = stringXs[Math.min(...strings)];
      const x2 = stringXs[Math.max(...strings)];
      const y = dotY(fret);
      return `<line class="barre" x1="${x1}" y1="${y}" x2="${x2}" y2="${y}" />`;
    })
    .join("");
  const baseLabel = base > 1 ? `<text class="base-fret" x="2" y="46">${base}fr</text>` : "";
  return `
    <svg viewBox="0 0 154 166" role="img" aria-label="${escapeHtml(item.symbol)} ${escapeHtml(item.shape)}">
      <style>
        line { stroke: #40546f; stroke-width: 2; stroke-linecap: round; }
        text { fill: #63708a; font: 700 12px Avenir Next, sans-serif; }
        circle { fill: #1f6feb; }
        .finger { fill: #fff; font-size: 9px; }
        .barre { stroke: #0b4fb8; stroke-width: 12; }
        .base-fret { font-size: 10px; }
      </style>
      ${stringLines}
      ${fretLines}
      ${baseLabel}
      ${barreMarks}
      ${markers}
    </svg>
  `;
}

function ratingLabel(annotation) {
  if (!annotation) return "未标注";
  const item = COMMONNESS.find((entry) => entry.value === Number(annotation.commonness));
  return item ? item.label : "已标注";
}

function getAnnotation(item) {
  const key = annotationKey(item);
  if (draft.has(key)) return draft.get(key);
  return normalizeAnnotation(item, item.annotation);
}

function editableAnnotation(item, commonness = 3) {
  const key = annotationKey(item);
  return draft.get(key) || normalizeAnnotation(item, item.annotation) || defaultAnnotation(item, commonness);
}

function updateDraftCount() {
  draftCount.textContent = String(draft.size);
  saveButton.disabled = draft.size === 0;
  exportButton.disabled = draft.size === 0;
}

function setCardState(card, annotation) {
  card.classList.toggle("is-preferred", annotation?.status === "preferred");
  card.classList.toggle("is-rejected", annotation?.status === "rejected");
  const badge = card.querySelector(".annotation-badge");
  badge.textContent = ratingLabel(annotation);
  badge.dataset.status = annotation?.status || "";
  card.querySelectorAll(".rating-row button").forEach((button) => {
    button.classList.toggle("is-active", Number(button.dataset.value) === Number(annotation?.commonness));
  });
  card.querySelectorAll(".tag-chip").forEach((chip) => {
    const group = chip.dataset.group;
    const value = chip.dataset.value;
    chip.classList.toggle("is-active", Boolean(annotation?.[group]?.includes(value)));
  });
  const noteInput = card.querySelector(".note-input");
  if (document.activeElement !== noteInput) {
    noteInput.value = annotation?.notes || "";
  }
}

function renderCandidate(item) {
  const node = candidateTemplate.content.firstElementChild.cloneNode(true);
  const annotation = getAnnotation(item);
  const shape = item.shape || shapeFromFrets(item.frets);
  node.querySelector(".voicing-name").textContent = `${item.symbol} · ${shape}`;
  node.querySelector(".voicing-meta").textContent = `难度 ${item.difficulty || "-"} · ${item.tags?.join(" / ") || "未标记"}`;
  node.querySelector(".diagram-host").innerHTML = renderFretDiagram({ ...item, shape });

  const ratingRow = node.querySelector(".rating-row");
  COMMONNESS.forEach((rating) => {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.value = String(rating.value);
    button.textContent = rating.label;
    button.addEventListener("click", () => {
      const key = annotationKey(item);
      const current = editableAnnotation(item, rating.value);
      draft.set(key, { ...current, commonness: rating.value, status: rating.status });
      setCardState(node, draft.get(key));
      updateDraftCount();
    });
    ratingRow.appendChild(button);
  });

  const tagGrid = node.querySelector(".tag-grid");
  TAGS.forEach((tag) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "tag-chip";
    chip.dataset.group = tag.group;
    chip.dataset.value = tag.value;
    chip.textContent = tag.label;
    chip.addEventListener("click", () => {
      const key = annotationKey(item);
      const current = editableAnnotation(item);
      const values = new Set(current[tag.group] || []);
      if (values.has(tag.value)) values.delete(tag.value);
      else values.add(tag.value);
      draft.set(key, { ...current, [tag.group]: Array.from(values) });
      setCardState(node, draft.get(key));
      updateDraftCount();
    });
    tagGrid.appendChild(chip);
  });

  const noteInput = node.querySelector(".note-input");
  noteInput.addEventListener("input", () => {
    const key = annotationKey(item);
    const current = editableAnnotation(item);
    draft.set(key, { ...current, notes: noteInput.value });
    updateDraftCount();
  });

  setCardState(node, annotation);
  return node;
}

function renderSummary(payload) {
  const chordCount = payload.chords.length;
  const candidateCount = Object.values(payload.candidates).reduce((sum, list) => sum + list.length, 0);
  const annotatedCount = Object.values(payload.candidates)
    .flat()
    .filter((item) => item.annotation).length;
  summaryStrip.innerHTML = [
    ["和弦数量", chordCount],
    ["候选指法", candidateCount],
    ["库内已标注", annotatedCount],
    ["全库标注", payload.annotation_count],
  ]
    .map(([label, value]) => `<div class="summary-item"><span>${label}</span><strong>${value}</strong></div>`)
    .join("");
}

function renderCandidates(payload) {
  lastPayload = payload;
  workspaceTitle.textContent = payload.progression;
  renderSummary(payload);
  candidateBoard.innerHTML = "";
  if (!payload.chords.length) {
    candidateBoard.innerHTML = `<div class="empty-state"><strong>没有解析到和弦</strong><p>请用空格、逗号或竖线分隔和弦。</p></div>`;
    return;
  }
  payload.chords.forEach((chord) => {
    const section = document.createElement("section");
    section.className = "chord-section";
    const candidates = payload.candidates[chord] || [];
    section.innerHTML = `
      <div class="chord-heading">
        <h3>${escapeHtml(chord)}</h3>
        <span>${candidates.length} 个候选</span>
      </div>
      <div class="card-grid"></div>
    `;
    const grid = section.querySelector(".card-grid");
    if (!candidates.length) {
      grid.innerHTML = `<div class="empty-state"><strong>${escapeHtml(chord)} 暂无候选</strong><p>可以后续补充到 voicing db。</p></div>`;
    } else {
      candidates.forEach((item) => grid.appendChild(renderCandidate(item)));
    }
    candidateBoard.appendChild(section);
  });
  updateDraftCount();
}

async function loadCandidates() {
  const progression = progressionInput.value.trim();
  const limit = Number(limitInput.value || 24);
  if (!progression) {
    setStatus("请先输入和弦进行。", "error");
    return;
  }
  setStatus("正在载入候选指法...", "busy");
  const params = new URLSearchParams({ progression, limit: String(limit) });
  const response = await fetch(apiUrl(`/api/voicing-candidates?${params.toString()}`));
  if (!response.ok) {
    throw new Error(`候选载入失败：HTTP ${response.status}`);
  }
  const payload = await response.json();
  renderCandidates(payload);
  setStatus(`已载入 ${payload.chords.length} 个和弦。`);
}

function currentAnnotations() {
  return Array.from(draft.values()).map((item) => ({
    ...item,
    shape: item.shape || shapeFromFrets(item.frets),
  }));
}

async function saveAnnotations() {
  const annotations = currentAnnotations();
  if (!annotations.length) return;
  setStatus("正在保存标注...", "busy");
  const response = await fetch(apiUrl("/api/voicing-annotations"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      progression: progressionInput.value.trim(),
      key: keyInput.value.trim(),
      annotations,
    }),
  });
  if (!response.ok) {
    throw new Error(`保存失败：HTTP ${response.status}`);
  }
  const payload = await response.json();
  setStatus(`已保存 ${payload.saved} 条标注。`);
  await loadCandidates();
}

function exportAnnotations() {
  const payload = {
    key: keyInput.value.trim(),
    progression: progressionInput.value.trim(),
    annotations: currentAnnotations(),
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "voicing-annotations-draft.json";
  link.click();
  URL.revokeObjectURL(url);
}

queryForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await loadCandidates();
  } catch (error) {
    setStatus(error.message || String(error), "error");
  }
});

saveButton.addEventListener("click", async () => {
  try {
    await saveAnnotations();
  } catch (error) {
    setStatus(error.message || String(error), "error");
  }
});

exportButton.addEventListener("click", exportAnnotations);

loadCandidates().catch((error) => setStatus(error.message || String(error), "error"));
