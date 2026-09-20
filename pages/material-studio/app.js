const state = {
  files: [],
};

const form = document.querySelector("#material-form");
const fileInput = document.querySelector("#files");
const folderInput = document.querySelector("#folders");
const fileList = document.querySelector("#file-list");
const preview = document.querySelector("#preview");
const zipButton = document.querySelector("#download-zip");
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
      const relativePath = normalizePath(file.webkitRelativePath || file.name);
      return {
        id: `${Date.now()}-${index}-${file.name}`,
        filename: file.name,
        relative_path: relativePath,
        type: file.type || "application/octet-stream",
        size: file.size,
        width: meta.width,
        height: meta.height,
        note: "",
        file,
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
        <span>${escapeHtml(item.relative_path)}</span>
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
      preserve_original_bytes: true,
      note: "manifest 记录文件元数据和用户备注；完整 ZIP 会把原始素材文件按原字节写入 materials/original/。",
    },
    article: input,
    materials: state.files.map((file) => ({
      filename: file.filename,
      relative_path: file.relative_path,
      package_path: `materials/original/${file.relative_path}`,
      type: file.type,
      size: file.size,
      width: file.width,
      height: file.height,
      note: file.note,
      preserve_original_bytes: true,
    })),
  };
}

async function buildZipPackage(input = readForm()) {
  const root = packageRoot(input);
  const entries = [
    {
      path: `${root}/draft.md`,
      data: encodeText(buildDraftMarkdown(input)),
    },
    {
      path: `${root}/manifest.json`,
      data: encodeText(JSON.stringify(buildManifest(input), null, 2)),
    },
    {
      path: `${root}/README.txt`,
      data: encodeText(
        [
          "辛庄桥小火车素材采集包",
          "",
          "结构：",
          "- draft.md：给流水线使用的素材初稿",
          "- manifest.json：结构化素材清单",
          "- materials/original/：原始素材文件，保持原文件字节和原图像素",
          "",
          "请把整个目录放入本地 inbox 后继续执行 pipeline.py。",
        ].join("\n"),
      ),
    },
  ];

  for (const item of state.files) {
    const bytes = new Uint8Array(await item.file.arrayBuffer());
    entries.push({
      path: `${root}/materials/original/${item.relative_path}`,
      data: bytes,
    });
  }
  return createZipBlob(entries);
}

function downloadText(filename, text, type) {
  const blob = new Blob([text], { type });
  downloadBlob(filename, blob);
}

function downloadBlob(filename, blob) {
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

function packageRoot(input) {
  const date = slug(input.date || "xiaohuoche");
  const kind = slug(input.kind || "ribao");
  return `${date}-${kind}`;
}

function slug(value) {
  return String(value)
    .trim()
    .replace(/[\\/:*?"<>|]+/g, "-")
    .replace(/\s+/g, "-")
    .replace(/^-+|-+$/g, "") || "xiaohuoche";
}

function normalizePath(path) {
  return path
    .split("/")
    .map((part) => slug(part))
    .filter(Boolean)
    .join("/");
}

function encodeText(text) {
  return new TextEncoder().encode(text);
}

function createZipBlob(entries) {
  const localParts = [];
  const centralParts = [];
  let offset = 0;
  for (const entry of entries) {
    const name = encodeText(entry.path);
    const data = entry.data;
    const crc = crc32(data);
    const localHeader = zipLocalHeader(name, data, crc);
    localParts.push(localHeader, data);
    centralParts.push(zipCentralHeader(name, data, crc, offset));
    offset += localHeader.length + data.length;
  }
  const centralSize = centralParts.reduce((sum, part) => sum + part.length, 0);
  const end = zipEndRecord(entries.length, centralSize, offset);
  return new Blob([...localParts, ...centralParts, end], { type: "application/zip" });
}

function zipLocalHeader(name, data, crc) {
  const header = new Uint8Array(30 + name.length);
  const view = new DataView(header.buffer);
  view.setUint32(0, 0x04034b50, true);
  view.setUint16(4, 20, true);
  view.setUint16(6, 0x0800, true);
  view.setUint16(8, 0, true);
  view.setUint32(14, crc, true);
  view.setUint32(18, data.length, true);
  view.setUint32(22, data.length, true);
  view.setUint16(26, name.length, true);
  header.set(name, 30);
  return header;
}

function zipCentralHeader(name, data, crc, offset) {
  const header = new Uint8Array(46 + name.length);
  const view = new DataView(header.buffer);
  view.setUint32(0, 0x02014b50, true);
  view.setUint16(4, 20, true);
  view.setUint16(6, 20, true);
  view.setUint16(8, 0x0800, true);
  view.setUint16(10, 0, true);
  view.setUint32(16, crc, true);
  view.setUint32(20, data.length, true);
  view.setUint32(24, data.length, true);
  view.setUint16(28, name.length, true);
  view.setUint32(42, offset, true);
  header.set(name, 46);
  return header;
}

function zipEndRecord(count, centralSize, centralOffset) {
  const end = new Uint8Array(22);
  const view = new DataView(end.buffer);
  view.setUint32(0, 0x06054b50, true);
  view.setUint16(8, count, true);
  view.setUint16(10, count, true);
  view.setUint32(12, centralSize, true);
  view.setUint32(16, centralOffset, true);
  return end;
}

function crc32(data) {
  let crc = 0xffffffff;
  for (const byte of data) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) {
      crc = crc & 1 ? (crc >>> 1) ^ 0xedb88320 : crc >>> 1;
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
}

fileInput.addEventListener("change", (event) => {
  handleFiles(event.target.files);
});

folderInput.addEventListener("change", (event) => {
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

zipButton.addEventListener("click", async () => {
  const input = readForm();
  zipButton.disabled = true;
  zipButton.textContent = "正在打包...";
  try {
    const blob = await buildZipPackage(input);
    downloadBlob(`${packageRoot(input)}-materials.zip`, blob);
  } finally {
    zipButton.disabled = false;
    zipButton.textContent = "导出完整素材包 ZIP";
  }
});

manifestButton.addEventListener("click", () => {
  const input = readForm();
  const manifest = JSON.stringify(buildManifest(input), null, 2);
  downloadText(`${input.date || "xiaohuoche"}-${input.kind || "ribao"}-manifest.json`, manifest, "application/json;charset=utf-8");
});

renderFiles();
updatePreview();
