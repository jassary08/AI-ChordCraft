const form = document.getElementById("analyze-form");
const fileInput = document.getElementById("media-file");
const filenameEl = document.getElementById("filename");
const waveform = document.getElementById("waveform");
const statusEl = document.getElementById("status");
const submitButton = document.getElementById("submit");
const chartView = document.getElementById("chart-view");
const processingTemplate = document.getElementById("processing-template");
const chatForm = document.getElementById("chat-form");
const chatFeed = document.getElementById("chat-feed");
const chatSubmit = document.getElementById("chat-submit");
const chatMessageInput = document.getElementById("chat-message");
const selectionContext = document.getElementById("selection-context");
const selectionCount = document.getElementById("selection-count");
const selectionClear = document.getElementById("selection-clear");

let lastResult = null;
let lastMediaBase64 = null;
let chatMessages = [];
let mediaObjectUrl = null;
let activePlayback = null;
let selectedSegments = new Map();
let availableSegments = new Map();

const audioPlayer = new Audio();

const API_BASE = (() => {
  if (typeof window === "undefined") return "";
  const elementBase = String(document.body?.dataset?.apiBase || "").trim();
  if (elementBase) return elementBase.replace(/\/+$/, "");
  const configured = String(window.CHORDCRAFT_API_BASE || window.MOSS_MUSIC_API_BASE || "").trim();
  if (configured) return configured.replace(/\/+$/, "");
  return "";
})();

const ANALYSIS_DEFAULTS = {
  backend: "sglang",
  base_url: null,
  thinking_base_url: null,
  instruct_base_url: null,
  model_path: "",
  max_new_tokens: 4096,
  temperature: 0,
  workflow: true,
  max_sections: 12,
  chord_engine: "plkd-btc",
  structure_engine: "songformer",
};

function apiUrl(path) {
  const normalizedPath = path.replace(/^\/+/, "");
  if (API_BASE === ".") return normalizedPath;
  return API_BASE ? `${API_BASE}/${normalizedPath}` : normalizedPath;
}

async function readJsonResponse(response, path) {
  const contentType = response.headers.get("content-type") || "";
  const text = await response.text();
  if (contentType.includes("application/json")) {
    try {
      return text ? JSON.parse(text) : {};
    } catch (error) {
      throw new Error(`接口 ${apiUrl(path)} 返回了无效 JSON：${error.message}`);
    }
  }
  const preview = text.replace(/\s+/g, " ").slice(0, 220);
  throw new Error(`接口 ${apiUrl(path)} 返回了非 JSON 响应，HTTP ${response.status}：${preview}`);
}

function networkErrorMessage(error, path) {
  const message = error?.message || String(error);
  if (message === "Failed to fetch" || message.includes("NetworkError")) {
    return `无法连接后端服务 ${apiUrl(path)}。请确认 Web 服务正在运行，且当前页面是从该服务打开的。`;
  }
  return message;
}

const CHAT_DEFAULTS = {
  instruct_base_url: null,
  max_new_tokens: 2048,
  temperature: 0.2,
  top_p: 0.9,
  top_k: 50,
};

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

function setStatus(text, state = "ready") {
  statusEl.textContent = text;
  statusEl.classList.toggle("is-busy", state === "busy");
  statusEl.classList.toggle("is-error", state === "error");
}

function fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

function parseTimestamp(value) {
  if (typeof value === "number" && Number.isFinite(value)) return Math.max(0, Math.round(value));
  if (typeof value !== "string") return null;
  const text = value.trim().toLowerCase();
  let match = text.match(/^(\d{1,3}):(\d{2})(?:\.\d+)?$/);
  if (match) return Number(match[1]) * 60 + Number(match[2]);
  match = text.match(/^(\d+(?:\.\d+)?)\s*(?:s|sec|secs|second|seconds|秒)?$/);
  if (match) return Math.max(0, Math.round(Number(match[1])));
  match = text.match(/^(\d+(?:\.\d+)?)\s*(?:m|min|mins|minute|minutes|分)\s*(?:(\d+(?:\.\d+)?)\s*(?:s|sec|secs|second|seconds|秒)?)?$/);
  if (match) return Math.max(0, Math.round(Number(match[1]) * 60 + Number(match[2] || 0)));
  return null;
}

function parseTimestampPrecise(value) {
  if (typeof value === "number" && Number.isFinite(value)) return Math.max(0, value);
  if (typeof value !== "string") return null;
  const text = value.trim().toLowerCase();
  let match = text.match(/^(\d{1,3}):(\d{2})(?:\.(\d+))?$/);
  if (match) {
    const seconds = Number(match[2]);
    if (seconds >= 60) return null;
    const fraction = match[3] ? Number(`0.${match[3]}`) : 0;
    return Number(match[1]) * 60 + seconds + fraction;
  }
  match = text.match(/^(\d+(?:\.\d+)?)\s*(?:s|sec|secs|second|seconds|秒)?$/);
  if (match) return Math.max(0, Number(match[1]));
  return null;
}

function formatTimestamp(value, fallback = "?") {
  const seconds = parseTimestamp(value);
  if (seconds === null) return escapeHtml(value, fallback);
  const minutes = Math.floor(seconds / 60);
  const rest = String(seconds % 60).padStart(2, "0");
  return `${String(minutes).padStart(2, "0")}:${rest}`;
}

function formatPlaybackTime(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "00:00";
  const seconds = Math.max(0, Math.floor(value));
  const minutes = Math.floor(seconds / 60);
  const rest = String(seconds % 60).padStart(2, "0");
  return `${String(minutes).padStart(2, "0")}:${rest}`;
}

function finiteNumber(value) {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() && Number.isFinite(Number(value))) return Number(value);
  return null;
}

function formatLyricTimestamp(value, fallback = "?") {
  if (typeof value === "string") {
    const text = value.trim();
    const match = text.match(/^(\d{1,3}):(\d{2})(\.\d+)?$/);
    if (match) {
      return `${String(Number(match[1])).padStart(2, "0")}:${match[2]}${match[3] || ""}`;
    }
  }
  return formatTimestamp(value, fallback);
}

function drawEmptyWaveform() {
  const ctx = waveform.getContext("2d");
  const width = waveform.width;
  const height = waveform.height;
  ctx.clearRect(0, 0, width, height);
  const gradient = ctx.createLinearGradient(0, 0, width, 0);
  gradient.addColorStop(0, "#5aa8ff");
  gradient.addColorStop(0.5, "#8f7cff");
  gradient.addColorStop(1, "#ff7fbe");
  ctx.fillStyle = "#f4f9ff";
  ctx.fillRect(0, 0, width, height);
  ctx.strokeStyle = gradient;
  ctx.lineWidth = 2;
  for (let x = 18; x < width; x += 16) {
    const amp = 20 + Math.sin(x * 0.075) * 30 + Math.cos(x * 0.021) * 18;
    ctx.globalAlpha = 0.34 + (x % 48) / 120;
    ctx.beginPath();
    ctx.moveTo(x, height / 2 - amp);
    ctx.lineTo(x, height / 2 + amp);
    ctx.stroke();
  }
  ctx.globalAlpha = 1;
}

async function drawWaveformFromFile(file) {
  const ctx = waveform.getContext("2d");
  const width = waveform.width;
  const height = waveform.height;
  drawEmptyWaveform();

  let audioContext = null;
  try {
    audioContext = new AudioContext();
    const arrayBuffer = await file.arrayBuffer();
    const audioBuffer = await audioContext.decodeAudioData(arrayBuffer.slice(0));
    const channel = audioBuffer.getChannelData(0);
    const step = Math.ceil(channel.length / width);
    const gradient = ctx.createLinearGradient(0, 0, width, 0);
    gradient.addColorStop(0, "#2f80ed");
    gradient.addColorStop(0.55, "#6f79ff");
    gradient.addColorStop(1, "#ff6faf");
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = "#f4f9ff";
    ctx.fillRect(0, 0, width, height);
    ctx.fillStyle = gradient;
    for (let x = 0; x < width; x += 3) {
      let min = 1;
      let max = -1;
      const start = x * step;
      for (let i = 0; i < step && start + i < channel.length; i += 1) {
        const sample = channel[start + i];
        if (sample < min) min = sample;
        if (sample > max) max = sample;
      }
      const top = (1 + min) * 0.5 * height;
      const bottom = (1 + max) * 0.5 * height;
      ctx.fillRect(x, top, 2, Math.max(2, bottom - top));
    }
    await audioContext.close();
  } catch {
    if (audioContext) {
      await audioContext.close().catch(() => {});
    }
    drawEmptyWaveform();
  }
}

function splitChordSymbol(value) {
  const text = String(value || "").trim();
  const match = text.match(/^([A-Ga-g])([#b♯♭]?)(.*)$/);
  if (!match) return { root: text, accidental: "", suffix: "", bass: "" };

  const root = match[1].toUpperCase();
  const accidental = match[2].replace("#", "♯").replace("b", "♭");
  let suffix = match[3] || "";
  let bass = "";
  if (suffix.includes("/")) {
    const parts = suffix.split("/");
    suffix = parts.shift() || "";
    bass = parts.join("/");
  }
  return { root, accidental, suffix, bass };
}

function renderChordSymbol(value) {
  const { root, accidental, suffix, bass } = splitChordSymbol(value);
  const suffixText = suffix.trim();
  const bassText = bass.trim();
  return `
    <span class="chord-root">${escapeHtml(root)}</span>${accidental ? `<sup>${escapeHtml(accidental)}</sup>` : ""}${suffixText ? `<sup>${escapeHtml(suffixText)}</sup>` : ""}${bassText ? `<span class="slash">/</span>${renderChordSymbol(bassText)}` : ""}
  `;
}

function metricCard(label, value, suffix = "") {
  const resolved = escapeHtml(value);
  return `
    <div class="metric-card">
      <span>${escapeHtml(label)}</span>
      <strong>${resolved === "-" ? resolved : `${resolved}${suffix}`}</strong>
    </div>
  `;
}

function renderChordBox(chord) {
  const timeLabel = chord.end
    ? `${formatTimestamp(chord.time)} - ${formatTimestamp(chord.end)}`
    : formatTimestamp(chord.time);
  return `
    <div class="chord-box">
      <strong class="chord-symbol">${renderChordSymbol(chord.display_chord || chord.chord)}</strong>
      <span class="chord-time">${timeLabel}</span>
    </div>
  `;
}

function renderChordGrid(chords) {
  const validChords = Array.isArray(chords) ? chords.filter((chord) => chord && typeof chord === "object") : [];
  if (!validChords.length) {
    return '<div class="chord-empty">该段暂无可展示和弦</div>';
  }
  return validChords.map(renderChordBox).join("");
}

function intervalBounds(item) {
  const start = finiteNumber(item.start_seconds) ?? parseTimestampPrecise(item.start);
  const end = finiteNumber(item.end_seconds) ?? parseTimestampPrecise(item.end);
  if (start === null) return null;
  const resolvedEnd = end === null || end <= start ? start + 1 : end;
  return { start, end: resolvedEnd };
}

function sectionsDuration(sections) {
  const ends = [];
  sections.forEach((section) => {
    const bounds = intervalBounds(section);
    if (bounds) ends.push(bounds.end);
    const childSections = Array.isArray(section.child_sections) ? section.child_sections : [];
    childSections.forEach((child) => {
      const childBounds = intervalBounds(child);
      if (childBounds) ends.push(childBounds.end);
    });
  });
  const fallback = Math.max(0, ...ends);
  return Number.isFinite(audioPlayer.duration) && audioPlayer.duration > 0 ? audioPlayer.duration : fallback;
}

function timelineWidths(sections) {
  const durations = sections.map((section) => {
    const bounds = intervalBounds(section);
    return bounds ? Math.max(0.5, bounds.end - bounds.start) : 8;
  });
  const total = Math.max(1, durations.reduce((sum, duration) => sum + duration, 0));
  return durations.map((duration) => Math.max(7, (duration / total) * 100));
}

function intervalOverlap(left, right) {
  return Math.max(0, Math.min(left.end, right.end) - Math.max(left.start, right.start));
}

function assignLyricsToSections(childSections, lyrics) {
  const buckets = childSections.map(() => []);
  if (!Array.isArray(lyrics) || !lyrics.length || !childSections.length) return buckets;

  const sectionBounds = childSections.map(intervalBounds);
  const seen = new Set();
  lyrics
    .filter((segment) => segment && typeof segment === "object")
    .forEach((segment) => {
      const lyricBounds = intervalBounds(segment);
      if (!lyricBounds) return;
      const dedupeKey = [
        lyricBounds.start.toFixed(2),
        lyricBounds.end.toFixed(2),
        String(segment.text || "").trim(),
      ].join("|");
      if (seen.has(dedupeKey)) return;
      seen.add(dedupeKey);

      let bestIndex = -1;
      let bestOverlap = 0;
      sectionBounds.forEach((bounds, index) => {
        if (!bounds) return;
        const overlap = intervalOverlap(lyricBounds, bounds);
        const startsInside = lyricBounds.start >= bounds.start && lyricBounds.start < bounds.end;
        if (overlap > bestOverlap || (overlap === bestOverlap && startsInside && bestIndex < 0)) {
          bestIndex = index;
          bestOverlap = overlap;
        }
      });

      if (bestIndex < 0) {
        bestIndex = sectionBounds.findIndex(
          (bounds) => bounds && lyricBounds.start >= bounds.start && lyricBounds.start < bounds.end,
        );
      }
      if (bestIndex >= 0) {
        buckets[bestIndex].push(segment);
      }
    });

  buckets.forEach((bucket) => {
    bucket.sort((left, right) => {
      const leftStart = intervalBounds(left)?.start ?? 0;
      const rightStart = intervalBounds(right)?.start ?? 0;
      return leftStart - rightStart;
    });
  });
  return buckets;
}

function renderSectionLyrics(segments) {
  if (!segments.length) return "";
  return `
    <div class="section-lyrics">
      ${segments.map((segment) => `
        <div class="lyric-line">
          <time>${formatLyricTimestamp(segment.start)} - ${formatLyricTimestamp(segment.end)}</time>
          <span>${escapeHtml(segment.text)}</span>
        </div>
      `).join("")}
    </div>
  `;
}

function renderPlayButton(label, start, end, scope) {
  const startValue = Number.isFinite(start) ? start.toFixed(3) : "";
  const endValue = Number.isFinite(end) ? end.toFixed(3) : "";
  return `
    <button class="play-button" type="button" data-play-segment data-scope="${escapeHtml(scope)}" data-start="${startValue}" data-end="${endValue}" aria-label="播放${escapeHtml(label)}">
      <span class="play-icon"></span>
      <span>${escapeHtml(label)}</span>
    </button>
  `;
}

function truncateText(value, limit = 360) {
  const text = String(value || "").trim();
  return text.length > limit ? `${text.slice(0, limit).trim()}...` : text;
}

function simplifyChords(chords, limit = 48) {
  if (!Array.isArray(chords)) return [];
  return chords
    .filter((chord) => chord && typeof chord === "object")
    .slice(0, limit)
    .map((chord) => ({
      time: chord.time ?? chord.start ?? null,
      end: chord.end ?? null,
      chord: chord.chord || "",
    }))
    .filter((chord) => chord.chord);
}

function simplifyLyrics(segments, limit = 24) {
  if (limit <= 0) return [];
  if (!Array.isArray(segments)) return [];
  return segments
    .filter((segment) => segment && typeof segment === "object" && String(segment.text || "").trim())
    .slice(0, limit)
    .map((segment) => ({
      start: segment.start ?? null,
      end: segment.end ?? null,
      text: truncateText(segment.text, 80),
    }));
}

function compactSectionForChat(section, chordLimit = 16, lyricLimit = 4) {
  if (!section || typeof section !== "object") return null;
  const compact = {
    name: section.name || "段落",
    parent_name: section.parent_name || null,
    start: section.start ?? null,
    end: section.end ?? null,
    // Preserve the precise float seconds computed in buildSelectedSegmentPayload
    // so the backend slices audio accurately instead of falling back to the
    // less-precise display strings.
    start_seconds: section.start_seconds ?? null,
    end_seconds: section.end_seconds ?? null,
    chords: simplifyChords(section.chords, chordLimit),
    lyrics: simplifyLyrics(section.lyrics, lyricLimit),
  };
  if (Array.isArray(section.child_sections)) {
    compact.child_sections = section.child_sections
      .filter((child) => child && typeof child === "object")
      .slice(0, 4)
      .map((child) => compactSectionForChat(child, 8, 0))
      .filter(Boolean);
  }
  return compact;
}

function compactAnalysisForChat(analysis) {
  if (!analysis || typeof analysis !== "object") return {};
  const overall = analysis.overall && typeof analysis.overall === "object" ? analysis.overall : {};
  const sections = Array.isArray(analysis.sections) ? analysis.sections : [];
  const uncertainPoints = Array.isArray(analysis.uncertain_points) ? analysis.uncertain_points : [];
  return {
    title_guess: analysis.title_guess || null,
    overall: {
      key: overall.key ?? null,
      mode: overall.mode ?? null,
      tempo_bpm: overall.tempo_bpm ?? null,
    },
    song_description: truncateText(analysis.song_description, 520),
    sections_overview: sections
      .slice(0, 8)
      .map((section) => compactSectionForChat(section, 12, 0))
      .filter(Boolean)
      .map((section) => {
        delete section.lyrics;
        return section;
      }),
    uncertain_points: uncertainPoints.slice(0, 3).map((item) => truncateText(item, 120)),
  };
}

function compactMessagesForChat(messages) {
  return messages
    .filter((message) => message && String(message.content || "").trim())
    .slice(-6)
    .map((message) => ({
      role: message.role === "assistant" ? "assistant" : "user",
      content: truncateText(message.content, 700),
    }));
}

function buildSelectedSegmentPayload(id, section, level, lyrics, parentName = "") {
  const bounds = intervalBounds(section);
  const childSections = Array.isArray(section.child_sections)
    ? section.child_sections.filter((child) => child && typeof child === "object")
    : [];
  return {
    id,
    level,
    name: section.name || "段落",
    parent_name: parentName || null,
    start: section.start ?? null,
    end: section.end ?? null,
    start_seconds: bounds ? Number(bounds.start.toFixed(3)) : null,
    end_seconds: bounds ? Number(bounds.end.toFixed(3)) : null,
    chords: simplifyChords(section.chords),
    child_sections: childSections.map((child) => ({
      name: child.name || "小段落",
      start: child.start ?? null,
      end: child.end ?? null,
      chords: simplifyChords(child.chords),
    })),
  };
}

function renderSelectButton(label, segmentId) {
  const isSelected = selectedSegments.has(segmentId);
  return `
    <button class="select-segment-button ${isSelected ? "is-selected" : ""}" type="button" data-select-segment="${escapeHtml(segmentId)}" aria-pressed="${isSelected ? "true" : "false"}">
      <span class="select-dot"></span>
      <span>${isSelected ? "已选中" : escapeHtml(label)}</span>
    </button>
  `;
}

function updateSelectionUI() {
  const count = selectedSegments.size;
  if (selectionCount) selectionCount.textContent = String(count);
  if (selectionClear) selectionClear.disabled = count === 0;
  if (selectionContext) selectionContext.classList.toggle("has-selection", count > 0);
  chartView.querySelectorAll("[data-select-segment]").forEach((button) => {
    const id = button.getAttribute("data-select-segment") || "";
    const selected = selectedSegments.has(id);
    button.classList.toggle("is-selected", selected);
    button.setAttribute("aria-pressed", selected ? "true" : "false");
    const label = button.querySelector("span:last-child");
    if (label) label.textContent = selected ? "已选中" : "加入对话";
  });
}

function selectedSegmentPayloads() {
  return Array.from(selectedSegments.values());
}

function renderChildSection(section, sectionLyrics, segmentId, parentName, fallbackChords = []) {
  const bounds = intervalBounds(section);
  const chords = Array.isArray(section.chords) && section.chords.length ? section.chords : fallbackChords;
  return `
    <div class="child-section">
      <div class="child-meta">
        <div>
          <strong>${escapeHtml(section.name, "段落")}</strong>
          <span>${formatTimestamp(section.start)} - ${formatTimestamp(section.end)}</span>
        </div>
        <div class="segment-tools">
          ${renderSelectButton("加入对话", segmentId)}
          ${bounds ? renderPlayButton("播放", bounds.start, bounds.end, "child") : ""}
        </div>
      </div>
      <div class="notation-panel">
        <div class="chord-grid">${renderChordGrid(chords)}</div>
        ${renderSectionLyrics(sectionLyrics)}
      </div>
    </div>
  `;
}

function renderSongMap(sections) {
  if (!sections.length) return "";
  const widths = timelineWidths(sections);
  const duration = sectionsDuration(sections);
  return `
    <section class="song-map">
      <div class="song-map-head">
        <div>
          <span>Song Structure</span>
          <strong>整体歌曲划分</strong>
        </div>
        <button class="play-button play-button-main" type="button" data-play-full>
          <span class="play-icon"></span>
          <span>播放完整曲目</span>
        </button>
      </div>
      <div class="playback-progress" aria-label="播放进度">
        <div class="playback-progress-fill" id="playback-progress-fill"></div>
      </div>
      <div class="playback-time">
        <span id="playback-current">00:00</span>
        <span id="playback-label">未播放</span>
        <span id="playback-total">${formatPlaybackTime(duration)}</span>
      </div>
      <div class="song-map-lane">
        ${sections.map((section, index) => {
          const bounds = intervalBounds(section);
          return `
            <button class="song-map-segment" type="button" style="flex: ${widths[index].toFixed(2)} 1 0;" ${bounds ? `data-play-segment data-scope="section" data-start="${bounds.start.toFixed(3)}" data-end="${bounds.end.toFixed(3)}"` : ""}>
              <strong>${escapeHtml(section.name, "段落")}</strong>
              <span>${formatTimestamp(section.start)} - ${formatTimestamp(section.end)}</span>
            </button>
          `;
        }).join("")}
      </div>
    </section>
  `;
}

function renderSongSections(sections, lyrics) {
  if (!sections.length) {
    return '<article class="song-section-card"><h3>没有返回段落分析</h3></article>';
  }

  const sectionItems = sections.map((section) => ({
    section,
    childSections: Array.isArray(section.child_sections) && section.child_sections.length
      ? section.child_sections.filter((child) => child && typeof child === "object")
      : [section],
  }));
  const allChildSections = sectionItems.flatMap((item) => item.childSections);
  const lyricBuckets = assignLyricsToSections(allChildSections, lyrics);
  let childOffset = 0;

  return sectionItems.map(({ section, childSections }, sectionIndex) => {
    const bounds = intervalBounds(section);
    const sectionId = `section-${sectionIndex}`;
    const sectionStartOffset = childOffset;
    const renderedChildren = childSections.map((child, childIndex) => {
      const childLyrics = lyricBuckets[childOffset] || [];
      const childId = `child-${sectionIndex}-${childIndex}`;
      availableSegments.set(
        childId,
        buildSelectedSegmentPayload(childId, child, "child", childLyrics, section.name || ""),
      );
      childOffset += 1;
      return renderChildSection(child, childLyrics, childId, section.name || "", section.chords || []);
    }).join("");
    const sectionLyrics = lyricBuckets
      .slice(sectionStartOffset, sectionStartOffset + childSections.length)
      .flat();
    availableSegments.set(
      sectionId,
      buildSelectedSegmentPayload(sectionId, section, "section", sectionLyrics),
    );
    return `
      <article class="song-section-card">
        <header>
          <div>
            <h3>${escapeHtml(section.name, "段落")}</h3>
            <p>${childSections.length} 个小段落</p>
          </div>
          <div class="section-actions">
            ${renderSelectButton("加入对话", sectionId)}
            ${bounds ? renderPlayButton("播放本段", bounds.start, bounds.end, "section") : ""}
            <span>${formatTimestamp(section.start)} - ${formatTimestamp(section.end)}</span>
          </div>
        </header>
        <div class="child-section-list">
          ${renderedChildren}
        </div>
      </article>
    `;
  }).join("");
}

function renderChart(analysis, sourceName) {
  const overall = analysis.overall || {};
  const sections = Array.isArray(analysis.sections) ? analysis.sections.filter((section) => section && typeof section === "object") : [];
  const lyrics = Array.isArray(analysis.lyrics_segments) ? analysis.lyrics_segments : [];
  const warnings = Array.isArray(analysis.uncertain_points)
    ? analysis.uncertain_points.filter((item) => item !== null && item !== undefined && String(item).trim())
    : [];
  const title = analysis.title_guess || sourceName || "未命名歌曲";
  availableSegments = new Map();

  return `
    <article class="score-sheet">
      <header class="score-header">
        <div class="score-title-mark">C</div>
        <div class="score-title-copy">
          <p>Generated Chord Sheet</p>
          <h2>${escapeHtml(title)}</h2>
          <span>${escapeHtml(sourceName)}</span>
        </div>
        <div class="score-badge">AI-ChordCraft</div>
      </header>

      <div class="metric-strip">
        ${metricCard("调性", overall.key)}
        ${metricCard("调式", overall.mode)}
        ${metricCard("速度", overall.tempo_bpm, " BPM")}
      </div>

      ${warnings.length ? `
        <section class="analysis-warning-panel" aria-label="分析提示">
          <strong>分析提示</strong>
          <ul>
            ${warnings.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}
          </ul>
        </section>
      ` : ""}

      ${renderSongMap(sections)}

      <section class="score-body">
        <div class="section-title">
          <h2>和弦谱</h2>
          <span>${sections.length} 个段落</span>
        </div>
        ${renderSongSections(sections, lyrics)}
      </section>
    </article>
  `;
}

function renderChatMessages() {
  if (!chatMessages.length) {
    return `
      <div class="chat-empty">
        <strong>生成后可继续追问</strong>
        <span>可选择右侧片段，把和声走向和对应音频加入对话。</span>
      </div>
    `;
  }
  return chatMessages.map((message) => {
    const isUser = message.role === "user";
    return `
      <article class="chat-message ${isUser ? "from-user" : "from-assistant"}">
        <span>${isUser ? "你" : "助手"}</span>
        <p>${escapeHtml(message.content).replaceAll("\n", "<br>")}</p>
      </article>
    `;
  }).join("");
}

function syncChatFeed() {
  chatFeed.innerHTML = renderChatMessages();
  chatFeed.scrollTop = chatFeed.scrollHeight;
}

function setChatEnabled(enabled) {
  chatSubmit.disabled = !enabled;
  chatMessageInput.disabled = !enabled;
  chatMessageInput.placeholder = enabled
    ? "可先在右侧选择片段，再问：这段如何改成钢琴弹唱？"
    : "请先上传音频并生成和弦谱";
}

function clearSelectedSegments() {
  selectedSegments = new Map();
  updateSelectionUI();
}

function setMediaSource(file) {
  audioPlayer.pause();
  audioPlayer.removeAttribute("src");
  audioPlayer.load();
  activePlayback = null;
  if (mediaObjectUrl) {
    URL.revokeObjectURL(mediaObjectUrl);
    mediaObjectUrl = null;
  }
  if (!file) return;
  mediaObjectUrl = URL.createObjectURL(file);
  audioPlayer.src = mediaObjectUrl;
  audioPlayer.load();
}

function playbackTotalSeconds() {
  const sections = lastResult?.analysis?.sections;
  return Array.isArray(sections) ? sectionsDuration(sections) : audioPlayer.duration || 0;
}

function setPlaybackButtonsPlaying(isPlaying) {
  chartView.querySelectorAll("[data-play-segment], [data-play-full]").forEach((button) => {
    button.classList.remove("is-playing");
  });
  if (!isPlaying || !activePlayback) return;
  const selector = activePlayback.full
    ? "[data-play-full]"
    : `[data-play-segment][data-start="${activePlayback.start.toFixed(3)}"][data-end="${activePlayback.end.toFixed(3)}"]`;
  chartView.querySelectorAll(selector).forEach((button) => button.classList.add("is-playing"));
}

function updatePlaybackUI() {
  const total = playbackTotalSeconds();
  const current = Number.isFinite(audioPlayer.currentTime) ? audioPlayer.currentTime : 0;
  const fill = document.getElementById("playback-progress-fill");
  const currentEl = document.getElementById("playback-current");
  const totalEl = document.getElementById("playback-total");
  const labelEl = document.getElementById("playback-label");
  if (fill) {
    const ratio = total > 0 ? Math.max(0, Math.min(1, current / total)) : 0;
    fill.style.width = `${(ratio * 100).toFixed(2)}%`;
  }
  if (currentEl) currentEl.textContent = formatPlaybackTime(current);
  if (totalEl) totalEl.textContent = formatPlaybackTime(total);
  if (labelEl) {
    labelEl.textContent = activePlayback
      ? activePlayback.label
      : audioPlayer.paused ? "未播放" : "播放中";
  }
  setPlaybackButtonsPlaying(!audioPlayer.paused);
}

function isSamePlaybackRange(start, end, full) {
  if (!activePlayback) return false;
  if (Boolean(activePlayback.full) !== Boolean(full)) return false;
  return Math.abs(activePlayback.start - start) < 0.01 && Math.abs(activePlayback.end - end) < 0.01;
}

async function playRange(start, end, label, full = false) {
  if (!audioPlayer.src) {
    setStatus("请先选择音频", "error");
    return;
  }
  const total = playbackTotalSeconds();
  const resolvedStart = Math.max(0, Number.isFinite(start) ? start : 0);
  const resolvedEnd = full
    ? (Number.isFinite(audioPlayer.duration) && audioPlayer.duration > 0 ? audioPlayer.duration : total)
    : Math.max(resolvedStart + 0.2, Number.isFinite(end) ? end : total);
  if (isSamePlaybackRange(resolvedStart, resolvedEnd, full)) {
    if (audioPlayer.paused) {
      if (audioPlayer.currentTime < resolvedStart || audioPlayer.currentTime >= resolvedEnd) {
        audioPlayer.currentTime = resolvedStart;
      }
      try {
        await audioPlayer.play();
        setStatus(`播放：${label}`);
      } catch (error) {
        setStatus("播放失败", "error");
        console.error(error);
      }
    } else {
      audioPlayer.pause();
      setStatus(`已暂停：${label}`);
    }
    updatePlaybackUI();
    return;
  }
  activePlayback = {
    start: resolvedStart,
    end: resolvedEnd,
    label,
    full,
  };
  audioPlayer.currentTime = resolvedStart;
  try {
    await audioPlayer.play();
    setStatus(`播放：${label}`);
  } catch (error) {
    setStatus("播放失败", "error");
    console.error(error);
  }
  updatePlaybackUI();
}

function stopPlaybackAtBoundary() {
  if (!activePlayback) return;
  audioPlayer.pause();
  audioPlayer.currentTime = Math.min(activePlayback.end, playbackTotalSeconds() || activePlayback.end);
  activePlayback = null;
  updatePlaybackUI();
}

function showProcessing() {
  const node = processingTemplate.content.cloneNode(true);
  const copy = node.querySelector("#processing-copy");
  if (copy) {
    copy.textContent = "正在识别结构与和弦；若检测到 MOSS-Music 服务，将自动补充歌词。";
  }
  chartView.className = "chart-surface";
  chartView.replaceChildren(node);
}

function showError(message) {
  chartView.className = "chart-surface";
  chartView.innerHTML = `
    <div class="error-panel">
      <strong>分析失败</strong>
      <span>${escapeHtml(message)}</span>
    </div>
  `;
}

async function submitAnalysis(event) {
  event.preventDefault();
  const file = fileInput.files[0];
  if (!file) return;

  submitButton.disabled = true;
  setChatEnabled(false);
  const isVideo = file.type.startsWith("video/");
  setStatus(isVideo ? "读取视频" : "读取音频", "busy");
  showProcessing();

  try {
    const mediaBase64 = await fileToDataUrl(file);
    lastMediaBase64 = mediaBase64;
    chatMessages = [];
    selectedSegments = new Map();
    syncChatFeed();
    updateSelectionUI();
    const payload = {
      filename: file.name,
      audio_base64: mediaBase64,
      ...ANALYSIS_DEFAULTS,
    };

    setStatus(isVideo ? "抽取音频并分析" : "运行分析", "busy");
    const response = await fetch(apiUrl("/api/analyze"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await readJsonResponse(response, "/api/analyze");
    if (!response.ok) {
      throw new Error(result.detail || "分析失败");
    }

    lastResult = result;
    chartView.className = "chart-surface has-score";
    chartView.innerHTML = renderChart(result.analysis, file.name);
    updateSelectionUI();
    updatePlaybackUI();
    setChatEnabled(true);
    setStatus(`完成 / ${Number(result.elapsed_seconds || 0).toFixed(2)}s`);
  } catch (error) {
    lastResult = null;
    setChatEnabled(false);
    showError(networkErrorMessage(error, "/api/analyze"));
    setStatus("错误", "error");
  } finally {
    submitButton.disabled = false;
  }
}

async function submitChat(event) {
  event.preventDefault();
  if (!lastResult || !lastResult.analysis) {
    setStatus("请先生成和弦谱", "error");
    return;
  }

  const message = String(chatMessageInput.value || "").trim();
  if (!message) return;

  const modelMode = "instruct";
  chatMessages.push({ role: "user", content: message });
  chatMessageInput.value = "";
  syncChatFeed();
  chatSubmit.disabled = true;
  setStatus("模型回答中", "busy");

  try {
    const file = fileInput.files[0];
    const selectedPayloads = selectedSegmentPayloads();
    const payload = {
      filename: file ? file.name : "upload.wav",
      audio_base64: selectedPayloads.length ? lastMediaBase64 : null,
      analysis: compactAnalysisForChat(lastResult.analysis),
      arrangement: null,
      selected_sections: selectedPayloads
        .slice(0, 6)
        .map((section) => compactSectionForChat(section, 24, 0))
        .filter(Boolean),
      messages: compactMessagesForChat(chatMessages),
      model_mode: modelMode,
      ...CHAT_DEFAULTS,
    };
    const response = await fetch(apiUrl("/api/chat"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await readJsonResponse(response, "/api/chat");
    if (!response.ok) {
      throw new Error(result.detail || "追问失败");
    }
    chatMessages.push({ role: "assistant", content: result.answer || "模型没有返回可展示内容。" });
    syncChatFeed();
    setStatus(`追问完成 / ${Number(result.elapsed_seconds || 0).toFixed(2)}s`);
  } catch (error) {
    // Roll the failed user turn out of history so it is not silently resent
    // as context on the next attempt; show the error in the feed instead.
    if (chatMessages.length && chatMessages[chatMessages.length - 1].role === "user") {
      chatMessages.pop();
    }
    chatMessages.push({ role: "assistant", content: `追问失败：${networkErrorMessage(error, "/api/chat")}` });
    syncChatFeed();
    setStatus("追问错误", "error");
  } finally {
    chatSubmit.disabled = false;
  }
}

fileInput.addEventListener("change", () => {
  const file = fileInput.files[0];
  filenameEl.textContent = file ? file.name : "尚未选择媒体文件";
  lastResult = null;
  chatMessages = [];
  selectedSegments = new Map();
  availableSegments = new Map();
  setMediaSource(file);
  syncChatFeed();
  updateSelectionUI();
  setChatEnabled(false);
  if (file && file.type.startsWith("audio/")) {
    drawWaveformFromFile(file);
  } else {
    drawEmptyWaveform();
  }
});

form.addEventListener("submit", submitAnalysis);
chatForm.addEventListener("submit", submitChat);
selectionClear.addEventListener("click", clearSelectedSegments);
chartView.addEventListener("click", (event) => {
  const target = event.target instanceof Element ? event.target : null;
  if (!target) return;
  const selectButton = target.closest("[data-select-segment]");
  if (selectButton) {
    const id = selectButton.getAttribute("data-select-segment") || "";
    if (selectedSegments.has(id)) {
      selectedSegments.delete(id);
    } else {
      const payload = availableSegments.get(id);
      if (payload) selectedSegments.set(id, payload);
    }
    updateSelectionUI();
    return;
  }
  const fullButton = target.closest("[data-play-full]");
  if (fullButton) {
    playRange(0, playbackTotalSeconds(), "完整曲目", true);
    return;
  }
  const segmentButton = target.closest("[data-play-segment]");
  if (!segmentButton) return;
  const start = Number(segmentButton.dataset.start);
  const end = Number(segmentButton.dataset.end);
  if (!Number.isFinite(start) || !Number.isFinite(end)) return;
  const label = segmentButton.dataset.scope === "child"
    ? "当前小段落"
    : segmentButton.querySelector("strong")?.textContent?.trim() || "当前段落";
  playRange(start, end, label, false);
});

audioPlayer.addEventListener("loadedmetadata", updatePlaybackUI);
audioPlayer.addEventListener("timeupdate", () => {
  if (activePlayback && audioPlayer.currentTime >= activePlayback.end) {
    stopPlaybackAtBoundary();
    return;
  }
  updatePlaybackUI();
});
audioPlayer.addEventListener("pause", updatePlaybackUI);
audioPlayer.addEventListener("play", updatePlaybackUI);
audioPlayer.addEventListener("ended", () => {
  activePlayback = null;
  updatePlaybackUI();
});

drawEmptyWaveform();
syncChatFeed();
setChatEnabled(false);
