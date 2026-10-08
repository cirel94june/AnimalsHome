let profileData = null;
let draftEntries = [];
let profileDirty = false;
let profileBusy = false;
let editingEntry = null;
let entryBeforeEdit = null;
let stateEditing = false;
const profileEl = id => document.getElementById(id);

function profileEscape(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function profileTime(ts) {
  if (!ts) return '';
  const date = new Date(ts * 1000);
  const pad = value => String(value).padStart(2, '0');
  return `${pad(date.getMonth()+1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}
function localDateValue(ts) {
  const d = new Date(ts * 1000);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0,16);
}
function markProfileDirty() {
  const entriesChanged = draftEntries.length !== profileData.entries.length || draftEntries.some((entry, index) => {
    const saved = profileData.entries[index];
    return !saved || ['key','category','content'].some(field => entry[field] !== saved[field]);
  });
  const state = profileData.current_state;
  const text = profileEl('stateText').value.trim();
  const stateChanged = text !== (state?.text || '') || (text && (
    profileEl('statePhase').value !== state?.phase || profileEl('stateExpiry').value !== localDateValue(state?.expires_at || 0)));
  profileDirty = !!(entriesChanged || stateChanged);
  profileEl('save').disabled = !profileDirty || profileBusy;
  profileEl('saveHint').textContent = profileDirty ? '有未保存的修改' : '尚无修改';
  renderProfileBudget();
}
async function profileRequest(url, options = {}) {
  const response = await fetch(url, {cache:'no-store', ...options});
  const data = await response.json();
  if (!response.ok) {
    const message = typeof data.detail === 'string' ? data.detail : '请检查填写内容后再保存';
    throw new Error(message);
  }
  return data;
}
function renderProfileStatus(data) {
  profileEl('enabled').checked = data.enabled;
  profileEl('status').textContent = data.enabled
    ? `已开启${data.pending ? ` · ${data.pending} 条待整理` : ''}${data.failed ? ` · ${data.failed} 条未处理完整` : ''}`
    : '已关闭';
  profileEl('model').textContent = `哨兵：${data.model || '未配置'}`;
  profileEl('error').textContent = data.last_error || '';
  profileEl('error').hidden = !data.last_error;
}
function renderProfileBudget() {
  if (!profileData) return;
  profileEl('budget').textContent = `发给 AI：${profileData.prompt_chars || 0}/${profileData.prompt_limit || 1200} 字符`
    + (profileData.omitted ? ` · 另有 ${profileData.omitted} 项未发送` : '')
    + (profileDirty ? ' · 保存后更新预览' : '');
  profileEl('promptPreview').textContent = profileData.enabled
    ? profileData.prompt_preview || '当前没有需要发送的资料'
    : '总开关已关闭，不向 AI 发送画像和状态';
}
function renderProfileEntries() {
  const categories = profileData.categories;
  const options = (items, value) => Object.entries(items).map(([key,label]) =>
    `<option value="${key}" ${value === key ? 'selected' : ''}>${profileEscape(label)}</option>`).join('');
  const row = (entry, index) => {
    const stamp = entry.updated_at || entry.source_ts;
    const time = `<time class="entry-time" ${stamp ? `datetime="${new Date(stamp*1000).toISOString()}" title="${profileEscape(new Date(stamp*1000).toLocaleString('zh-CN'))}"` : ''}>${profileTime(stamp)}</time>`;
    return `<article class="profile-entry${editingEntry === entry.key ? ' is-editing' : ''}" data-index="${index}">
      ${editingEntry === entry.key ? `<div class="entry-editor">
        <select aria-label="资料分类" data-field="category">${options(categories, entry.category)}</select>
        <textarea rows="3" maxlength="600" aria-label="画像内容" data-field="content" placeholder="写下希望 AI 记住的事实或偏好">${profileEscape(entry.content)}</textarea>
        <div class="editor-actions"><button class="entry-remove" data-action="remove" type="button">删除</button><button class="quiet-button" data-action="cancel" type="button">取消</button><button class="save-button" data-action="save" type="button">保存</button></div>
      </div>` : `<div class="entry-copy"><p>${profileEscape(entry.content)}</p>${time}</div>
        <div class="entry-actions"><button class="quick-remove" data-action="quick-remove" aria-label="删除这条画像" title="删除这条画像" type="button">删除</button>
        <button class="edit-button" data-action="edit" aria-label="编辑画像" title="编辑画像" type="button"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m16 3 5 5M4 20l4-1L21 6a2 2 0 0 0-3-3L5 16l-1 4Z"/></svg></button></div>`}
    </article>`;
  };
  profileEl('entries').innerHTML = draftEntries.length ? Object.entries(categories).map(([cat,title]) => {
    const rows = draftEntries.map((entry,index) => ({entry,index})).filter(({entry}) => entry.category === cat);
    return `<div class="profile-group"><h4>${profileEscape(title)}</h4>${rows.length ? rows.map(({entry,index}) => row(entry,index)).join('') : '<p class="profile-empty">暂无内容</p>'}</div>`;
  }).join('') : '<p class="profile-empty">还没有画像内容。<br>可以先手动写几条，也会从新聊天中维护。</p>';
}
function renderState() {
  const state = profileData.current_state;
  profileEl('stateText').value = state?.text || '';
  profileEl('statePhase').value = state?.phase || 'unknown';
  profileEl('stateExpiry').value = localDateValue(state?.expires_at || Date.now()/1000 + 3600);
  profileEl('stateContent').textContent = state?.text || '暂无状态';
  profileEl('stateTime').textContent = profileTime(state?.updated_at || state?.event_at);
  profileEl('stateView').hidden = stateEditing;
  profileEl('stateEditor').hidden = !stateEditing;
}
function renderProfile(data) {
  profileData = data;
  draftEntries = data.entries.map(e => ({...e}));
  editingEntry = null;
  entryBeforeEdit = null;
  stateEditing = false;
  renderProfileStatus(data);
  renderProfileEntries();
  renderState();
  profileDirty = false;
  profileEl('save').disabled = true;
  profileEl('saveHint').textContent = '尚无修改';
  renderProfileBudget();
}
async function loadProfile({background = false} = {}) {
  try {
    const data = await profileRequest('/api/user-profile');
    if (data.prompt_limit !== 1200 || !data.categories?.profile) {
      profileEl('status').textContent = '后端版本尚未更新';
      throw new Error('请重启小家后端，再刷新此页使用新版画像');
    }
    if (background && (profileDirty || editingEntry || stateEditing)) {
      renderProfileStatus(data);
      if (data.revision !== profileData?.revision) profileEl('saveHint').textContent = '后台资料已更新；你的修改仍保留，保存前请刷新核对';
      return;
    }
    renderProfile(data);
  } catch (error) {
    profileEl('saveHint').textContent = error.message || '加载失败，请稍后刷新';
  }
}
profileEl('entries').addEventListener('input', event => {
  const row = event.target.closest('[data-index]');
  if (!row || !event.target.dataset.field) return;
  const entry = draftEntries[Number(row.dataset.index)];
  const field = event.target.dataset.field;
  entry[field] = event.target.value;
  markProfileDirty();
  if (field === 'category') renderProfileEntries();
});
profileEl('entries').addEventListener('click', event => {
  const action = event.target.closest('[data-action]')?.dataset.action;
  if (!action || profileBusy) return;
  const index = Number(event.target.closest('[data-index]').dataset.index);
  if (action === 'edit') {
    editingEntry = draftEntries[index].key;
    entryBeforeEdit = {...draftEntries[index]};
    renderProfileEntries();
    profileEl('entries').querySelector(`[data-index="${index}"] textarea`).focus();
    return;
  }
  if (action === 'save' && profileDirty) return profileEl('save').click();
  if (action === 'remove' || action === 'quick-remove' || (action === 'cancel' && !profileData.entries.some(e => e.key === editingEntry))) {
    draftEntries.splice(index, 1);
  } else if (action === 'cancel') {
    draftEntries[index] = entryBeforeEdit;
  }
  editingEntry = null;
  entryBeforeEdit = null;
  renderProfileEntries();
  markProfileDirty();
  if (action === 'quick-remove') profileEl('save').click();
});
profileEl('addEntry').addEventListener('click', () => {
  if (!profileData) return;
  if (draftEntries.length >= 100) return showToast('请先合并或移除旧资料');
  const id = globalThis.crypto?.randomUUID?.() || `${Date.now().toString(36)}.${Math.random().toString(36).slice(2)}`;
  const entry = {key:`manual.${id}`, category:'profile', content:'', locked:false};
  draftEntries.push(entry);
  editingEntry = entry.key;
  entryBeforeEdit = {...entry};
  renderProfileEntries();
  markProfileDirty();
  profileEl('entries').querySelector(`[data-index="${draftEntries.length-1}"] textarea`).focus();
});
for (const id of ['stateText','statePhase','stateExpiry']) {
  profileEl(id).addEventListener('input', markProfileDirty);
}
profileEl('editState').addEventListener('click', () => {
  stateEditing = true;
  profileEl('stateView').hidden = true;
  profileEl('stateEditor').hidden = false;
  profileEl('stateText').focus();
});
profileEl('cancelState').addEventListener('click', () => {
  stateEditing = false;
  renderState();
  markProfileDirty();
});
profileEl('saveState').addEventListener('click', () => {
  if (profileDirty) return profileEl('save').click();
  stateEditing = false;
  renderState();
});
profileEl('clearState').addEventListener('click', () => {
  profileEl('stateText').value = '';
  markProfileDirty();
});
profileEl('enabled').addEventListener('change', async event => {
  event.target.disabled = true;
  try {
    await profileRequest('/api/capabilities/post_sentinel', {
      method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({enabled:event.target.checked})
    });
    await loadProfile({background:true});
  } catch (error) {
    event.target.checked = !event.target.checked;
    showToast(error.message);
  } finally { event.target.disabled = false; }
});
profileEl('refresh').addEventListener('click', () => {
  if (!profileDirty || confirm('刷新会放弃尚未保存的修改，继续吗？')) loadProfile();
});
profileEl('save').addEventListener('click', async () => {
  if (!profileData || profileBusy) return;
  const text = profileEl('stateText').value.trim();
  const expiryValue = profileEl('stateExpiry').value;
  const originalExpiry = profileData.current_state?.expires_at;
  // Preserve seconds when another field is edited; datetime-local displays minutes.
  const expiresAt = originalExpiry && expiryValue === localDateValue(originalExpiry)
    ? originalExpiry : new Date(expiryValue).getTime()/1000;
  if (draftEntries.some(e => !e.content.trim())) return showToast('请填写每条画像的内容');
  if (draftEntries.some(e => ['available_at','due_at','expires_at'].some(f => e[f] != null && !Number.isFinite(e[f])))) return showToast('请检查资料中的日期');
  if (text && !Number.isFinite(expiresAt)) return showToast('请填写状态有效时间');
  const body = {
    revision:profileData.revision,
    entries:draftEntries.map(({key,category,content}) => ({key,category,content:content.trim(),locked:false})),
    current_state:text ? {text,phase:profileEl('statePhase').value,expires_at:expiresAt,locked:false} : null
  };
  profileBusy = true;
  profileEl('save').disabled = true;
  try {
    const data = await profileRequest('/api/user-profile', {method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    renderProfile(data);
    profileEl('saveHint').textContent = '已保存';
    showToast('画像已保存');
  } catch (error) {
    profileEl('saveHint').textContent = `${error.message}；未保存的内容仍保留`;
  } finally {
    profileBusy = false;
    profileEl('save').disabled = !profileDirty;
  }
});
connectCommonWS(message => {
  if (message.type === 'user_profile_changed' || message.type === 'capability_config_changed') loadProfile({background:true});
});
fetch('/api/worldbook').then(r => r.json()).then(data => {
  if (data.user_name) profileEl('pageTitle').textContent = `${data.user_name}的画像`;
}).catch(() => {});
loadProfile();
setInterval(() => { if (!document.hidden) loadProfile({background:true}); }, 60000);
