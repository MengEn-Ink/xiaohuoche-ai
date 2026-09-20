const state = {
  currentStage: "activity",
  files: [],
  deletedFiles: [],
  confirmed: false,
};

const stages = [
  { id: "activity", label: "活动信息" },
  { id: "materials", label: "素材池" },
  { id: "layout", label: "排版台" },
  { id: "copy", label: "文案批注" },
  { id: "preview", label: "最终预览" },
];

const layoutRoles = [
  ["cover", "封面"],
  ["hero", "主图"],
  ["body", "正文"],
  ["collage", "拼贴"],
  ["chat", "群聊梗"],
  ["data", "数据截图"],
  ["spare", "备用"],
  ["unused", "不使用"],
];

const form = document.querySelector("#material-form");
const fileInput = document.querySelector("#files");
const folderInput = document.querySelector("#folders");
const fileList = document.querySelector("#file-list");
const layoutWorkbench = document.querySelector("#layout-workbench");
const preview = document.querySelector("#preview");
const visualPreview = document.querySelector("#visual-preview");
const zipButton = document.querySelector("#download-zip");
const draftButton = document.querySelector("#download-draft");
const manifestButton = document.querySelector("#download-manifest");
const confirmExport = document.querySelector("#confirm-export");
const deletedSummary = document.querySelector("#deleted-summary");
const undoDeleteButton = document.querySelector("#undo-delete");
const stageNav = document.querySelector("#stage-nav");
const workspaceSummary = document.querySelector("#workspace-summary");
const prevStageButton = document.querySelector("#prev-stage");
const nextStageButton = document.querySelector("#next-stage");

function readForm() {
  const data = new FormData(form);
  return {
    date: String(data.get("date") || "").trim(),
    kind: String(data.get("kind") || "ribao").trim(),
    title_hint: String(data.get("titleHint") || "").trim(),
    summary: String(data.get("summary") || "").trim(),
    people_notes: String(data.get("peopleNotes") || "").trim(),
    privacy_notes: String(data.get("privacyNotes") || "").trim(),
    ai_instruction: String(data.get("aiInstructions") || "").trim(),
    copy: {
      title: String(data.get("finalTitle") || "").trim(),
      intro: String(data.get("introCopy") || "").trim(),
      body_points: String(data.get("bodyPoints") || "").trim(),
      outro: String(data.get("outroCopy") || "").trim(),
    },
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
  releasePreviewUrls();
  state.deletedFiles = [];
  state.confirmed = false;
  confirmExport.checked = false;

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
        caption: "",
        layout_role: defaultLayoutRole(index),
        ai_instruction: "",
        file,
        preview_url: file.type.startsWith("image/") ? URL.createObjectURL(file) : "",
      };
    }),
  );
  state.files = incoming;
  renderFiles();
  renderLayoutWorkbench();
  renderDeletedZone();
  updatePreview();
}

function releasePreviewUrls() {
  for (const item of [...state.files, ...state.deletedFiles.map((deleted) => deleted.item)]) {
    if (item.preview_url) URL.revokeObjectURL(item.preview_url);
  }
}

function renderFiles() {
  fileList.innerHTML = "";
  if (state.files.length === 0) {
    fileList.innerHTML = "<p>尚未选择图片。真实文件只留在你的电脑上。</p>";
    return;
  }
  for (const [index, item] of state.files.entries()) {
    const card = document.createElement("article");
    card.className = "file-card";
    const thumbnail = item.preview_url
      ? `<img class="file-thumb" src="${escapeHtml(item.preview_url)}" alt="${escapeHtml(item.filename)} 预览" />`
      : `<div class="file-thumb placeholder">无预览</div>`;
    card.innerHTML = `
      <div class="file-head">
        ${thumbnail}
        <div>
          <strong>${index + 1}. ${escapeHtml(item.filename)}</strong>
          <div class="file-meta">
            <span>${escapeHtml(item.type)}</span>
            <span>${formatBytes(item.size)}</span>
            <span>${item.width && item.height ? `${item.width}x${item.height}` : "尺寸未知"}</span>
            <span>${escapeHtml(item.relative_path)}</span>
          </div>
        </div>
      </div>
      <div class="file-controls">
        <button type="button" data-stage="layout">去排版</button>
        <button type="button" data-file-action="delete" data-file-id="${escapeHtml(item.id)}">删除</button>
      </div>
      <label>
        素材备注
        <input data-file-field="note" data-file-id="${escapeHtml(item.id)}" type="text" placeholder="例如：冲线、合照、群聊梗截图" value="${escapeHtml(item.note)}" />
      </label>
    `;
    fileList.appendChild(card);
  }
}

function renderLayoutWorkbench() {
  layoutWorkbench.innerHTML = "";
  if (state.files.length === 0) {
    layoutWorkbench.innerHTML = "<p>素材池为空。请先回到素材池选择本地图片。</p>";
    return;
  }
  for (const [index, item] of state.files.entries()) {
    const row = document.createElement("article");
    row.className = "layout-row";
    const thumbnail = item.preview_url
      ? `<img class="file-thumb" src="${escapeHtml(item.preview_url)}" alt="${escapeHtml(item.filename)} 预览" />`
      : `<div class="file-thumb placeholder">无预览</div>`;
    row.innerHTML = `
      ${thumbnail}
      <div class="layout-body">
        <div class="layout-title">
          <strong>${index + 1}. ${escapeHtml(item.filename)}</strong>
          <span>${item.width && item.height ? `${item.width}x${item.height}` : "尺寸未知"}</span>
        </div>
        <div class="file-controls">
          <button type="button" data-file-action="move-up" data-file-id="${escapeHtml(item.id)}" ${index === 0 ? "disabled" : ""}>上移</button>
          <button type="button" data-file-action="move-down" data-file-id="${escapeHtml(item.id)}" ${index === state.files.length - 1 ? "disabled" : ""}>下移</button>
          <button type="button" data-stage="materials">回素材池</button>
        </div>
        <div class="two-column">
          <label>
            版位
            <select data-file-field="layout_role" data-file-id="${escapeHtml(item.id)}">
              ${layoutRoles
                .map(([value, label]) => `<option value="${value}" ${item.layout_role === value ? "selected" : ""}>${label}</option>`)
                .join("")}
            </select>
          </label>
          <label>
            图片说明
            <input data-file-field="caption" data-file-id="${escapeHtml(item.id)}" type="text" placeholder="例如：队伍爬坡进入最后一公里" value="${escapeHtml(item.caption)}" />
          </label>
        </div>
        <label>
          单图 AI 批注
          <textarea data-file-field="ai_instruction" data-file-id="${escapeHtml(item.id)}" rows="3" placeholder="例如：保留人物表情，不要裁掉码表；车牌需打码">${escapeHtml(item.ai_instruction)}</textarea>
        </label>
      </div>
    `;
    layoutWorkbench.appendChild(row);
  }
}

function renderDeletedZone() {
  const last = state.deletedFiles[state.deletedFiles.length - 1];
  deletedSummary.textContent = last
    ? `已删除 ${state.deletedFiles.length} 个素材，最近删除：${last.item.filename}`
    : "暂无删除素材。";
  undoDeleteButton.disabled = state.deletedFiles.length === 0;
}

function setStage(stageId) {
  if (!stages.some((stage) => stage.id === stageId)) return;
  state.currentStage = stageId;
  document.querySelectorAll("[data-stage-panel]").forEach((panel) => {
    panel.hidden = panel.getAttribute("data-stage-panel") !== stageId;
  });
  renderStageNav();
  renderWorkspaceSummary();
  renderFiles();
  renderLayoutWorkbench();
  updatePreview();
}

function computeStageStatus(stageId, input = readForm()) {
  if (stageId === "activity") {
    if (!input.date && !input.summary && input.strava_links.length === 0) return "未填";
    if (!input.privacy_notes) return "有风险";
    return "已填";
  }
  if (stageId === "materials") {
    if (state.files.length === 0) return "未填";
    return state.deletedFiles.length > 0 ? "有风险" : "已填";
  }
  if (stageId === "layout") {
    if (state.files.length === 0) return "未填";
    return state.files.some((file) => file.layout_role === "unused" || !file.caption) ? "有风险" : "已填";
  }
  if (stageId === "copy") {
    if (!input.copy.title && !input.copy.intro && !input.copy.body_points && !input.copy.outro && !input.ai_instruction) return "未填";
    return !input.ai_instruction ? "有风险" : "已填";
  }
  if (stageId === "preview") {
    return state.confirmed ? "已确认" : "有风险";
  }
  return "未填";
}

function renderStageNav() {
  const input = readForm();
  stageNav.querySelectorAll("[data-stage]").forEach((button) => {
    const stageId = button.getAttribute("data-stage");
    const stage = stages.find((item) => item.id === stageId);
    const status = computeStageStatus(stageId, input);
    button.classList.toggle("active", stageId === state.currentStage);
    button.setAttribute("aria-current", stageId === state.currentStage ? "step" : "false");
    button.setAttribute("data-stage-status", status);
    button.innerHTML = `<strong>${escapeHtml(stage.label)}</strong><span>${escapeHtml(status)}</span>`;
  });
}

function renderWorkspaceSummary() {
  const input = readForm();
  const usedFiles = state.files.filter((file) => file.layout_role !== "unused").length;
  const captionedFiles = state.files.filter((file) => file.caption).length;
  workspaceSummary.innerHTML = `
    <h2>工作台摘要</h2>
    <dl>
      <div><dt>日期</dt><dd>${escapeHtml(input.date || "未填")}</dd></div>
      <div><dt>栏目</dt><dd>${escapeHtml(kindLabel(input.kind))}</dd></div>
      <div><dt>素材</dt><dd>${state.files.length} 个在用 / ${state.deletedFiles.length} 个已删</dd></div>
      <div><dt>版位</dt><dd>${usedFiles} 个参与排版</dd></div>
      <div><dt>说明</dt><dd>${captionedFiles} 个已写图片说明</dd></div>
      <div><dt>确认</dt><dd>${state.confirmed ? "已确认" : "未确认"}</dd></div>
    </dl>
  `;
}

function buildDraftMarkdown(input = readForm()) {
  const lines = [
    `# ${input.date || "未填日期"} ${kindLabel(input.kind)} 素材初稿`,
    "",
    "## 标题方向",
    input.title_hint || "-",
    "",
    "## 文案工作台",
    `- 成稿标题：${input.copy.title || "-"}`,
    `- 导语：${input.copy.intro || "-"}`,
    "### 正文要点",
    ...(toLines(input.copy.body_points).length ? toLines(input.copy.body_points).map((line) => `- ${line}`) : ["-"]),
    `- 结尾文案：${input.copy.outro || "-"}`,
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
    "## 全局 AI 要求",
    input.ai_instruction || "-",
    "",
    "## 素材清单",
    ...state.files.map((file, index) => {
      const size = file.width && file.height ? `${file.width}x${file.height}` : "尺寸未知";
      return [
        `- ${index + 1}. ${file.filename}`,
        `  - 版位：${layoutRoleLabel(file.layout_role)}`,
        `  - 文件：${file.relative_path} (${size}, ${formatBytes(file.size)})`,
        `  - 图片说明：${file.caption || "待补充"}`,
        `  - 素材备注：${file.note || "待补充"}`,
        `  - AI 批注：${file.ai_instruction || "待补充"}`,
      ].join("\n");
    }),
    "",
    "## 已确认实现要求",
    "后续实现必须按 preview/preview.html 中确认的文案、素材顺序、版位、图片说明、AI 批注和隐私提醒执行。",
    "",
    "## 本地流水线",
    `python3 pipeline.py collect --date ${input.date || "0913"} --kind ${input.kind || "ribao"}`,
    `python3 pipeline.py generate --date ${input.date || "0913"} --kind ${input.kind || "ribao"}`,
  ];
  return lines.join("\n");
}

function buildManifest(input = readForm()) {
  return {
    schema_version: 2,
    generated_at: new Date().toISOString(),
    implementation_contract: buildImplementationContract(),
    privacy: {
      local_only: true,
      uploads_files: false,
      preserve_original_bytes: true,
      note: "manifest 记录文件元数据和用户备注；完整 ZIP 会把原始素材文件按原字节写入 materials/original/。",
    },
    article: input,
    materials: state.files.map((file, index) => materialRecord(file, index)),
  };
}

function buildLayoutJson(input = readForm()) {
  return {
    schema_version: 1,
    generated_at: new Date().toISOString(),
    date: input.date,
    kind: input.kind,
    implementation_contract: buildImplementationContract(),
    copy: input.copy,
    sections: state.files.map((file, index) => ({
      order: index + 1,
      filename: file.filename,
      package_path: `materials/original/${file.relative_path}`,
      layout_role: file.layout_role,
      layout_role_label: layoutRoleLabel(file.layout_role),
      caption: file.caption,
      note: file.note,
      ai_instruction: file.ai_instruction,
    })),
  };
}

function buildInstructionsMarkdown(input = readForm()) {
  const lines = [
    `# ${input.date || "未填日期"} ${kindLabel(input.kind)} 实现说明`,
    "",
    "## 实现契约",
    "后续实现必须按 `preview/preview.html` 中确认的文案、素材顺序、版位、图片说明、AI 批注和隐私提醒执行。",
    "",
    "## 文案",
    `- 成稿标题：${input.copy.title || input.title_hint || "-"}`,
    `- 导语：${input.copy.intro || "-"}`,
    "### 正文要点",
    ...(toLines(input.copy.body_points).length ? toLines(input.copy.body_points).map((line) => `- ${line}`) : ["-"]),
    `- 结尾文案：${input.copy.outro || "-"}`,
    "",
    "## 全局 AI 要求",
    input.ai_instruction || "-",
    "",
    "## 隐私注意事项",
    input.privacy_notes || "-",
    "",
    "## 素材执行表",
    ...state.files.map((file, index) => {
      return [
        `### ${index + 1}. ${file.filename}`,
        `- 路径：materials/original/${file.relative_path}`,
        `- 版位：${layoutRoleLabel(file.layout_role)}`,
        `- 图片说明：${file.caption || "-"}`,
        `- 素材备注：${file.note || "-"}`,
        `- AI 批注：${file.ai_instruction || "-"}`,
      ].join("\n");
    }),
    "",
    "## 本地流水线",
    `python3 pipeline.py collect --date ${input.date || "0913"} --kind ${input.kind || "ribao"}`,
    `python3 pipeline.py generate --date ${input.date || "0913"} --kind ${input.kind || "ribao"}`,
  ];
  return lines.join("\n");
}

function buildPreviewHtml(input = readForm()) {
  const materials = state.files
    .map((file, index) => {
      const size = file.width && file.height ? `${file.width}x${file.height}` : "尺寸未知";
      const image = file.type.startsWith("image/")
        ? `<img src="../materials/original/${escapeHtml(file.relative_path)}" alt="${escapeHtml(file.caption || file.filename)}" />`
        : `<div class="thumb-placeholder">无图片预览</div>`;
      return `
        <article class="material">
          ${image}
          <div>
            <p class="role">${index + 1}. ${escapeHtml(layoutRoleLabel(file.layout_role))}</p>
            <h2>${escapeHtml(file.filename)}</h2>
            <p>${escapeHtml(file.caption || "未填写图片说明")}</p>
            <dl>
              <dt>路径</dt><dd>materials/original/${escapeHtml(file.relative_path)}</dd>
              <dt>尺寸</dt><dd>${escapeHtml(size)}</dd>
              <dt>备注</dt><dd>${escapeHtml(file.note || "-")}</dd>
              <dt>AI 批注</dt><dd>${escapeHtml(file.ai_instruction || "-")}</dd>
            </dl>
          </div>
        </article>`;
    })
    .join("");

  return `<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>${escapeHtml(input.date || "未填日期")} ${escapeHtml(kindLabel(input.kind))} 实现预览</title>
    <style>
      body { margin: 0; background: #63c7ff; color: #111; font-family: Helvetica, Arial, sans-serif; }
      main { max-width: 960px; margin: 0 auto; padding: 28px; }
      header, section, .material { border: 4px solid #111; background: #fff; padding: 20px; margin-bottom: 18px; box-shadow: 7px 7px 0 #111; }
      h1 { margin: 0 0 10px; font-size: 42px; line-height: 1; }
      h2 { margin: 0 0 8px; }
      .label, .role { font-weight: 900; background: #ffe04f; display: inline-block; padding: 4px 8px; border: 2px solid #111; }
      .material { display: grid; grid-template-columns: 220px 1fr; gap: 18px; }
      img, .thumb-placeholder { width: 100%; aspect-ratio: 4 / 3; object-fit: cover; border: 3px solid #111; background: #f5f1e8; }
      dt { font-weight: 900; }
      dd { margin: 0 0 8px; }
      @media (max-width: 720px) { .material { grid-template-columns: 1fr; } }
    </style>
  </head>
  <body>
    <main>
      <header>
        <p class="label">确认预览</p>
        <h1>${escapeHtml(input.copy.title || input.title_hint || `${input.date || "未填日期"} ${kindLabel(input.kind)}`)}</h1>
        <p>${escapeHtml(input.copy.intro || input.summary || "未填写导语")}</p>
      </header>
      <section>
        <h2>正文要点</h2>
        ${htmlList(toLines(input.copy.body_points))}
        <h2>结尾文案</h2>
        <p>${escapeHtml(input.copy.outro || "-")}</p>
      </section>
      <section>
        <h2>全局 AI 要求</h2>
        <p>${escapeHtml(input.ai_instruction || "-")}</p>
        <h2>隐私注意事项</h2>
        <p>${escapeHtml(input.privacy_notes || "-")}</p>
      </section>
      <section>
        <h2>素材顺序与版位</h2>
        ${materials || "<p>未选择素材。</p>"}
      </section>
    </main>
  </body>
</html>`;
}

async function buildZipPackage(input = readForm()) {
  const root = packageRoot(input);
  const entries = [
    {
      path: `${root}/draft.md`,
      data: encodeText(buildDraftMarkdown(input)),
    },
    {
      path: `${root}/instructions.md`,
      data: encodeText(buildInstructionsMarkdown(input)),
    },
    {
      path: `${root}/layout.json`,
      data: encodeText(JSON.stringify(buildLayoutJson(input), null, 2)),
    },
    {
      path: `${root}/manifest.json`,
      data: encodeText(JSON.stringify(buildManifest(input), null, 2)),
    },
    {
      path: `${root}/preview/preview.html`,
      data: encodeText(buildPreviewHtml(input)),
    },
    {
      path: `${root}/README.txt`,
      data: encodeText(
        [
          "辛庄桥小火车素材采集包",
          "",
          "结构：",
          "- draft.md：给流水线使用的素材初稿",
          "- instructions.md：给制作人员和 AI 的确认后执行要求",
          "- layout.json：素材顺序、版位和批注的结构化契约",
          "- manifest.json：结构化素材清单",
          "- preview/preview.html：用户确认过的实现预览",
          "- materials/original/：原始素材文件，保持原文件字节和原图像素",
          "",
          "后续实现必须按 preview/preview.html 中确认的文案、素材顺序、版位、图片说明、AI 批注和隐私提醒执行。",
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

function materialRecord(file, index) {
  return {
    order: index + 1,
    filename: file.filename,
    relative_path: file.relative_path,
    package_path: `materials/original/${file.relative_path}`,
    type: file.type,
    size: file.size,
    width: file.width,
    height: file.height,
    layout_role: file.layout_role,
    layout_role_label: layoutRoleLabel(file.layout_role),
    caption: file.caption,
    note: file.note,
    ai_instruction: file.ai_instruction,
    preserve_original_bytes: true,
  };
}

function buildImplementationContract() {
  return {
    source_of_truth: "preview/preview.html",
    must_follow: [
      "copy",
      "material_order",
      "layout_role",
      "caption",
      "ai_instruction",
      "privacy_notes",
    ],
    preserve_original_bytes: true,
  };
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
  renderVisualPreview();
  renderStageNav();
  renderWorkspaceSummary();
  updateExportState();
}

function renderVisualPreview() {
  const input = readForm();
  const bodyPoints = htmlList(toLines(input.copy.body_points));
  const materialList = state.files
    .map((file, index) => {
      const image = file.preview_url
        ? `<img src="${escapeHtml(file.preview_url)}" alt="${escapeHtml(file.filename)} 预览" />`
        : `<div class="preview-thumb placeholder">无预览</div>`;
      return `
        <article class="preview-item">
          ${image}
          <div>
            <span>${index + 1}. ${escapeHtml(layoutRoleLabel(file.layout_role))}</span>
            <strong>${escapeHtml(file.caption || file.filename)}</strong>
            <p>${escapeHtml(file.note || "暂无素材备注")}</p>
            <p>${escapeHtml(file.ai_instruction || "暂无单图 AI 批注")}</p>
          </div>
        </article>`;
    })
    .join("");

  visualPreview.innerHTML = `
    <div class="preview-copy">
      <span>确认预览</span>
      <h3>${escapeHtml(input.copy.title || input.title_hint || "未填写标题")}</h3>
      <p>${escapeHtml(input.copy.intro || input.summary || "未填写导语")}</p>
      <strong>正文要点</strong>
      ${bodyPoints}
      <p>${escapeHtml(input.copy.outro || "未填写结尾文案")}</p>
    </div>
    <div class="preview-rules">
      <strong>AI 要求</strong>
      <p>${escapeHtml(input.ai_instruction || "未填写全局 AI 要求")}</p>
      <strong>隐私注意事项</strong>
      <p>${escapeHtml(input.privacy_notes || "未填写隐私注意事项")}</p>
    </div>
    <div class="preview-materials">
      ${materialList || "<p>未选择素材。</p>"}
    </div>
  `;
}

function moveFile(id, direction) {
  const index = state.files.findIndex((file) => file.id === id);
  if (index < 0) return;
  const targetIndex = index + direction;
  if (targetIndex < 0 || targetIndex >= state.files.length) return;
  const [item] = state.files.splice(index, 1);
  state.files.splice(targetIndex, 0, item);
  markUnconfirmed();
  renderFiles();
  renderLayoutWorkbench();
  updatePreview();
}

function deleteFile(id) {
  const index = state.files.findIndex((file) => file.id === id);
  if (index < 0) return;
  const [item] = state.files.splice(index, 1);
  state.deletedFiles.push({ deleted_at: new Date().toISOString(), index, item });
  markUnconfirmed();
  renderFiles();
  renderLayoutWorkbench();
  renderDeletedZone();
  updatePreview();
}

function undoDelete() {
  const deleted = state.deletedFiles.pop();
  if (!deleted) return;
  const index = Math.min(deleted.index, state.files.length);
  state.files.splice(index, 0, deleted.item);
  markUnconfirmed();
  renderFiles();
  renderLayoutWorkbench();
  renderDeletedZone();
  updatePreview();
}

function updateExportState() {
  zipButton.disabled = !state.confirmed || state.currentStage !== "preview";
  zipButton.textContent = state.confirmed && state.currentStage === "preview" ? "导出完整素材包 ZIP" : "确认预览后导出 ZIP";
}

function markUnconfirmed() {
  state.confirmed = false;
  confirmExport.checked = false;
}

function updateFileField(id, field, value) {
  const target = state.files.find((file) => file.id === id);
  if (!target) return;
  target[field] = value;
  markUnconfirmed();
  updatePreview();
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

function layoutRoleLabel(role) {
  const found = layoutRoles.find(([value]) => value === role);
  return found ? found[1] : role;
}

function defaultLayoutRole(index) {
  if (index === 0) return "cover";
  if (index === 1) return "hero";
  return "body";
}

function toLines(value) {
  return String(value || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

function htmlList(items) {
  if (!items.length) return "<p>-</p>";
  return `<ul>${items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`;
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
  return (
    String(value)
      .trim()
      .replace(/[\\/:*?"<>|]+/g, "-")
      .replace(/\s+/g, "-")
      .replace(/^-+|-+$/g, "") || "xiaohuoche"
  );
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
  const id = input.getAttribute("data-file-id");
  const field = input.getAttribute("data-file-field");
  if (!id || !field) return;
  updateFileField(id, field, input.value);
});

layoutWorkbench.addEventListener("input", (event) => {
  const input = event.target;
  const id = input.getAttribute("data-file-id");
  const field = input.getAttribute("data-file-field");
  if (!id || !field) return;
  updateFileField(id, field, input.value);
});

fileList.addEventListener("change", (event) => {
  const input = event.target;
  const stageId = input.getAttribute("data-stage");
  if (stageId) setStage(stageId);
});

layoutWorkbench.addEventListener("change", (event) => {
  const input = event.target;
  const id = input.getAttribute("data-file-id");
  const field = input.getAttribute("data-file-field");
  if (!id || !field) return;
  updateFileField(id, field, input.value);
});

fileList.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-file-action]");
  const stageButton = event.target.closest("button[data-stage]");
  if (stageButton) {
    setStage(stageButton.getAttribute("data-stage"));
    return;
  }
  if (!button) return;
  const id = button.getAttribute("data-file-id");
  const action = button.getAttribute("data-file-action");
  if (action === "delete") deleteFile(id);
});

layoutWorkbench.addEventListener("click", (event) => {
  const stageButton = event.target.closest("button[data-stage]");
  if (stageButton) {
    setStage(stageButton.getAttribute("data-stage"));
    return;
  }
  const button = event.target.closest("button[data-file-action]");
  if (!button) return;
  const id = button.getAttribute("data-file-id");
  const action = button.getAttribute("data-file-action");
  if (action === "move-up") moveFile(id, -1);
  if (action === "move-down") moveFile(id, 1);
});

undoDeleteButton.addEventListener("click", undoDelete);

stageNav.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-stage]");
  if (!button) return;
  setStage(button.getAttribute("data-stage"));
});

prevStageButton.addEventListener("click", () => {
  const index = stages.findIndex((stage) => stage.id === state.currentStage);
  setStage(stages[Math.max(0, index - 1)].id);
});

nextStageButton.addEventListener("click", () => {
  const index = stages.findIndex((stage) => stage.id === state.currentStage);
  setStage(stages[Math.min(stages.length - 1, index + 1)].id);
});

form.addEventListener("input", (event) => {
  if (event.target.hasAttribute("data-file-field")) {
    return;
  }
  if (event.target === confirmExport) {
    state.confirmed = confirmExport.checked && state.currentStage === "preview";
  } else {
    markUnconfirmed();
  }
  updatePreview();
});

draftButton.addEventListener("click", () => {
  const input = readForm();
  downloadText(`${input.date || "xiaohuoche"}-${input.kind || "ribao"}-draft.md`, buildDraftMarkdown(input), "text/markdown;charset=utf-8");
});

zipButton.addEventListener("click", async () => {
  if (!state.confirmed) return;
  const input = readForm();
  zipButton.disabled = true;
  zipButton.textContent = "正在打包...";
  try {
    const blob = await buildZipPackage(input);
    downloadBlob(`${packageRoot(input)}-materials.zip`, blob);
  } finally {
    updateExportState();
  }
});

manifestButton.addEventListener("click", () => {
  const input = readForm();
  const manifest = JSON.stringify(buildManifest(input), null, 2);
  downloadText(`${input.date || "xiaohuoche"}-${input.kind || "ribao"}-manifest.json`, manifest, "application/json;charset=utf-8");
});

window.addEventListener("beforeunload", releasePreviewUrls);

renderFiles();
renderLayoutWorkbench();
renderDeletedZone();
setStage(state.currentStage);
