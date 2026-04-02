const fileInput    = document.getElementById('fileInput');
const dropZone     = document.getElementById('dropZone');
const fileList     = document.getElementById('fileList');
const keyword      = document.getElementById('keyword');
const previewBtn   = document.getElementById('previewBtn');
const processBtn   = document.getElementById('processBtn');
const previewPanel = document.getElementById('previewPanel');
const previewContent  = document.getElementById('previewContent');
const previewMeta     = document.getElementById('previewMeta');
const previewLoader   = document.getElementById('previewLoader');
const processLoader   = document.getElementById('processLoader');

let selectedFiles = []; // Array of File objects

// ── Drag & Drop ──────────────────────────────────────────────────────────────
dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('dragover'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
dropZone.addEventListener('drop', e => {
  e.preventDefault();
  dropZone.classList.remove('dragover');
  addFiles(Array.from(e.dataTransfer.files));
});

fileInput.addEventListener('change', () => {
  addFiles(Array.from(fileInput.files));
  fileInput.value = '';
});

// ── File Management ───────────────────────────────────────────────────────────
function addFiles(newFiles) {
  const allowed = newFiles.filter(f => {
    const ext = f.name.split('.').pop().toLowerCase();
    if (!['xml', 'zip'].includes(ext)) {
      showToast(`❌ Skipped "${f.name}" — only .xml and .zip allowed`, 'error');
      return false;
    }
    if (selectedFiles.some(s => s.name === f.name && s.size === f.size)) {
      showToast(`⚠️ "${f.name}" is already in the list`, 'error');
      return false;
    }
    return true;
  });

  selectedFiles = [...selectedFiles, ...allowed];
  renderFileList();
}

window.removeFileAt = function(index) {
  selectedFiles.splice(index, 1);
  renderFileList();
};

function renderFileList() {
  fileList.innerHTML = '';

  if (selectedFiles.length === 0) {
    previewBtn.disabled = true;
    processBtn.disabled = true;
    previewPanel.classList.remove('show');
    previewContent.innerHTML = '';
    return;
  }

  previewBtn.disabled = false;
  processBtn.disabled = false;

  selectedFiles.forEach((file, i) => {
    const ext = file.name.split('.').pop().toLowerCase();
    const icon = ext === 'zip' ? '🗜️' : '📄';
    const item = document.createElement('div');
    item.className = 'file-item';
    item.innerHTML = `
      <span class="file-item-icon">${icon}</span>
      <div class="file-item-info">
        <div class="file-item-name">${file.name}</div>
        <div class="file-item-size">${formatBytes(file.size)}</div>
      </div>
      <button class="file-remove" onclick="removeFileAt(${i})" title="Remove">✕</button>
    `;
    fileList.appendChild(item);
  });

  if (selectedFiles.length > 1) {
    const summary = document.createElement('div');
    summary.className = 'file-summary';
    summary.textContent = `${selectedFiles.length} files · ${formatBytes(selectedFiles.reduce((a, f) => a + f.size, 0))} total`;
    fileList.appendChild(summary);
  }
}

// ── Preview ───────────────────────────────────────────────────────────────────
previewBtn.addEventListener('click', async () => {
  if (!selectedFiles.length) return;
  const kw = keyword.value.trim();
  if (!kw) { showToast('⚠️ Please enter a keyword', 'error'); return; }

  previewBtn.disabled = true;
  previewLoader.classList.add('show');
  previewPanel.classList.remove('show');

  const fd = new FormData();
  selectedFiles.forEach(f => fd.append('file', f));
  fd.append('keyword', kw);

  try {
    const res = await fetch('/preview', { method: 'POST', body: fd });
    const data = await res.json();
    if (data.error) { showToast('❌ ' + data.error, 'error'); return; }
    renderPreview(data);
    previewPanel.classList.add('show');
  } catch(e) {
    showToast('❌ Server error: ' + e.message, 'error');
  } finally {
    previewBtn.disabled = false;
    previewLoader.classList.remove('show');
  }
});

function renderPreview(data) {
  const kw = data.keyword;
  previewMeta.textContent = `keyword: "${kw}"`;

  if (data.files.length === 1) {
    previewContent.innerHTML = renderFilePreview(data.files[0], kw);
  } else {
    // Group by zip_source
    const groups = {};
    data.files.forEach(f => {
      const group = f.zip_source || '__xml__';
      if (!groups[group]) groups[group] = [];
      groups[group].push(f);
    });

    const groupKeys = Object.keys(groups);
    let tabs = '<div class="file-tabs">';
    let contents = '';

    groupKeys.forEach((groupKey, idx) => {
      const label = groupKey === '__xml__' ? 'XML Files' : groupKey.split('/').pop();
      const files = groups[groupKey];
      tabs += `<div class="file-tab ${idx===0?'active':''}" onclick="switchTab(${idx})">${label}</div>`;

      let groupHtml = '';
      files.forEach(f => {
        if (files.length > 1) groupHtml += `<div class="sub-file-label">📄 ${f.filename}</div>`;
        groupHtml += renderFilePreview(f, kw);
      });
      contents += `<div class="tab-content ${idx===0?'active':''}" id="tab-${idx}">${groupHtml}</div>`;
    });

    tabs += '</div>';
    previewContent.innerHTML = tabs + contents;
  }
}

function renderFilePreview(f, kw) {
  const kept = f.total_lines - f.matched_lines;
  let html = `
    <div class="stats-grid">
      <div class="stat-box">
        <div class="stat-label">Total Lines</div>
        <div class="stat-value blue">${f.total_lines.toLocaleString()}</div>
      </div>
      <div class="stat-box">
        <div class="stat-label">Lines to Delete</div>
        <div class="stat-value red">${f.matched_lines.toLocaleString()}</div>
      </div>
      <div class="stat-box">
        <div class="stat-label">Lines Remaining</div>
        <div class="stat-value green">${kept.toLocaleString()}</div>
      </div>
    </div>`;

  if (f.sample.length > 0) {
    html += `<div class="sample-lines">
      <div class="sample-label">Sample lines to be removed${f.matched_lines > f.sample.length ? ` (showing ${f.sample.length} of ${f.matched_lines})` : ''}</div>`;
    f.sample.forEach(s => {
      const highlighted = s.content.replace(
        new RegExp(escapeRegex(kw), 'gi'),
        m => `<span class="keyword-highlight">${m}</span>`
      );
      html += `<div class="line-item">
        <span class="line-num">L${s.line_number}</span>
        <span class="line-content">${highlighted}</span>
      </div>`;
    });
    html += '</div>';
  } else {
    html += `<div class="sample-lines"><div class="stat-label" style="color:var(--success)">✅ No lines found with keyword "${kw}"</div></div>`;
  }
  return html;
}

window.switchTab = function(idx) {
  document.querySelectorAll('.file-tab').forEach((t,i) => t.classList.toggle('active', i===idx));
  document.querySelectorAll('.tab-content').forEach((t,i) => t.classList.toggle('active', i===idx));
};

// ── Process ───────────────────────────────────────────────────────────────────
processBtn.addEventListener('click', async () => {
  if (!selectedFiles.length) return;
  const kw = keyword.value.trim();
  if (!kw) { showToast('⚠️ Please enter a keyword', 'error'); return; }

  processBtn.disabled = true;
  processLoader.classList.add('show');

  const fd = new FormData();
  selectedFiles.forEach(f => fd.append('file', f));
  fd.append('keyword', kw);

  try {
    const res = await fetch('/process', { method: 'POST', body: fd });
    if (!res.ok) {
      const data = await res.json();
      showToast('❌ ' + (data.error || 'Error'), 'error');
      return;
    }
    const blob = await res.blob();
    const disposition = res.headers.get('Content-Disposition') || '';
    const nameMatch = disposition.match(/filename=(.+)/);
    const outName = nameMatch ? nameMatch[1].replace(/"/g,'') : 'cleaned_file';

    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = outName; a.click();
    URL.revokeObjectURL(url);
    showToast(`✅ Downloaded: ${outName}`, 'success');
  } catch(e) {
    showToast('❌ ' + e.message, 'error');
  } finally {
    processBtn.disabled = false;
    processLoader.classList.remove('show');
  }
});

// ── Helpers ───────────────────────────────────────────────────────────────────
function formatBytes(b) {
  if (b < 1024) return b + ' B';
  if (b < 1048576) return (b/1024).toFixed(1) + ' KB';
  return (b/1048576).toFixed(1) + ' MB';
}

function escapeRegex(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }

let toastTimer;
function showToast(msg, type='success') {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast show ' + type;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.classList.remove('show'); }, 4000);
}