import { icon } from './icons.js';

export function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key === 'html') node.innerHTML = value;
    else if (key === 'dataset') Object.assign(node.dataset, value);
    else if (key === 'style') Object.assign(node.style, value);
    else if (key.startsWith('on') && typeof value === 'function') node.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key === 'checked' || key === 'disabled' || key === 'selected' || key === 'open') node[key] = Boolean(value);
    else if (key === 'value') node.value = value;
    else if (key === 'htmlFor') node.htmlFor = value;
    else node.setAttribute(key, String(value));
  }
  append(node, children);
  return node;
}

export function append(node, children) {
  for (const child of children.flat(Infinity)) {
    if (child === undefined || child === null || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function card(options = {}, ...body) {
  const header = options.title || options.actions
    ? el('div', { class: 'card-head' },
        el('div', { class: 'card-heading' },
          options.eyebrow ? el('span', { class: 'eyebrow', text: options.eyebrow }) : null,
          options.title ? el('h2', { text: options.title }) : null,
          options.subtitle ? el('p', { class: 'card-subtitle', text: options.subtitle }) : null),
        options.actions ? el('div', { class: 'card-actions' }, options.actions) : null)
    : null;
  return el('section', { class: 'card ' + (options.class || '') }, header, el('div', { class: 'card-body' }, ...body));
}

export function pageHeader(options = {}) {
  return el('header', { class: 'page-head' },
    el('div', {},
      options.eyebrow ? el('span', { class: 'eyebrow', text: options.eyebrow }) : null,
      el('h1', { text: options.title || '' }),
      options.description ? el('p', { class: 'page-description' }, options.description) : null),
    options.actions ? el('div', { class: 'page-actions' }, options.actions) : null);
}

export function button(label, options = {}) {
  const classes = ['btn'];
  if (options.variant) classes.push('btn-' + options.variant);
  if (options.size) classes.push('btn-' + options.size);
  if (options.loading) classes.push('is-loading');
  const node = el('button', {
    class: classes.join(' '),
    type: options.type || 'button',
    disabled: Boolean(options.disabled || options.loading),
    title: options.title || undefined,
    'aria-busy': options.loading ? 'true' : undefined,
    onclick: options.onClick,
  });
  if (options.loading) node.append(el('span', { class: 'spinner', 'aria-hidden': 'true' }));
  else if (options.icon) node.append(icon(options.icon, options.iconSize || 17));
  node.append(el('span', { text: label }));
  return node;
}

export function iconButton(name, label, onClick, options = {}) {
  const node = el('button', {
    class: 'icon-btn ' + (options.class || ''),
    type: 'button',
    title: label,
    'aria-label': label,
    disabled: Boolean(options.disabled),
    onclick: onClick,
  }, icon(name, options.size || 18));
  return node;
}

export function badge(text, type = 'neutral') {
  return el('span', { class: 'badge badge-' + type }, el('span', { class: 'badge-dot' }), text);
}

export function pill(text, type = 'neutral') {
  return el('span', { class: 'pill pill-' + type }, text);
}

export function field(label, control, hint) {
  return el('label', { class: 'field' },
    el('span', { class: 'field-label', text: label }),
    control,
    hint ? el('span', { class: 'field-hint' }, hint) : null);
}

export function select(options, value, onChange, attrs = {}) {
  const node = el('select', { class: 'input', ...attrs, onchange: (event) => onChange(event.target.value) });
  for (const item of options) {
    node.append(el('option', { value: item.value, text: item.label, selected: item.value === value }));
  }
  node.value = value;
  return node;
}

export function input(value, attrs = {}) {
  return el('input', { class: 'input', value: value ?? '', ...attrs });
}

export function checkbox(checked, label, onChange, attrs = {}) {
  const id = 'chk-' + Math.random().toString(36).slice(2);
  return el('label', { class: 'check-row', htmlFor: id },
    el('input', { id, type: 'checkbox', checked: Boolean(checked), onchange: (event) => onChange(event.target.checked), ...attrs }),
    el('span', { text: label }));
}

export function empty(title, description, action) {
  return el('div', { class: 'empty' },
    el('div', { class: 'empty-mark' }, icon('info', 22)),
    el('strong', { text: title }),
    description ? el('p', { text: description }) : null,
    action || null);
}

export function kv(rows) {
  return el('dl', { class: 'kv' }, rows.map(([name, value]) => el('div', { class: 'kv-row' },
    el('dt', { text: name }),
    el('dd', {}, value instanceof Node ? value : String(value ?? '—')))));
}

export function note(text, type = 'info') {
  const icons = { info: 'info', warn: 'alert', bad: 'alert', ok: 'check' };
  return el('div', { class: 'note note-' + type }, icon(icons[type] || 'info', 18), el('div', {}, text));
}

export function spacer() {
  return el('div', { class: 'spacer' });
}

export function formatBytes(value) {
  const bytes = Number(value) || 0;
  if (bytes < 1024) return bytes + ' B';
  const units = ['KB', 'MB', 'GB', 'TB'];
  let size = bytes / 1024;
  let index = 0;
  while (size >= 1024 && index < units.length - 1) { size /= 1024; index += 1; }
  return size.toFixed(size >= 10 ? 0 : 1) + ' ' + units[index];
}

export function formatTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString('zh-CN', { hour12: false });
}

export function relativeTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const diff = Date.now() - date.getTime();
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return '刚刚';
  if (minutes < 60) return minutes + ' 分钟前';
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return hours + ' 小时前';
  const days = Math.floor(hours / 24);
  if (days < 30) return days + ' 天前';
  return formatTime(value);
}

export function statusText(value) {
  return { passed: '通过', failed: '失败', untested: '未测试', unknown: '未核验', success: '成功' }[value] || String(value || '未知');
}

export function statusTone(value) {
  if (value === 'passed' || value === 'success' || value === 'ok') return 'ok';
  if (value === 'failed' || value === 'error') return 'bad';
  if (value === 'untested' || value === 'unknown') return 'warn';
  return 'neutral';
}

export function toast(message, type = 'info', timeout = 4200) {
  let host = document.getElementById('toasts');
  if (!host) {
    host = el('div', { id: 'toasts', class: 'toasts', 'aria-live': 'polite' });
    document.body.append(host);
  }
  const node = el('div', { class: 'toast toast-' + type },
    icon(type === 'ok' ? 'check' : type === 'bad' ? 'alert' : type === 'warn' ? 'alert' : 'info', 18),
    el('span', { text: message }));
  host.append(node);
  setTimeout(() => {
    node.classList.add('leaving');
    setTimeout(() => node.remove(), 220);
  }, timeout);
}

export function copyText(text, label = '已复制') {
  const done = () => toast(label, 'ok');
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(done).catch(() => fallback());
  } else {
    fallback();
  }
  function fallback() {
    const area = el('textarea', { class: 'clipboard-fallback' });
    area.value = text;
    document.body.append(area);
    area.select();
    try { document.execCommand('copy'); done(); } catch { toast('复制失败，请手动选择文本。', 'warn'); }
    area.remove();
  }
}

export function confirmDialog(options = {}) {
  return new Promise((resolve) => {
    const dialog = el('dialog', { class: 'modal' });
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      dialog.close();
      setTimeout(() => dialog.remove(), 0);
      resolve(value);
    };
    const body = el('div', { class: 'modal-body' });
    if (options.description) body.append(el('p', { class: 'modal-description' }, options.description));
    if (options.items && options.items.length) {
      body.append(el('ul', { class: 'impact-list' }, options.items.map((item) => el('li', {},
        el('strong', { text: item.title }),
        item.text ? el('span', { text: item.text }) : null))));
    }
    if (options.note) body.append(note(options.note, options.noteType || 'warn'));
    let checker = null;
    if (options.requireCheck) {
      checker = checkbox(false, options.requireCheck, () => {
        confirm.disabled = !checker.querySelector('input').checked;
      });
      body.append(checker);
    }
    const confirm = el('button', {
      class: 'btn ' + (options.danger ? 'btn-danger' : 'btn-ink'),
      type: 'button',
      disabled: Boolean(options.requireCheck),
      onclick: () => finish(true),
    }, options.confirmText || '确认');
    const cancel = el('button', { class: 'btn btn-secondary', type: 'button', onclick: () => finish(false) }, options.cancelText || '取消');
    dialog.append(
      el('div', { class: 'modal-head' },
        el('div', {},
          el('h2', { text: options.title || '确认' }),
          options.subtitle ? el('p', { class: 'modal-subtitle', text: options.subtitle }) : null),
        iconButton('close', '关闭', () => finish(false))),
      body,
      el('div', { class: 'modal-foot' }, cancel, confirm));
    dialog.addEventListener('cancel', (event) => { event.preventDefault(); finish(false); });
    dialog.addEventListener('click', (event) => { if (event.target === dialog) finish(false); });
    document.body.append(dialog);
    dialog.showModal();
    (options.danger ? cancel : confirm).focus();
  });
}

export function logBlock(text, emptyText = '暂无日志。') {
  return el('pre', { class: 'log-block' }, text || emptyText);
}
