const state = {
  files: [],
};

const form = document.querySelector("#material-form");
const fileInput = document.querySelector("#files");
const fileList = document.querySelector("#file-list");
const preview = document.querySelector("#preview");
const draftButton = document.querySelector("#download-draft");
const manifestButton = document.querySelector("#download-manifest");

function readForm() {
  const data = new FormData(form);
  return {
    date: String(data.get("date") || "").trim(),
    kind: String(data.get("kind") || "ribao").trim(),
    title_hint: String(data.get("titleHint") || "").trim(),
    summary: String(data.get("summary") || "").trim(),
    people_notes: String(data.get("peopleNotes") || "").trim(),
    privacy_notes: String(data.get("privacyNotes") || "").trim(),
    strava_links: String(data.get("stravaLinks") || "")
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean),
  };
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function readImageMeta(file) {
  return new Promise((resolve) => {
    if (!file.type.startsWith("image/")) {
      resolve({ width: null, height: null });
      return;
    }
    const url = URL.createObjectURL(file);
    const image = new Image();
    image.onload = () => {
      URL.revokeObjectURL(url);
      resolve({ width: image.naturalWidth, height: image.naturalHeight });
    };
    image.onerror = () => {
      URL.revokeObjectURL(url);
      resolve({ width: null, height: null });
    };
    image.src = url;
  });
}

async function handleFiles(files) {
  const incoming = await Promise.all(
    Array.from(files).map(async (file, index) => {
      const meta = await readImageMeta(file);
      return {
        id: `${Date.now()}-${index}-${file.name}`,
        filename: file.name,
        type: file.type || "application/octet-stream",
        size: file.size,
        width: meta.width,
        height: meta.height,
        note: "",
      };
    }),
  );
  state.files = incoming;
  renderFiles();
  updatePreview();
}

function renderFiles() {
  fileList.innerHTML = "";
  if (state.files.length === 0) {
    fileList.innerHTML = "<p>尚未选择图片。真实文件只留在你的电脑上。</p>";
    return;
  }
  for (const item of state.files) {
    const card = document.createElement("article");
    card.className = "file-card";
    card.innerHTML = `
      <strong>${escapeHtml(item.filename)}</strong>
      <div class="file-meta">
        <span>${escapeHtml(item.type)}</span>
        <span>${formatBytes(item.size)}</span>
        <span>${item.width && item.height ? `${item.width}x${item.height}` : "尺寸未知"}</span>
      </div>
      <label>
        素材备注
        <input data-file-note="${escapeHtml(item.id)}" type="text" placeholder="例如：冲线、合照、群聊梗截图" value="${escapeHtml(item.note)}" />
      </label>
    `;
    fileList.appendChild(card);
  }
}

function buildDraftMarkdown(input = readForm()) {
  const lines = [
    `# ${input.date || "未填日期"} ${kindLabel(input.kind)} 素材初稿`,
    "",
    "## 标题方向",
    input.title_hint || "-",
    "",
    "## 活动摘要",
    input.summary || "-",
    "",
    "## Strava 链接",
    ...(input.strava_links.length ? input.strava_links.map((link) => `- ${link}`) : ["-"]),
    "",
    "## 人物与梗",
    input.people_notes || "-",
    "",
    "## 隐私注意事项",
    input.privacy_notes || "-",
    "",
    "## 素材清单",
    ...state.files.map((file, index) => {
      const size = file.width && file.height ? `${file.width}x${file.height}` : "尺寸未知";
      return `- ${index + 1}. ${file.filename} (${size}, ${formatBytes(file.size)})：${file.note || "待补充"}`;
    }),
    "",
    "## 本地流水线",
    `python3 pipeline.py collect --date ${input.date || "0913"} --kind ${input.kind || "ribao"}`,
    `python3 pipeline.py generate --date ${input.date || "0913"} --kind ${input.kind || "ribao"}`,
  ];
  return lines.join("\n");
}

function buildManifest(input = readForm()) {
  return {
    schema_version: 1,
    generated_at: new Date().toISOString(),
    privacy: {
      local_only: true,
      uploads_files: false,
      note: "manifest 只记录文件元数据和用户备注，不包含图片二进制。",
    },
    article: input,
    materials: state.files.map((file) => ({
      filename: file.filename,
      type: file.type,
      size: file.size,
      width: file.width,
      height: file.height,
      note: file.note,
    })),
  };
}

function downloadText(filename, text, type) {
  const blob = new Blob([text], { type });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

function updatePreview() {
  preview.textContent = buildDraftMarkdown();
}

function kindLabel(kind) {
  const labels = {
    ribao: "日报",
    wanbao: "晚报",
    zaobao: "早报",
    subao: "速报",
    kuaixun: "快讯",
    tuanjian: "团建",
  };
  return labels[kind] || kind;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => {
    const entities = {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    };
    return entities[char];
  });
}

fileInput.addEventListener("change", (event) => {
  handleFiles(event.target.files);
});

fileList.addEventListener("input", (event) => {
  const input = event.target;
  const id = input.getAttribute("data-file-note");
  if (!id) return;
  const target = state.files.find((file) => file.id === id);
  if (target) target.note = input.value;
  updatePreview();
});

form.addEventListener("input", updatePreview);

draftButton.addEventListener("click", () => {
  const input = readForm();
  downloadText(`${input.date || "xiaohuoche"}-${input.kind || "ribao"}-draft.md`, buildDraftMarkdown(input), "text/markdown;charset=utf-8");
});

manifestButton.addEventListener("click", () => {
  const input = readForm();
  const manifest = JSON.stringify(buildManifest(input), null, 2);
  downloadText(`${input.date || "xiaohuoche"}-${input.kind || "ribao"}-manifest.json`, manifest, "application/json;charset=utf-8");
});

renderFiles();
updatePreview();
