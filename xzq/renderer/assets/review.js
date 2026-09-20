(() => {
  'use strict';
  const config = window.XZQ_REVIEW || {};
  const apiPath = path => `${config.apiBase || ''}${path}`;
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const clone = value => JSON.parse(JSON.stringify(value));
  const same = (left, right) => JSON.stringify(left) === JSON.stringify(right);
  const storageKey = `xzq-review:${config.articleId || location.pathname}`;
  const queueKey = `${storageKey}:pending`;
  const recentKey = `${storageKey}:recent`;
  const quickTags = ['错字', '图片不合适', '遮挡人物', '留白过多', '需要补充'];
  let state = {schemaVersion: 1, pageId: config.articleId, revision: 0, sessionState: 'reviewing', generalNote: '', annotations: []};
  let serverState = clone(state);
  let operations = [];
  let checklist = [];
  let lastStatus = null;
  let filter = 'open';
  let pending = null;
  let selected = -1;
  let activeRevision = '';
  let saveTimer = 0;
  let statusTail = Promise.resolve();
  let checklistVersion = 0;
  let draining = null;
  let dirty = false;
  let online = navigator.onLine;
  let activeConflict = null;
  let deleted = null;
  let toggled = null;
  let undoTimer = 0;
  let approvalTrigger = null;
  let approvalBackground = [];
  let drawerSnap = 'peek';
  let drawerView = 'opinions';
  let lastScrollY = scrollY;
  let scrollDirection = 0;
  let scrollDistance = 0;
  const expandedClusters = new Set();

  function announce(message, kind = 'saved') {
    const node = $('#reviewStatus');
    node.textContent = message; node.dataset.state = kind;
  }
  function persist() {
    try {
      localStorage.setItem(storageKey, JSON.stringify(state));
      localStorage.setItem(queueKey, JSON.stringify(operations));
    } catch (_) { announce('离线修改已暂存', 'offline'); }
  }
  function setDirty() { dirty = operations.length > 0; persist(); }
  function setOnline(value) {
    online = value;
    document.body.classList.toggle('review-offline', !online);
    $$('[data-online-action]').forEach(node => { node.disabled = !online; node.setAttribute('aria-disabled', String(!online)); });
    $('#reviewOfflineActions').hidden = online && !operations.some(item => item.failed);
    if (!online) announce('离线修改已暂存', 'offline');
    else if (operations.length) { announce('正在保存…', 'syncing'); drainQueue().catch(() => {}); }
    else announce('已同步', 'saved');
    renderChecklist();
  }
  async function request(path, options = {}) {
    const response = await fetch(apiPath(path), {headers: {'Content-Type': 'application/json'}, ...options});
    if (!response.ok) {
      const error = new Error((await response.json().catch(() => ({}))).error || `HTTP ${response.status}`);
      error.status = response.status; throw error;
    }
    return response.json();
  }
  function operationId() { return crypto.randomUUID?.() || `op-${Date.now()}-${Math.random()}`; }
  function enqueue(type, payload, base = null) {
    const operation = {id: operationId(), type, payload: clone(payload), base: clone(base), revision: state.revision, createdAt: new Date().toISOString(), failed: ''};
    operations.push(operation); setDirty(); announce(online ? '正在保存…' : '离线修改已暂存', online ? 'syncing' : 'offline');
    if (online) drainQueue().catch(() => {});
    return operation;
  }
  function applyOperation(target, operation) {
    const next = clone(target);
    if (operation.type === 'annotation-upsert') {
      const index = next.annotations.findIndex(item => item.id === operation.payload.id);
      if (index < 0) next.annotations.push(clone(operation.payload)); else next.annotations[index] = clone(operation.payload);
    } else if (operation.type === 'annotation-delete') {
      next.annotations = next.annotations.filter(item => item.id !== operation.payload.id);
    } else if (operation.type === 'general-note') next.generalNote = operation.payload;
    else if (operation.type === 'session') next.sessionState = operation.payload;
    else if (operation.type === 'replace') return {...clone(operation.payload), revision: next.revision, pageId: next.pageId};
    return next;
  }
  function materialize(remote) { return operations.reduce(applyOperation, clone(remote)); }
  function annotationConflict(remote, operation) {
    if (!operation.type.startsWith('annotation-')) return null;
    const remoteItem = remote.annotations.find(item => item.id === operation.payload.id) || null;
    if (operation.type === 'annotation-upsert') {
      if (same(remoteItem, operation.payload)) return null;
      if (!same(remoteItem, operation.base)) return {operation, mine: operation.payload, remote: remoteItem};
    } else if (remoteItem && !same(remoteItem, operation.base)) return {operation, mine: null, remote: remoteItem};
    return null;
  }
  function showConflict(conflict) {
    activeConflict = conflict;
    const host = $('#reviewConflictList'); host.replaceChildren();
    const item = document.createElement('div'); item.className = 'review-conflict-item';
    const title = document.createElement('b'); title.textContent = `批注 ${conflict.operation.payload.id}`;
    const versions = document.createElement('div'); versions.className = 'review-conflict-versions';
    [['mine', '保留我的版本', conflict.mine], ['remote', '采用远端版本', conflict.remote]].forEach(([choice, label, value]) => {
      const button = document.createElement('button'); button.type = 'button'; button.dataset.choice = choice;
      button.innerHTML = `<b>${label}</b><small></small>`; button.querySelector('small').textContent = value?.text || '删除此批注';
      button.onclick = () => resolveConflict(choice); versions.append(button);
    });
    item.append(title, versions); host.append(item); $('#reviewConflicts').hidden = false;
    setDrawer('full', 'opinions'); announce('冲突', 'error');
  }
  function resolveConflict(choice) {
    if (!activeConflict) return;
    const operation = activeConflict.operation;
    if (choice === 'remote') operations = operations.filter(item => item.id !== operation.id);
    else operation.base = clone(activeConflict.remote);
    activeConflict = null; $('#reviewConflicts').hidden = true; state = materialize(serverState); setDirty(); render(); drainQueue().catch(() => {});
  }
  async function drainQueue() {
    if (draining) return draining;
    if (!online || !operations.length || activeConflict) return Promise.resolve();
    draining = (async () => {
      while (online && operations.length && !activeConflict) {
        const operation = operations[0]; operation.failed = '';
        try {
          const remote = await request('/api/review');
          const conflict = annotationConflict(remote, operation);
          if (conflict) { serverState = remote; showConflict(conflict); break; }
          let candidate = applyOperation(remote, operation); candidate.revision = operation.revision ?? remote.revision;
          if (same(candidate.annotations, remote.annotations) && candidate.generalNote === remote.generalNote && candidate.sessionState === remote.sessionState) {
            serverState = remote;
          } else serverState = await request('/api/review', {method: 'PUT', body: JSON.stringify(candidate)});
          operations.shift(); state = materialize(serverState); setDirty(); render();
        } catch (error) {
          operation.failed = error.message;
          persist();
          if (!navigator.onLine || !online || !error.status) setOnline(false);
          else if (error.status === 409) { operation.revision = null; persist(); continue; }
          else { $('#reviewOfflineActions').hidden = false; announce('离线修改已暂存', 'offline'); }
          throw error;
        }
      }
      if (!operations.length && !activeConflict) { dirty = false; persist(); announce('已同步', 'saved'); }
    })().finally(() => { draining = null; });
    return draining;
  }
  async function syncBeforeAction() {
    if (!online) throw new Error('离线时不能执行此操作');
    if (saveTimer) { clearTimeout(saveTimer); saveTimer = 0; enqueue('general-note', state.generalNote, serverState.generalNote); }
    await drainQueue();
    if (activeConflict) throw new Error('请先处理保存冲突');
    if (operations.length) throw new Error('仍有修改未同步，请重试');
  }
  function anchorTarget(anchor) { return document.querySelector(`[data-review-anchor="${CSS.escape(anchor)}"]`); }
  function anchorLabel(anchor) { return anchorTarget(anchor)?.dataset.reviewLabel || anchor; }
  function visibleItems() { return state.annotations.filter(item => filter === 'all' || item.status === filter); }
  function locateItem(item) {
    setDrawer('half', 'opinions');
    const card = $(`.review-card[data-id="${CSS.escape(item.id)}"]`); card?.focus({preventScroll: true});
    const target = anchorTarget(item.anchor);
    if (target) { target.scrollIntoView({behavior: 'smooth', block: 'start'}); target.classList.add('review-target-active'); setTimeout(() => target.classList.remove('review-target-active'), 1200); }
  }
  function select(index) {
    const items = visibleItems(); if (!items.length) return;
    selected = (index + items.length) % items.length; locateItem(items[selected]);
    $$('.review-card').forEach((card, i) => card.classList.toggle('active', i === selected));
  }
  function edit(item) {
    const text = prompt('编辑批注', item.text); if (text === null || !text.trim()) return;
    const before = clone(item); item.text = text.trim(); item.updatedAt = new Date().toISOString();
    enqueue('annotation-upsert', item, before); render();
  }
  function deleteItem(item) {
    if (!confirm('确认删除这条批注？删除后 5 秒内可撤销。')) return;
    const index = state.annotations.findIndex(value => value.id === item.id); if (index < 0) return;
    deleted = {item: clone(item), index}; toggled = null; state.annotations.splice(index, 1); enqueue('annotation-delete', item, item);
    showUndo('批注已删除'); render();
  }
  function showUndo(message) {
    clearTimeout(undoTimer); const host = $('#reviewUndo'); host.firstChild.nodeValue = `${message} `; host.hidden = false;
    undoTimer = setTimeout(() => { deleted = null; toggled = null; host.hidden = true; }, 5000);
  }
  function mutate(item, action, undoable = false) {
    if (action === 'delete') return deleteItem(item);
    if (action === 'edit') return edit(item);
    const before = clone(item); item.status = item.status === 'open' ? 'resolved' : 'open'; item.updatedAt = new Date().toISOString();
    enqueue('annotation-upsert', item, before);
    if (undoable) { deleted = null; toggled = {id: item.id, before}; showUndo(item.status === 'resolved' ? '批注已解决' : '批注已重开'); }
    render();
  }
  function attachSwipe(card, item) {
    const directionRatio = 1.35; const commitDistance = 72;
    let startX = 0; let startY = 0; let startTime = 0; let tracking = false;
    card.addEventListener('pointerdown', event => { if (event.pointerType === 'mouse') return; startX = event.clientX; startY = event.clientY; startTime = performance.now(); tracking = true; });
    card.addEventListener('pointermove', event => {
      if (!tracking) return; const dx = event.clientX - startX; const dy = event.clientY - startY;
      if (Math.abs(dy) > Math.abs(dx) * 0.9 && Math.abs(dy) > 12) { tracking = false; card.classList.remove('swipe-left', 'swipe-right'); return; }
      const horizontal = Math.abs(dx) > Math.abs(dy) * directionRatio;
      card.classList.toggle('swipe-right', horizontal && dx > 35); card.classList.toggle('swipe-left', horizontal && dx < -35);
    });
    card.addEventListener('pointerup', event => {
      if (!tracking) return; tracking = false; const dx = event.clientX - startX; const dy = event.clientY - startY; const duration = performance.now() - startTime; card.classList.remove('swipe-left', 'swipe-right');
      if (duration > 1500 || Math.abs(dx) < commitDistance || Math.abs(dx) <= Math.abs(dy) * directionRatio) return;
      if (dx > 0) mutate(item, 'toggle', true); else deleteItem(item);
    });
    card.addEventListener('pointercancel', () => { tracking = false; card.classList.remove('swipe-left', 'swipe-right'); });
  }
  function renderMarkers(items) {
    $$('.annotation-marker').forEach(node => node.remove());
    const groups = new Map();
    items.forEach((item, index) => {
      const key = `${item.anchor}:${Math.round(item.position.x / 8)}:${Math.round(item.position.y / 8)}`;
      if (!groups.has(key)) groups.set(key, []); groups.get(key).push({item, index});
    });
    groups.forEach((entries, key) => {
      const target = anchorTarget(entries[0].item.anchor); if (!target) return;
      if (entries.length > 1 && !expandedClusters.has(key)) {
        const marker = document.createElement('button'); marker.type = 'button'; marker.className = 'annotation-marker cluster'; marker.dataset.exportIgnore = 'annotation';
        marker.style.left = `${entries[0].item.position.x}%`; marker.style.top = `${entries[0].item.position.y}%`; marker.textContent = String(entries.length); marker.setAttribute('aria-label', `${entries.length} 条相邻批注，展开`);
        marker.onclick = event => { event.stopPropagation(); expandedClusters.add(key); render(); locateItem(entries[0].item); }; target.append(marker); return;
      }
      entries.forEach(({item, index}, offset) => {
        const marker = document.createElement('button'); marker.type = 'button'; marker.className = `annotation-marker ${item.status}`; marker.dataset.exportIgnore = 'annotation';
        marker.style.left = `${Math.min(98, item.position.x + offset * 4)}%`; marker.style.top = `${Math.min(98, item.position.y + offset * 4)}%`; marker.textContent = String(index + 1); marker.title = item.text; marker.setAttribute('aria-label', `批注 ${index + 1}：${item.text}`);
        marker.onclick = event => { event.stopPropagation(); select(index); }; target.append(marker);
      });
    });
  }
  function render() {
    const list = $('#reviewList'); list.replaceChildren(); const items = visibleItems(); renderMarkers(items);
    const openCount = state.annotations.filter(item => item.status === 'open').length; $('#reviewCount').textContent = `${openCount} 条待处理`; $('#mobileOpinionCount').textContent = String(openCount);
    $$('.review-filter').forEach(button => button.classList.toggle('active', button.dataset.filter === filter));
    if (!items.length) { const empty = document.createElement('p'); empty.className = 'review-empty'; empty.textContent = filter === 'open' ? '没有待处理意见。可长按正文或开启批注模式添加。' : '当前筛选下没有意见。'; list.append(empty); }
    items.forEach((item, index) => {
      const target = anchorTarget(item.anchor); const card = document.createElement('article'); card.tabIndex = 0;
      card.className = `review-card ${item.status}${target ? '' : ' stale'}`; card.dataset.anchor = item.anchor; card.dataset.id = item.id;
      const number = document.createElement('span'); number.className = 'review-index'; number.textContent = String(index + 1);
      const content = document.createElement('div'); const text = document.createElement('p'); text.textContent = item.text;
      const meta = document.createElement('small'); meta.textContent = `${target ? anchorLabel(item.anchor) : '失效定位'} · ${item.status === 'open' ? '待处理' : '已解决'} · ${new Date(item.updatedAt).toLocaleString()}`;
      content.append(text, meta); card.append(number, content);
      const actions = document.createElement('div'); actions.className = 'review-card-actions';
      [['edit', '编辑'], ['toggle', item.status === 'open' ? '解决' : '重开'], ['delete', '删除']].forEach(([action, label]) => { const button = document.createElement('button'); button.type = 'button'; button.textContent = label; button.onclick = event => { event.stopPropagation(); mutate(item, action); }; actions.append(button); });
      card.append(actions); card.onclick = event => { if (!event.target.closest('button')) target?.scrollIntoView({behavior: 'smooth', block: 'center'}); }; attachSwipe(card, item); list.append(card);
    });
    const note = $('#reviewGeneralNote'); if (document.activeElement !== note) note.value = state.generalNote || '';
    renderSummary();
  }
  function renderChecklist() {
    const pendingHost = $('#reviewChecklistPending'); const doneHost = $('#reviewChecklistDone'); pendingHost.replaceChildren(); doneHost.replaceChildren();
    const completed = checklist.filter(item => item.checked).length; const progress = `${completed}/${checklist.length}`;
    $('#reviewChecklistProgress').textContent = progress; $('#mobileChecklistProgress').textContent = progress; $('#reviewChecklistCompletedCount').textContent = String(completed);
    checklist.forEach((item, index) => {
      const label = document.createElement('label'); label.className = 'review-check-item'; label.dataset.index = String(index);
      const input = document.createElement('input'); input.type = 'checkbox'; input.checked = item.checked; input.disabled = !online;
      input.setAttribute('aria-label', item.text); input.onchange = () => saveChecklist(index, input.checked);
      label.append(input, document.createTextNode(item.text)); (item.checked ? doneHost : pendingHost).append(label);
    });
    $('#reviewChecklistEmpty').hidden = checklist.length > 0; $('#reviewChecklistCompleted').hidden = completed === 0;
    $('#reviewCheckReason').hidden = !(lastStatus?.articleState === 'draft' && ['revised', 'changes_requested'].includes(lastStatus?.sessionState) && checklist.some(item => !item.checked));
    renderSummary();
  }
  function saveChecklist(index, checked) {
    const previous = checklist.map(item => item.checked); checklist[index].checked = checked;
    const completed = checklist.filter(item => item.checked).length; const progress = `${completed}/${checklist.length}`;
    $('#reviewChecklistProgress').textContent = progress; $('#mobileChecklistProgress').textContent = progress; renderSummary();
    const desired = checklist.map(item => item.checked); const version = ++checklistVersion;
    statusTail = statusTail.catch(() => {}).then(async () => {
      try { const status = await request('/api/checklist', {method: 'POST', body: JSON.stringify({checked: desired})}); if (version === checklistVersion) { applyStatus(status); announce('已同步', 'saved'); } }
      catch (error) { if (version === checklistVersion) { checklist.forEach((item, position) => { item.checked = previous[position]; }); renderChecklist(); announce('离线修改已暂存', 'offline'); } }
    });
  }
  function renderSummary() {
    const completed = checklist.filter(item => item.checked).length; const openCount = state.annotations.filter(item => item.status === 'open').length;
    const revisionLabel = `r${state.revision}`;
    const productLabel = lastStatus?.exportCurrent ? '成品为当前版本' : '需要重新导出';
    const rows = [[openCount ? '⚠ 待处理意见' : '✓ 批注意见', `${openCount} 条待处理`], [completed === checklist.length ? '✓ 发布检查' : '⚠ 发布检查', `${completed}/${checklist.length}`], ['当前版本', revisionLabel], ['成品状态', productLabel], [online ? '✓ 网络状态' : '⚠ 网络状态', online ? '在线' : '离线']];
    [$('#approvalSummary'), $('#approvalDialogSummary')].forEach(host => { if (!host) return; host.replaceChildren(); rows.forEach(([label, value]) => { const row = document.createElement('div'); row.className = 'review-summary-row'; const left = document.createElement('b'); left.textContent = label; const right = document.createElement('span'); right.textContent = value; row.append(left, right); host.append(row); }); });
    const allowed = online && openCount === 0 && completed === checklist.length && config.gatePassed !== false;
    $('#approveReview').disabled = !online; $('#confirmApproval').disabled = !allowed;
  }
  function applyStatus(status) {
    lastStatus = status; config.gatePassed = status.gate.passed; checklist = status.checklist || [];
    $('#reviewVersion').textContent = `r${status.reviewRevision}`;
    $('#reviewTitle').textContent = (status.articleId || config.articleId || '日报').replace(/-ribao$/, ' 日报');
    const gate = $('#reviewGate'); gate.classList.toggle('fail', !status.gate.passed); gate.textContent = status.gate.passed ? (status.exportCurrent ? '✓ 门禁通过 · 成品为当前版本' : '✓ 门禁通过 · 需生成新成品') : `⚠ 门禁未通过：${status.gate.reasons.join('；')}`;
    $('#downloadProduct').textContent = status.exportCurrent ? '下载成品' : '生成成品'; $('#downloadProduct').dataset.current = String(status.exportCurrent); renderChecklist(); render();
  }
  function refreshStatus() {
    statusTail = statusTail.catch(() => {}).then(async () => { try { applyStatus(await request('/api/status')); } catch (_) { if (!navigator.onLine) setOnline(false); } }); return statusTail;
  }
  function toggleMode(force) {
    const enabled = force === undefined ? !document.body.classList.contains('annotation-mode') : force;
    document.body.classList.toggle('annotation-mode', enabled); $('#addAnnotation').classList.toggle('active', enabled); $('#mobileAnnotate').classList.toggle('active', enabled); $('#addAnnotation').setAttribute('aria-pressed', String(enabled));
    announce(enabled ? '点击正文添加批注' : (online ? '已同步' : '离线修改已暂存'), enabled ? 'syncing' : (online ? 'saved' : 'offline'));
  }
  function composerAt(target, clientX, clientY) {
    const box = target.getBoundingClientRect(); pending = {anchor: target.dataset.reviewAnchor, position: {x: Math.max(0, Math.min(100, (clientX - box.left) / box.width * 100)), y: Math.max(0, Math.min(100, (clientY - box.top) / box.height * 100))}};
    target.classList.add('review-target-active');
    $('#reviewComposerLabel').textContent = `批注位置：${anchorLabel(pending.anchor)}`; $('#reviewComposer').hidden = false; setDrawer('full', 'opinions'); $('#reviewText').focus();
  }
  function closeComposer() { anchorTarget(pending?.anchor)?.classList.remove('review-target-active'); pending = null; $('#reviewComposer').hidden = true; $('#reviewText').value = ''; }
  function rememberNote(text) {
    try { const recent = JSON.parse(localStorage.getItem(recentKey) || '[]').filter(value => value !== text); recent.unshift(text); localStorage.setItem(recentKey, JSON.stringify(recent.slice(0, 3))); renderRecentNotes(); } catch (_) {}
  }
  function renderRecentNotes() {
    const host = $('#reviewRecentNotes'); host.replaceChildren();
    let recent = []; try { recent = JSON.parse(localStorage.getItem(recentKey) || '[]'); } catch (_) {}
    recent.forEach(text => { const button = document.createElement('button'); button.type = 'button'; button.textContent = text; button.onclick = () => { $('#reviewText').value = text; $('#reviewText').focus(); }; host.append(button); });
    host.hidden = recent.length === 0;
  }
  function saveAnnotation() {
    const text = $('#reviewText').value.trim(); if (!pending || !text) return announce('请填写批注内容', 'error');
    const now = new Date().toISOString(); const item = {id: crypto.randomUUID?.() || `annotation-${Date.now()}`, ...pending, text, status: 'open', createdAt: now, updatedAt: now};
    state.annotations.push(item); rememberNote(text); closeComposer(); enqueue('annotation-upsert', item, null); render();
  }
  function setDrawer(snap = 'half', view = drawerView) {
    drawerSnap = snap; drawerView = view; const shell = $('#reviewShell'); shell.dataset.snap = snap; shell.dataset.view = view; shell.classList.toggle('open', !['closed', 'peek'].includes(snap)); shell.setAttribute('aria-hidden', String(snap === 'closed'));
    $$('[data-review-view]').forEach(node => { node.hidden = node.dataset.reviewView !== view; });
    $$('#reviewBottomBar [data-view]').forEach(button => button.classList.toggle('active', button.dataset.view === view && !['closed', 'peek'].includes(snap)));
    if (view === 'checklist' && !['closed', 'peek'].includes(snap)) requestAnimationFrame(() => $('#reviewChecklistPending input:not(:checked)')?.focus({preventScroll: true})); renderSummary();
  }
  function setupDrawerGestures() {
    const handle = $('#reviewDragHandle'); let startY = 0; let currentY = 0; let activePointer = null; let suppressClick = false;
    handle.addEventListener('click', event => {
      if (suppressClick) { suppressClick = false; event.preventDefault(); event.stopPropagation(); return; }
      setDrawer(drawerSnap === 'full' ? 'half' : 'full', drawerView);
    });
    handle.addEventListener('pointerdown', event => { startY = currentY = event.clientY; activePointer = event.pointerId; try { handle.setPointerCapture(event.pointerId); } catch (_) {} });
    handle.addEventListener('pointermove', event => { if (activePointer !== event.pointerId) return; currentY = event.clientY; $('#reviewShell').style.transform = `translateY(${Math.max(0, currentY - startY)}px)`; });
    handle.addEventListener('pointerup', event => {
      if (activePointer !== event.pointerId) return; activePointer = null; if (handle.hasPointerCapture(event.pointerId)) handle.releasePointerCapture(event.pointerId);
      $('#reviewShell').style.transform = ''; const dy = currentY - startY; suppressClick = Math.abs(dy) > 8;
      if (dy > 120) setDrawer('peek'); else if (dy > 40) setDrawer('half'); else if (dy < -40) setDrawer('full');
    });
    handle.addEventListener('pointercancel', event => { if (activePointer !== event.pointerId) return; activePointer = null; if (handle.hasPointerCapture(event.pointerId)) handle.releasePointerCapture(event.pointerId); $('#reviewShell').style.transform = ''; currentY = startY; });
    let timer = 0; let origin = null;
    document.addEventListener('pointerdown', event => {
      if (event.pointerType === 'mouse' || document.body.classList.contains('annotation-mode') || event.target.closest('[data-export-ignore]')) return;
      const target = event.target.closest('[data-review-anchor]'); if (!target) return; origin = {target, x: event.clientX, y: event.clientY, id: event.pointerId};
      timer = setTimeout(() => { if (!origin || getSelection()?.toString()) return; composerAt(origin.target, origin.x, origin.y); navigator.vibrate?.(20); origin = null; timer = 0; }, 400);
    }, true);
    document.addEventListener('pointermove', event => { if (origin && (Math.abs(event.clientX - origin.x) > 10 || Math.abs(event.clientY - origin.y) > 10)) { clearTimeout(timer); timer = 0; origin = null; } }, true);
    ['pointerup', 'pointercancel'].forEach(name => document.addEventListener(name, () => { clearTimeout(timer); timer = 0; origin = null; }, true));
    addEventListener('scroll', () => {
      const next = scrollY; const delta = next - lastScrollY; const direction = Math.sign(delta);
      if (direction && direction !== scrollDirection) { scrollDirection = direction; scrollDistance = 0; }
      scrollDistance += Math.abs(delta);
      if (direction > 0 && scrollDistance > 36 && next > 80) { document.body.classList.add('review-controls-hidden'); if (!['closed', 'peek'].includes(drawerSnap)) setDrawer('peek', drawerView); scrollDistance = 0; }
      else if (direction < 0 && scrollDistance > 24) { document.body.classList.remove('review-controls-hidden'); scrollDistance = 0; }
      lastScrollY = next;
    }, {passive: true});
  }
  function mergeRemote(remote) {
    serverState = remote; state = materialize(remote); cacheState(); render();
  }
  function cacheState() { try { localStorage.setItem(storageKey, JSON.stringify(state)); } catch (_) {} }
  async function loadNewVersion() {
    try { const remote = await request('/api/review'); mergeRemote(remote); $('#showNewVersion').hidden = true; if (operations.length) drainQueue().catch(() => {}); else announce('已同步', 'saved'); }
    catch (_) { announce('离线修改已暂存', 'offline'); }
  }
  function openApprovalDialog() {
    const dialog = $('#approvalDialog'); approvalTrigger = document.activeElement;
    approvalBackground = [...document.body.children].filter(node => node !== dialog && !['SCRIPT', 'STYLE'].includes(node.tagName)).map(node => ({node, inert: node.hasAttribute('inert'), ariaHidden: node.getAttribute('aria-hidden')}));
    approvalBackground.forEach(({node}) => { node.inert = true; node.setAttribute('aria-hidden', 'true'); });
    dialog.hidden = false; $('#confirmApproval').focus();
  }
  function closeApprovalDialog() {
    const dialog = $('#approvalDialog'); if (dialog.hidden) return;
    dialog.hidden = true;
    approvalBackground.forEach(({node, inert, ariaHidden}) => { node.inert = inert; if (ariaHidden === null) node.removeAttribute('aria-hidden'); else node.setAttribute('aria-hidden', ariaHidden); });
    approvalBackground = []; const trigger = approvalTrigger; approvalTrigger = null; if (trigger?.isConnected) trigger.focus();
  }
  function handleApprovalKeys(event) {
    const dialog = $('#approvalDialog'); if (dialog.hidden) return;
    if (event.key === 'Escape') { event.preventDefault(); event.stopImmediatePropagation(); closeApprovalDialog(); return; }
    if (event.key !== 'Tab') return;
    const focusable = $$('button:not([disabled]), [href], input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])', dialog).filter(node => !node.hidden);
    if (!focusable.length) { event.preventDefault(); dialog.focus(); return; }
    const first = focusable[0]; const last = focusable.at(-1);
    if (!dialog.contains(document.activeElement)) { event.preventDefault(); (event.shiftKey ? last : first).focus(); }
    else if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }
  async function approve() {
    if (!online) return announce('离线修改已暂存', 'offline');
    try { await syncBeforeAction(); } catch (error) { return announce(error.message, 'error'); }
    const incomplete = checklist.findIndex(item => !item.checked); if (incomplete >= 0) { setDrawer('full', 'checklist'); requestAnimationFrame(() => $(`.review-check-item[data-index="${incomplete}"] input`)?.focus()); return announce('请先完成发布检查', 'error'); }
    if (state.annotations.some(item => item.status === 'open')) { setDrawer('full', 'opinions'); return announce('请先处理全部意见', 'error'); }
    openApprovalDialog();
  }
  async function confirmApproval() {
    try { await syncBeforeAction(); const result = await request('/api/review/approve', {method: 'POST', body: JSON.stringify({revision: state.revision})}); serverState = result.review; state = materialize(serverState); closeApprovalDialog(); cacheState(); applyStatus(result.status); announce('审阅已通过', 'saved'); }
    catch (error) { announce(error.status === 409 ? '存在新版本' : error.message || '冲突', 'error'); }
  }
  async function init() {
    document.body.classList.add('review-enabled');
    $('#reviewTitle').textContent = (config.articleId || '日报').replace(/-ribao$/, ' 日报');
    try { const cached = JSON.parse(localStorage.getItem(storageKey) || 'null'); const queued = JSON.parse(localStorage.getItem(queueKey) || '[]'); if (cached?.annotations) state = {...state, ...cached, pageId: config.articleId}; if (Array.isArray(queued)) operations = queued; } catch (_) {}
    quickTags.forEach(tag => { const button = document.createElement('button'); button.type = 'button'; button.textContent = tag; button.onclick = () => { const input = $('#reviewText'); input.value = input.value ? `${input.value}；${tag}` : tag; input.focus(); }; $('#reviewQuickTags').append(button); });
    renderRecentNotes();
    try { serverState = await request('/api/review'); state = materialize(serverState); cacheState(); announce(operations.length ? '正在保存…' : '已同步', operations.length ? 'syncing' : 'saved'); }
    catch (_) { serverState = clone(state); announce('离线修改已暂存', 'offline'); online = false; }
    render(); refreshStatus(); setOnline(online && navigator.onLine); setupDrawerGestures();
    $('#addAnnotation').onclick = () => toggleMode(); $('#mobileAnnotate').onclick = () => toggleMode(); $('#saveAnnotation').onclick = saveAnnotation; $('#cancelAnnotation').onclick = closeComposer;
    document.addEventListener('click', event => { if (!document.body.classList.contains('annotation-mode')) return; const target = event.target.closest('[data-review-anchor]'); if (!target) return; event.preventDefault(); event.stopPropagation(); composerAt(target, event.clientX, event.clientY); }, true);
    $$('.review-filter').forEach(button => button.onclick = () => { filter = button.dataset.filter; selected = -1; render(); });
    $$('[data-view]').forEach(button => button.onclick = () => setDrawer(['closed', 'peek'].includes(drawerSnap) ? 'half' : drawerSnap, button.dataset.view));
    $('#closeReviewDrawer').onclick = () => setDrawer('peek');
    $('#reviewMore').onclick = () => setDrawer('full', 'more');
    $('#reviewBack').onclick = () => { if (!['closed', 'peek'].includes(drawerSnap)) setDrawer('peek'); else if (history.length > 1) history.back(); };
    $('#reviewGeneralNote').oninput = event => { state.generalNote = event.target.value; dirty = true; cacheState(); clearTimeout(saveTimer); saveTimer = setTimeout(() => { saveTimer = 0; enqueue('general-note', state.generalNote, serverState.generalNote); }, 180); };
    $('#toggleGeneralNote').onclick = () => { $('#reviewNote').hidden = !$('#reviewNote').hidden; if (!$('#reviewNote').hidden) $('#reviewGeneralNote').focus(); };
    $('#generateRevision').onclick = async () => { if (!online) return; try { await syncBeforeAction(); const result = await request('/api/revisions', {method: 'POST', body: JSON.stringify({revision: state.revision})}); serverState = result.review; state = materialize(serverState); activeRevision = result.revisionId; $('#reviewDiff').hidden = false; $('#reviewDiffSummary').textContent = `修订 ${result.revisionId} 已生成；正文变化后发布检查已失效。`; cacheState(); render(); refreshStatus(); announce('已同步', 'saved'); } catch (error) { announce(error.status === 409 ? '存在新版本' : '冲突', 'error'); } };
    $('#acceptRevision').onclick = () => { state.annotations.filter(item => item.status === 'open').forEach(item => { const before = clone(item); item.status = 'resolved'; item.updatedAt = new Date().toISOString(); enqueue('annotation-upsert', item, before); }); $('#reviewDiff').hidden = true; render(); };
    $('#continueRevision').onclick = () => { state.sessionState = 'changes_requested'; enqueue('session', state.sessionState, serverState.sessionState); $('#reviewDiff').hidden = true; };
    $('#restoreRevision').onclick = async () => { if (!online) return; try { await syncBeforeAction(); const result = await request('/api/revisions/restore', {method: 'POST', body: JSON.stringify({revisionId: activeRevision, revision: state.revision})}); serverState = result.review; state = materialize(serverState); $('#reviewDiff').hidden = true; render(); refreshStatus(); announce('已同步', 'saved'); } catch (error) { announce(error.status === 409 ? '存在新版本' : '冲突', 'error'); } };
    $('#approveReview').onclick = approve; $('#cancelApproval').onclick = () => { closeApprovalDialog(); setDrawer('full', 'checklist'); }; $('#confirmApproval').onclick = confirmApproval;
    document.addEventListener('keydown', handleApprovalKeys, true);
    $('#requestChanges').onclick = () => { state.sessionState = 'changes_requested'; enqueue('session', state.sessionState, serverState.sessionState); };
    $('#downloadProduct').onclick = async event => { if (!online) return; try { await syncBeforeAction(); } catch (error) { return announce(error.message, 'error'); } if (event.target.dataset.current !== 'true') { try { await request('/api/export', {method: 'POST', body: '{}'}); await refreshStatus(); announce('已同步', 'saved'); } catch (error) { announce(error.status === 409 ? '存在新版本' : '冲突', 'error'); } return; } location.href = apiPath('/api/export'); };
    $('#exportReview').onclick = () => { const link = document.createElement('a'); link.href = URL.createObjectURL(new Blob([JSON.stringify(state, null, 2)], {type: 'application/json'})); link.download = `${state.pageId}-review.json`; link.click(); URL.revokeObjectURL(link.href); };
    $('#importReview').onchange = async event => { const previous = clone(state); try { await syncBeforeAction(); const value = JSON.parse(await event.target.files[0].text()); if (!Array.isArray(value.annotations)) throw new Error('annotations 必须是数组'); const legacy = [state.pageId, state.pageId.split('-')[0], state.pageId.split('-').at(-1), 'ribao']; if (value.pageId && !legacy.includes(value.pageId)) throw new Error('审阅包属于其他稿件'); value.pageId = state.pageId; value.revision = state.revision; const saved = await request('/api/review', {method: 'PUT', body: JSON.stringify(value)}); serverState = saved; state = materialize(saved); render(); announce('已同步', 'saved'); } catch (error) { state = previous; cacheState(); render(); announce(`导入失败：${error.message}`, 'error'); } event.target.value = ''; };
    $('#undoDelete').onclick = () => {
      clearTimeout(undoTimer);
      if (deleted) { const restored = deleted.item; state.annotations.splice(Math.min(deleted.index, state.annotations.length), 0, restored); enqueue('annotation-upsert', restored, null); }
      else if (toggled) { const item = state.annotations.find(value => value.id === toggled.id); if (item) { const current = clone(item); Object.assign(item, clone(toggled.before), {updatedAt: new Date().toISOString()}); enqueue('annotation-upsert', item, current); } }
      else return;
      deleted = null; toggled = null; $('#reviewUndo').hidden = true; render();
    };
    $('#retrySync').onclick = () => { operations.forEach(item => { item.failed = ''; }); setOnline(navigator.onLine); drainQueue().catch(() => {}); };
    $('#showNewVersion').onclick = loadNewVersion;
    document.addEventListener('keydown', event => { if (/INPUT|TEXTAREA|SELECT/.test(event.target.tagName)) { if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') saveAnnotation(); if (event.key === 'Escape') closeComposer(); return; } if (event.key.toLowerCase() === 'c') toggleMode(); if (event.key === 'Escape') { closeComposer(); setDrawer('peek'); } if (event.key.toLowerCase() === 'j') select(selected + 1); if (event.key.toLowerCase() === 'k') select(selected - 1); });
    addEventListener('online', () => setOnline(true)); addEventListener('offline', () => setOnline(false));
    addEventListener('beforeunload', event => { if (!operations.length && !dirty && !saveTimer) return; event.preventDefault(); event.returnValue = ''; });
    if (visualViewport) visualViewport.addEventListener('resize', () => { const keyboard = Math.max(0, innerHeight - visualViewport.height - visualViewport.offsetTop); document.documentElement.style.setProperty('--keyboard-offset', `${keyboard}px`); });
    if (window.EventSource) { const events = new EventSource(apiPath('/events')); events.addEventListener('review', async () => { if (operations.length || dirty || saveTimer || draining || $('#reviewText').value) { $('#showNewVersion').hidden = false; announce('存在新版本', 'error'); return; } await loadNewVersion(); }); events.addEventListener('content', async () => { if (operations.length || dirty) { $('#showNewVersion').hidden = false; announce('存在新版本', 'error'); return; } try { const response = await fetch(apiPath('/'), {cache: 'no-store'}); const copy = new DOMParser().parseFromString(await response.text(), 'text/html'); const next = copy.querySelector('#article-content'); if (next) $('#article-content').replaceWith(next); render(); refreshStatus(); } catch (_) {} }); events.addEventListener('status', refreshStatus); events.onerror = () => { if (!navigator.onLine) setOnline(false); }; }
  }
  init();
})();
