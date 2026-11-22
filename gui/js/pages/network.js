import { icon } from '../icons.js';
import * as ui from '../ui.js';

function operationsBlocked(ctx, settingsRecovery = false) { return ctx.busy || Boolean(ctx.state.operation?.pending_rule_update) || Boolean(ctx.state.operation?.pending_tun?.length) || (!settingsRecovery && Boolean(ctx.state.operation?.settings_pending?.length)); }

const FLOW = [
  ['节点连接与真实局域网', '窄范围例外、真实私有地址、已审核的路由器/本地服务域名保持直连。'],
  ['拒绝公网 IPv6', '在代理前阻断公网 IPv6，避免绕过 TUN 的路径。'],
  ['Claude 优先', 'Claude 进程、官方入站 IP、核心与补充域名先于国内规则进入固定节点组。'],
  ['海外域名优先', 'geolocation-!cn 先于国内白名单进入固定海外节点，对应 DNS 同样走海外出口。'],
  ['国内域名', '已审核的国内域名名单先转换为规则；不使用未经审核的远程关键词。'],
  ['国内 IP 与 GEOIP,CN', '国内 IP 段与 GeoIP 规则使用 no-resolve，避免额外 DNS 泄漏。'],
  ['MATCH 固定海外组', '未知海外流量进入固定节点组，不回落 DIRECT。'],
];

export function render(ctx) {
  const root = document.createElement('div');
  root.className = 'stack';
  root.append(ui.pageHeader({
    title: '网络环境',
    actions: [ui.button('刷新状态', { variant: 'secondary', icon: 'refresh', onClick: ctx.refreshManually })],
  }));
  root.append(verificationCard(ctx));
  root.append(diagnosticsCard(ctx));
  root.append(actionsCard(ctx));
  return root;
}

let mobileExport = null;
let mobileProgress = '';
let downloadPlatform = '';
let gistLinks = {};
let gistError = '';
let gistDraft = '';
let syncGistDraft = '';
let gistNotice = '';
const MOBILE_FILES = [
  {platform: 'android', name: 'Android', format: 'YAML', filename: 'android-clash-meta.yaml', downloadLabel: '安卓手机', importStep: '在手机的 Clash Meta 中，打开「配置」，选择从文件导入。', importHint: '在 Clash Meta 的配置页选择文件或 URL 导入。'},
  {platform: 'ios', name: 'iOS', format: 'CONF', filename: 'ios-shadowrocket.conf', downloadLabel: 'iPhone', importStep: '在手机的 Shadowrocket 中，打开「配置」导入文件，再把「全局路由」设为「配置」。', importHint: '在 Shadowrocket 的「配置」页导入，再将全局路由设为「配置」。'},
];

function mobileFiles() {
  return MOBILE_FILES.map(file => ({...file, href: mobileExport?.[file.platform]}))
    .filter(({platform, href}) => href && new RegExp('^/api/mobile-exports/[a-f0-9]{32}/' + platform + '$').test(href));
}

async function generateMobileFiles(ctx) {
  mobileExport = null;
  gistLinks = {};
  gistError = '';
  gistNotice = '';
  try {
    mobileExport = await ctx.api.post('/api/mobile-export');
    const files = mobileFiles();
    ctx.acceptState('mobile_export', {id: mobileExport.id, current: files.map(file => file.platform)});
    return files;
  } catch (error) {
    mobileExport = {error: error.message || '手机配置生成失败。'};
    throw error;
  }
}

function mobileExportBlockReason(ctx) {
  return !ctx.state.configured ? '完成一键配置后可以导出。'
    : operationsBlocked(ctx) ? '当前有操作或恢复待处理，完成后可以导出。' : '';
}

function mobileCardState(ctx) {
  return JSON.stringify([mobileExportBlockReason(ctx), ctx.state.mobile_export,
    ctx.state.mobile_gist, ctx.state.update?.mobile_gist_auto_sync]);
}

export function refreshMobileExportCard(ctx) {
  const card = document.getElementById('mobile-export-card');
  if (card && card.dataset.mobileState !== mobileCardState(ctx)) card.replaceWith(mobileExportCard(ctx));
}

export function mobileExportCard(ctx) {
  const files = mobileFiles();
  const exportStatus = ctx.state.mobile_export;
  const currentFiles = files.filter(file => !mobileExport?.id ||
    (exportStatus?.id === mobileExport.id && exportStatus.current.includes(file.platform)));
  const blockReason = mobileExportBlockReason(ctx);
  const binding = ctx.state.mobile_gist || {};
  const boundUrl = binding.url || '';
  const gistActionLabel = () => (boundUrl || gistRawBase(syncGistDraft)) ? '更新 Secret Gist' : '创建 Secret Gist';
  const destinationInput = !boundUrl ? ui.input(syncGistDraft, {type: 'url', autocomplete: 'off',
    placeholder: '可粘贴原 Gist 页面或 Raw 地址',
    oninput: event => {
      syncGistDraft = event.target.value;
      create.textContent = gistActionLabel();
    }}) : null;
  function refresh(focusId) {
    const current = document.getElementById('mobile-export-card');
    if (!current) return;
    current.replaceWith(mobileExportCard(ctx));
    if (focusId) document.getElementById(focusId)?.focus({preventScroll: true});
  }
  async function run(mode, action, buttonId) {
    if (mobileProgress || (!ctx.state.configured && !['clean', 'manage'].includes(mode)) || operationsBlocked(ctx)) return;
    buttonId ||= {export: 'generate-mobile-files', prepare: 'create-mobile-gist',
      manage: 'manage-mobile-gist', clean: 'clean-mobile-exports'}[mode];
    const restoreFocus = document.activeElement?.id === buttonId;
    mobileProgress = mode;
    refresh();
    try {
      await action();
    } finally {
      mobileProgress = '';
      refresh(restoreFocus ? buttonId : null);
    }
  }
  const disabled = Boolean(blockReason || mobileProgress);
  const downloads = MOBILE_FILES.map(({platform}) => {
    const id = 'download-mobile-' + platform;
    const button = ui.button(platform === 'ios' ? '下载 iPhone 配置' : '下载安卓配置', {
      variant: 'secondary', icon: 'download', disabled,
      onClick: () => run('export', async () => {
        downloadPlatform = platform;
        let available;
        try {
          available = await generateMobileFiles(ctx);
        } catch {
          return;
        }
        const file = available.find(item => item.platform === platform);
        if (file) {
          const link = ui.el('a', {href: file.href, download: file.filename, hidden: true});
          document.body.append(link);
          try {
            link.click();
          } finally {
            link.remove();
          }
        }
      }, id),
    });
    button.id = id;
    return button;
  });
  const generate = ui.button('生成配置文件', {
    variant: 'secondary', disabled,
    onClick: () => run('export', async () => {
      downloadPlatform = '';
      try {
        await generateMobileFiles(ctx);
      } catch {
        // The card displays the export error.
      }
    }),
  });
  generate.id = 'generate-mobile-files';
  const create = ui.button(gistActionLabel(), {
    variant: 'ink', disabled: disabled || Boolean(binding.error),
    title: '使用已登录的 GitHub CLI，重新生成手机配置并在确认后上传。',
    onClick: () => run('prepare', async () => {
      gistError = '';
      try {
        const address = destinationInput?.value.trim() || '';
        const rawBase = address ? gistRawBase(address) : '';
        if (address && !rawBase) throw new Error('请粘贴有效的 Secret Gist 页面地址或 Raw 地址。');
        const destination = boundUrl || rawBase?.replace('gist.githubusercontent.com', 'gist.github.com').replace(/\/raw\/$/, '') || '';
        downloadPlatform = '';
        const available = await generateMobileFiles(ctx);
        if (!available.length) throw new Error('本次没有可上传的手机配置。');
        const skipped = MOBILE_FILES.filter(file => binding.platforms?.includes(file.platform) &&
          !available.some(item => item.platform === file.platform));
        mobileProgress = 'confirm';
        refresh();
        const confirmed = await ui.confirmDialog({
          title: destination ? '覆盖原 Secret Gist 的同名配置' : '首次创建 Secret Gist',
          description: destination ? '更新目标 ' + destination : '将使用 Windows 上已登录 GitHub CLI 的账号创建并绑定 Secret Gist。',
          items: available.map(({filename}) => ({title: filename})),
          note: [
            skipped.length ? skipped.map(file => file.name).join(' / ') + ' 本次无法生成，远端原文件会保留。' : '',
            binding.auto_sync ? '此订阅已单独开启自动同步。' : '',
          ].filter(Boolean).join(' '),
          requireCheck: '我了解这些文件可能含有节点凭据，持有链接的人可以匿名下载。',
          confirmText: destination ? '覆盖并保持链接' : '创建并生成链接',
        });
        if (!confirmed) return;
        mobileProgress = 'upload';
        refresh();
        const result = await ctx.api.post('/api/mobile-gist', {
          id: available[0].href.split('/')[3], platforms: available.map(({platform}) => platform), confirm: true,
          gist_url: destination,
        }, {timeout: 85000});
        if (!gistRawBase(result.gist_url || '')) throw new Error('未收到有效的 Gist 地址，请到 GitHub Gist 核对。');
        ctx.acceptState('mobile_gist', result.binding);
        syncGistDraft = '';
        const updated = available.map(file => file.name).join(' / ');
        gistNotice = destination ? '已覆盖原 Gist 的 ' + updated + ' 配置。现在可在手机上刷新原订阅。'
          : '已创建并绑定 Secret Gist。以后同步可沿用同一订阅链接。';
      } catch (error) {
        if (!mobileExport?.error) gistError = error.message || '同步 Secret Gist 未完成。';
        if (boundUrl && mobileProgress === 'upload') ctx.refreshManually?.();
      }
    }),
  });
  create.id = 'create-mobile-gist';
  const finderResults = !boundUrl ? ui.el('div', {class: 'stack', 'aria-live': 'polite'}) : null;
  function selectGist(url) {
    syncGistDraft = url;
    destinationInput.value = url;
    create.textContent = gistActionLabel();
  }
  const find = !boundUrl ? ui.button('查找已有 Secret Gist', {
    variant: 'secondary', disabled: Boolean(mobileProgress),
    onClick: async () => {
      const addressBeforeSearch = destinationInput.value;
      find.disabled = true;
      create.disabled = true;
      finderResults.replaceChildren(ui.el('p', {class: 'muted small', text: '正在读取 GitHub CLI 账号中的 Gist…'}));
      try {
        const result = await ctx.api.post('/api/find-mobile-gists');
        const choices = result.gists.filter(({url}) => gistRawBase(url));
        if (!choices.length) {
          finderResults.replaceChildren(ui.note('未找到可识别的手机配置 Gist。知道原地址时，可以直接粘贴。', 'info'));
        } else if (choices.length === 1 && destinationInput.value === addressBeforeSearch) {
          selectGist(choices[0].url);
          finderResults.replaceChildren(ui.note('已填入找到的 Gist。确认更新后才会绑定并上传。', 'info'));
        } else {
          finderResults.replaceChildren(ui.field('选择要沿用链接的 Gist', ui.select([
            {value: '', label: '请选择'},
            ...choices.map(({url, platforms, updated_at}) => ({
              value: url,
              label: [platforms.map(platform => platform === 'ios' ? 'iOS' : 'Android').join(' / '),
                ui.formatTime(updated_at), url.slice(-8)].join(' · '),
            })),
          ], '', selectGist)));
        }
      } catch (error) {
        finderResults.replaceChildren(ui.note(error.message || '无法查找已有 Secret Gist。', 'warn'));
      } finally {
        find.disabled = Boolean(mobileProgress);
        create.disabled = disabled || Boolean(binding.error || mobileProgress);
      }
    },
  }) : null;
  function manageBinding(action) {
    return run('manage', async () => {
      gistError = '';
      gistNotice = '';
      try {
        if (action !== 'disable' && !await ui.confirmDialog(action === 'enable' ? {
          title: '为此订阅开启自动同步',
          description: '规则更新后，可将此配置再次上传到已绑定的 Secret Gist。持有链接的人可以匿名下载文件。',
          requireCheck: '我同意后续自动上传可能包含节点凭据的配置。',
          confirmText: '开启自动同步',
        } : {
          title: '解除手机订阅绑定',
          description: '解除后会停止同步。GitHub 上的文件和原订阅地址仍可访问。',
          confirmText: '解除绑定',
        })) return;
        const result = await ctx.api.post('/api/mobile-gist-manage', {action, gist_url: boundUrl, confirm: true});
        ctx.acceptState('mobile_gist', result);
        if (action === 'unbind') gistLinks = {};
        gistNotice = {enable: '此订阅已开启自动同步。', disable: '此订阅已关闭自动同步。',
          unbind: '已解除本机绑定。'}[action];
      } catch (error) {
        gistError = error.message || '无法更新手机订阅设置。';
      }
    });
  }
  const manage = boundUrl ? ui.button(binding.auto_sync ? '关闭自动同步' : '开启自动同步', {
    variant: 'secondary', disabled, onClick: () => manageBinding(binding.auto_sync ? 'disable' : 'enable'),
  }) : null;
  if (manage) manage.id = 'manage-mobile-gist';
  const cleanup = ui.button('清理旧导出', {variant: 'secondary', disabled: Boolean(mobileProgress || operationsBlocked(ctx)),
    onClick: () => run('clean', async () => {
      gistError = '';
      gistNotice = '';
      try {
        const {removed} = await ctx.api.post('/api/mobile-export-clean');
        gistNotice = removed ? '已清理 ' + removed + ' 批旧导出。' : '没有需要清理的旧导出。';
      } catch (error) {
        gistError = error.message || '清理旧导出失败。';
      }
    }),
  });
  cleanup.id = 'clean-mobile-exports';
  const boundLinks = boundUrl ? subscriptionsFromGist(boundUrl, MOBILE_FILES.filter(file =>
    binding.platforms?.includes(file.platform))) || {} : {};
  const links = {...(boundUrl ? {} : gistLinks), ...boundLinks};
  const progress = {export: '正在生成手机配置…', prepare: '正在准备上传的配置…',
    confirm: '等待确认上传…', upload: '正在同步到 GitHub…', manage: '正在更新订阅设置…', clean: '正在清理旧导出…'}[mobileProgress];
  const chosen = MOBILE_FILES.find(file => file.platform === downloadPlatform);
  const chosenExport = files.find(file => file.platform === downloadPlatform);
  const chosenCurrent = currentFiles.find(file => file.platform === downloadPlatform);
  const iosError = mobileExport?.ios_error;
  const localResult = chosen && !mobileProgress && (chosenExport || (chosen.platform === 'ios' && iosError)) ? mobileFileResult(chosen, {
    href: chosenCurrent?.href, stale: Boolean(chosenExport && !chosenCurrent),
    error: chosen.platform === 'ios' && iosError ? '当前配置无法导出到 iPhone。原因见「进阶设置」。' : '',
    manual: Boolean(chosenCurrent), simple: true,
  }) : null;
  const card = ui.card({title: '导出手机配置', subtitle: '选你的手机，下载配置文件。'},
    ui.el('div', {class: 'mobile-export-buttons'}, downloads),
    blockReason ? ui.note(blockReason, 'info') : null,
    mobileProgress === 'export' ? ui.el('p', {class: 'muted small', role: 'status', text: progress}) : null,
    downloadPlatform && mobileExport?.error ? ui.note('下载未开始，请重试。详情见「进阶设置」。', 'warn') : null,
    localResult,
    ui.disclosure({title: '进阶设置（订阅、故障详情和清理）', key: 'mobile-advanced'},
      ui.el('div', {class: 'mobile-export-option'},
        ui.el('h3', {text: 'GitHub 订阅'}),
        ui.el('p', {class: 'muted small', text: boundUrl ? '更新后继续使用原订阅链接。'
          : '需要已登录的 GitHub CLI。沿用旧链接时，请先填入原地址。'}),
        destinationInput ? ui.field('已有 Secret Gist（可选）', destinationInput) : null,
        finderResults,
        binding.error ? ui.note(binding.error, 'warn') : null,
        ui.el('div', {class: 'mobile-export-buttons'}, create, find, manage,
          boundUrl ? ui.button('解除绑定', {variant: 'secondary', disabled, onClick: () => manageBinding('unbind')}) : null),
        binding.auto_sync && ctx.state.update?.mobile_gist_auto_sync === false
          ? ui.note('规则更新中的总开关已暂停自动同步。', 'info') : null),
      progress && mobileProgress !== 'export' ? ui.el('p', {class: 'muted small', role: 'status', text: progress}) : null,
      mobileExport?.error ? ui.note(mobileExport.error, 'warn') : null,
      gistError ? ui.note(gistError, 'warn') : null,
      gistNotice ? ui.note(gistNotice, 'ok') : null,
      mobileExport?.cleanup_warning ? ui.note(mobileExport.cleanup_warning, 'warn') : null,
      mobileExport?.generated_at && mobileProgress !== 'export' ? ui.el('p', {class: 'muted small',
        text: '来源节点 ' + mobileExport.source + ' · 生成于 ' + ui.formatTime(mobileExport.generated_at)}) : null,
      (files.length || Object.keys(links).length) && mobileProgress !== 'export'
        ? ui.el('div', {class: 'mobile-export-grid', id: 'mobile-export-results',
          tabindex: '-1', 'aria-label': '手机配置结果'}, MOBILE_FILES.map(file => {
            const exported = files.find(item => item.platform === file.platform);
            const current = currentFiles.some(item => item.platform === file.platform);
            const error = file.platform === 'ios' ? iosError : '';
            const link = links[file.platform];
            return exported || error || link ? mobileFileResult(file, {
              href: current ? exported?.href : '', stale: Boolean(exported && !current), error, link,
              status: boundLinks[file.platform] ? binding.statuses?.[file.platform] || {status: 'unknown'} : null,
              gistAlias: binding.ios_alias, manual: current,
            }) : null;
          })) : null,
      ui.el('div', {class: 'mobile-export-buttons'}, cleanup),
      secretGistGuide(currentFiles, ctx.api, refresh, Boolean(boundUrl), generate)));
  card.id = 'mobile-export-card';
  card.dataset.mobileState = mobileCardState(ctx);
  return card;
}

function gistRawBase(address) {
  let url;
  try { url = new URL(address.trim()); } catch { return null; }
  if (url.protocol !== 'https:' || url.username || url.password || url.port) return null;
  const match = url.pathname.match(/^\/([a-z\d-]{1,39})\/([a-f\d]{32}|[a-f\d]{20}|\d{1,20})(\/.*)?$/i);
  if (!match) return null;
  const [, owner, id, suffix = ''] = match;
  if ((url.hostname === 'gist.github.com' && (suffix === '' || suffix === '/')) ||
      (url.hostname === 'gist.githubusercontent.com' && /^\/raw(?:\/|$)/.test(suffix))) {
    return 'https://gist.githubusercontent.com/' + owner + '/' + id + '/raw/';
  }
  return null;
}

function subscriptionsFromGist(address, files) {
  const base = gistRawBase(address);
  return base ? Object.fromEntries(files.map(({platform, filename}) => [platform, base + filename])) : null;
}

async function copyGistText(text, message, fallback = '剪贴板不可用，可以手动选择并复制。') {
  try {
    await navigator.clipboard.writeText(text);
    ui.toast(message, 'ok');
  } catch {
    ui.toast(fallback, 'warn');
  }
}

function mobileFileResult({platform, name, format, downloadLabel, importStep, importHint},
  {href, stale, error, link, status, gistAlias, manual, simple}) {
  const nodes = platform === 'ios' && manual ? mobileExport?.ios_manual || [] : [];
  const subscriptionAliases = link && gistAlias ? nodes.map(entry =>
    ({...entry, alias: entry.alias.replace(/^CCN-[a-f0-9]{12}-/, 'CCN-' + gistAlias + '-')})) : nodes;
  const distinctAliases = subscriptionAliases.some((entry, index) => entry.alias !== nodes[index].alias);
  const omitted = platform === 'ios' && manual ? mobileExport?.omitted_geo_regex : 0;
  const statusText = {current: '订阅已同步', stale: '远端仍是旧版',
    failed: '订阅同步失败', unknown: '订阅版本未确认'}[status?.status];
  const aliases = (entries, prefix = '') => ui.el('ul', {class: 'check-list'}, entries.map(({name, alias, reason}) =>
    ui.el('li', {}, ui.el('span', {text: prefix + name + ' → ' + alias + (reason ? ' · ' + reason : '')}),
      ui.button('复制别名 ' + alias, {variant: 'secondary', size: 'small',
        onClick: () => copyGistText(alias, '已复制节点别名')}))));
  return ui.el('section', {class: 'mobile-export-option'},
    ui.el('h3', {text: simple ? downloadLabel : name}),
    simple ? null : ui.el('p', {class: 'muted small', text: importHint}),
    stale ? ui.note(simple ? '配置已更新，请再点一次上方的下载按钮。' : '本地文件已过期，请重新生成。', 'warn') : null,
    error ? ui.note(error, 'warn') : null,
    nodes.length ? ui.note('先在 Shadowrocket 导入以下原节点，并按对应名称保存。', 'warn') : null,
    nodes.length ? aliases(nodes, distinctAliases ? '本地文件 ' : '') : null,
    distinctAliases ? aliases(subscriptionAliases, '订阅 ') : null,
    omitted ? ui.note(simple ? '部分网站的分流可能与电脑不同。'
      : '已跳过 ' + omitted + ' 条 GeoSite 正则。部分域名可能匹配后续规则，分流与电脑不同。', 'warn') : null,
    simple && href ? ui.el('ol', {class: 'mobile-import-steps'},
      ui.el('li', {text: '在电脑的「下载」文件夹找到配置文件，私下传到自己的手机。'}),
      ui.el('li', {text: importStep})) : null,
    statusText ? ui.note(statusText + (status.last_success ? ' · 上次成功 ' + ui.formatTime(status.last_success) : '') +
      (status.reason ? ' · ' + status.reason : ''), status.status === 'current' ? 'ok' : 'warn') : null,
    link ? ui.field(name + ' 订阅链接', ui.input(link, {readonly: true, spellcheck: 'false'})) : null,
    ui.el('div', {class: 'mobile-export-buttons'},
      href ? ui.el('a', {class: 'btn btn-secondary', href, download: '',
        text: simple ? '下载没开始？点这里' : '下载 ' + name + ' ' + format}) : null,
      link ? ui.button('复制 ' + name + ' 订阅链接', {variant: 'secondary',
        onClick: () => copyGistText(link, '已复制 ' + name + ' 订阅链接')}) : null));
}

function secretGistGuide(files, api, refresh, bound, generate) {
  const validation = ui.el('div', {'aria-live': 'polite'});
  const input = ui.input(gistDraft, {type: 'url', autocomplete: 'off', spellcheck: 'false',
    placeholder: 'https://gist.github.com/用户名/Gist-ID',
    oninput: event => {
      gistDraft = event.target.value;
      validation.replaceChildren();
    }});
  const platform = files.length > 1 ? ui.select([
    {value: 'all', label: 'Android 和 iOS'},
    ...files.map(file => ({value: file.platform, label: file.name})),
  ], 'all', () => {}) : null;
  const copyButtons = files.map(({href, name, format}) => ui.button('复制 ' + name + ' ' + format + ' 内容', {
    variant: 'secondary', size: 'small',
    onClick: async () => {
      try {
        const response = await fetch(href, {mode: 'same-origin', credentials: 'same-origin', cache: 'no-store'});
        if (!response.ok) throw new Error('Export unavailable');
        await copyGistText(await response.text(), '已复制配置内容', '剪贴板不可用，请下载文件后用文本编辑器复制。');
      } catch {
        ui.toast('无法读取导出的文件，请重新生成手机配置。', 'bad');
      }
    },
  }));
  return ui.disclosure({title: '手动创建 Secret Gist', key: 'mobile-secret-gist'},
    ui.note('持有 Secret Gist 链接的人可以匿名下载配置，请妥善保管。', 'warn'),
    ui.el('ol', {class: 'mobile-gist-steps'},
      ui.el('li', {text: '生成配置文件，再复制需要的内容。'}),
      ui.el('li', {text: '在 GitHub 中添加所需文件。Android 文件名为 android-clash-meta.yaml，iOS 文件名为 ios-shadowrocket.conf。'}),
      ui.el('li', {text: '选择 Create secret gist，再把 Gist 页面地址或 Raw 地址粘贴到下方。'})),
    ui.el('div', {class: 'mobile-export-buttons'},
      generate, ...copyButtons,
      ui.button('打开 GitHub Gist', {variant: 'secondary', onClick: async () => {
        try {
          await api.post('/api/open-github-gist');
        } catch (error) {
          ui.toast(error.message || '无法在默认浏览器中打开 GitHub Gist。', 'bad');
        }
      }})),
    files.length ? ui.field('Gist 地址', input) : null,
    platform ? ui.field('Gist 中上传的文件', platform) : null,
    files.length ? ui.button('生成订阅链接', {variant: 'secondary', disabled: Boolean(mobileProgress), onClick: () => {
      const address = input.value.trim();
      const included = platform && platform.value !== 'all' ? files.filter(file => file.platform === platform.value) : files;
      const links = subscriptionsFromGist(address, included);
      if (!links) {
        validation.replaceChildren(ui.note('请粘贴含用户名和 Gist ID 的 GitHub Gist 页面地址或 Raw 地址。', 'warn'));
        return;
      }
      gistDraft = address;
      gistLinks = links;
      gistError = '';
      refresh('mobile-export-results');
    }}) : null,
    validation,
    bound ? MOBILE_FILES.filter(file => gistLinks[file.platform]).map(({platform, name}) =>
      ui.el('div', {class: 'stack'},
        ui.field('手动生成的 ' + name + ' 链接', ui.input(gistLinks[platform], {readonly: true})),
        ui.button('复制手动生成的 ' + name + ' 链接', {variant: 'secondary', size: 'small',
          onClick: () => copyGistText(gistLinks[platform], '已复制订阅链接')}))) : null,
    ui.el('p', {class: 'muted small', text: '更新时编辑同一个 Gist 里的同名文件，再在手机上刷新。'}));
}

// Detailed status belongs in the guide, keeping the network page focused on actions.
export function configurationDetails(ctx) {
  const state = ctx.state;
  if (!state.plan?.exists) return [ui.card({ title: '当前配置详情' },
    ui.empty('尚未配置网络', '完成一次配置后可在这里查看详细状态。',
      ui.button('去一键配置', { variant: 'ink', onClick: () => ctx.navigate('overview') })))];
  const cards = [];
  cards.push(configCard(ctx));
  if (state.verification?.dns_failed) {
    cards.push(ui.note('上次验收的 DNS A/64/65 未通过，可能存在 DNS 泄漏。请在「网络环境」重新运行完整网络验收。', 'bad'));
  }
  const health = state.preview?.current?.health;
  cards.push(ui.card({ title: '当前配置一致性', subtitle: '核对本地配置文件和系统状态。规则或版本不一致请重新配置；系统地区或新增网卡请补充配置。' },
    ...(health?.checks || []).map(row => ui.note(row.title + '：' + (row.status === 'passed' ? '一致' : row.status === 'failed' ? '不一致，请' + row.action : '待检查') + '；当前 ' + (row.title === '配置生成版本' ? (row.current ? '已记录版本' : '旧版未记录') : String(row.current ?? '未知')) + ' → ' + (row.title === '配置生成版本' ? '当前软件版本' : String(row.target)), row.status === 'passed' ? 'info' : 'warn'))));
  cards.push(flowCard());
  const rtc = state.preview?.current?.webrtc;
  cards.push(ui.card({ title: '常用浏览器 WebRTC', subtitle: '配置与补充配置会备份并管理这三款标准浏览器的 WebRTC 策略。' },
    ui.kv((rtc?.browsers || []).map(row => [row.browser + ' 配置', row.configuration_state || '正在读取'])),
    ui.kv((rtc?.browsers || []).map(row => [row.browser, row.state])),
    ui.kv((rtc?.browsers || []).filter(row => row.installation).map(row => [row.browser + ' 扩展安装', row.installation.label])),
    ui.kv([['离线备用扩展', rtc?.bundle?.label || '尚未读取'], ['备用目录', rtc?.bundle?.path || '配置时准备']]),
    rtc?.bundle?.path ? ui.button('复制备用目录', { variant: 'secondary', size: 'small', onClick: async () => {
      try { await navigator.clipboard.writeText(rtc.bundle.path); ui.toast('已复制扩展目录', 'success'); }
      catch { ui.toast('复制失败，请手动复制上方目录', 'error'); }
    } }) : null,
    ui.el('p', { class: 'muted small', text: rtc?.installation_help || '一键配置会自动安排 Chrome、Edge 安装官方商店扩展，已有扩展直接复用。' }),
    ui.el('p', { class: 'muted small', text: rtc?.note || '正在读取 WebRTC 配置。' })));
  return cards;
}

function configCard(ctx) {
  const state = ctx.state;
  const plan = state.plan || {};
  const service = state.service || {};
  const awaitingVerification = state.last_run?.network_tested === false &&
    (!state.verification?.available || (Date.parse(state.verification.time) || 0) < (ui.configurationTime(state) || Infinity));
  return ui.card({
    title: '当前固定配置',
    subtitle: state.configured ? '项目分流配置正在运行。' : '当前未运行项目分流配置。',
    actions: !state.configured ? ui.badge('未生效', 'warn')
      : state.last_run?.success === false ? ui.badge('上次操作未完成', 'bad')
        : ui.badge('已配置', 'ok'),
  },
  ui.kv([
    ['网络验收', awaitingVerification ? '网络未验收' : state.verification?.dns_failed ? 'DNS 验收失败' : state.verification?.available ? ui.verificationLabel(state.verification) + ' · ' + ui.verificationRelation(state) : '尚未验收'],
    ['固定节点', plan.node || '—'],
    ['订阅', plan.subscription || '—'],
    ['出口地区', plan.region || 'SG'],
    ['回滚点', plan.snapshot || '—'],
    ['首次恢复点', state.baseline || '—'],
    ['保持独立', '本项目的固定节点组只包含所选节点，不包含 DIRECT 或自动轮换'],
    ['Clash 服务', service.service || '未知'],
    ['Clash 客户端', service.app_running ? '运行中' : '未运行'],
    ['内核路径', plan.core_program || '—'],
  ]),
  !state.configured ? ui.note('补充配置和规则更新要求当前运行配置仍关联项目脚本。请先重新配置，或从「快照与恢复」恢复到关联快照。', 'warn') : null);
}

function flowCard() {
  return ui.card({ title: '分流顺序', subtitle: '从最窄的例外到最终兜底；顺序用于避免国内域名与 Claude 流量经过不必要的代理。' },
    ui.el('ol', { class: 'flow' }, FLOW.map(([title, text], index) => ui.el('li', {},
      ui.el('span', { class: 'flow-index', text: String(index + 1).padStart(2, '0') }),
      ui.el('div', {}, ui.el('strong', { text: title }), ui.el('p', { text }))))));
}

function verificationCard(ctx) {
  const verification = ctx.state.verification || {};
  const body = [];
  if (!ctx.state.configured) {
    body.push(ui.note('当前未运行项目配置，检查和补充配置暂不可用。', 'warn'));
  }
  if (!verification.available) {
    body.push(ui.el('p', { class: 'muted', text: '还没有验收结果' }));
  } else {
    if (verification.dns_failed) body.push(ui.note('上次 DNS 验收未通过，可重新运行完整网络验收。', 'bad'));
    if (verification.configuration_matches_current === false) body.push(ui.note('网络配置或规则已变化。需要时请重新检查。', 'warn'));
    if (!verification.source_matches_current) body.push(ui.note('该历史报告未对应当前软件版本，可重新验收。', 'warn'));
    body.push(ui.el('div', { class: 'verification-head' },
      ui.badge(ui.verificationLabel(verification), ui.verificationTone(verification)),
      ui.el('span', { class: 'muted', text: '记录时间 ' + ui.formatTime(verification.time) + ' · ' + ui.verificationRelation(ctx.state) }),
      verification.egress?.country ? ui.pill(verification.egress.country + ' · ' + (verification.egress.city || ''), 'neutral') : null));
    body.push(ui.el('div', { class: 'check-grid' }, (verification.checks || []).map((check) => ui.el('div', { class: 'check-item' },
      ui.el('span', { class: 'check-mark ' + ui.statusTone(check.status) }, check.status === 'passed' ? icon('check', 14) : check.status === 'failed' ? icon('close', 14) : icon('info', 14)),
      ui.el('div', {}, ui.el('strong', { text: check.title }), ui.el('small', { text: ui.statusText(check.status) }))))));
    body.push(ui.el('div', { class: 'stage-grid' }, (verification.stages || []).map((stage) => ui.el('div', { class: 'stage-item' },
      ui.el('span', { class: 'status-dot ' + (stage.success ? 'ok' : stage.status === 'unverified' ? 'warn' : stage.tested ? 'bad' : 'warn') }),
      ui.el('div', {}, ui.el('strong', { text: stage.title }), ui.el('small', { text: stage.success ? '已通过' : stage.status === 'unverified' ? '待检查' : stage.tested ? '未通过' : '未记录' }))))));
  }
  return ui.card({ title: '上次网络验收', help: '显示最近一次验收记录及其与当前配置的对应关系。浏览器、虚拟网络、Claude 服务和账号行为未验证。', actions: ui.button('查看日志', { variant: 'ghost', size: 'small', icon: 'list', onClick: () => ctx.navigate('logs') }) }, ...body);
}

function actionsCard(ctx) {
  const state = ctx.state;
  const unavailable = !state.configured;
  const collection = state.collection?.preview;
  const recommendation = state.preview?.current?.health?.recommendation;
  return ui.card({ title: '维护与恢复', help: '这些动作会修改系统设置或短暂中断网络，执行前会再次确认。' },
    recommendation ? ui.note(collection?.stale || collection?.refreshing
      ? '本地状态正在刷新或已经过期，请刷新后再参考操作建议。'
      : recommendation.reason, 'info') : null,
    ui.el('div', { class: 'action-grid' },
      actionRow('只更新分流名单', '只下载并审核规则变化；内容和生成版本均未变化时不重载，不重新应用系统设置。',
        ui.button('去规则更新', { variant: 'secondary', disabled: unavailable || ctx.busy, onClick: () => ctx.navigate('updates') })),
      actionRow('补充配置', '补充防火墙、IPv6、地区和常用浏览器 WebRTC 策略，累积恢复记录。不会关闭浏览器或运行网络测试。',
        ui.button('补充配置', { variant: 'secondary', icon: 'shield', disabled: unavailable || operationsBlocked(ctx), onClick: () => supplement(ctx) })),
      actionRow('更换节点、地区或重新应用配置', '在一键配置页选择精确节点和地区。沿用首次恢复点，生成并校验新配置；不先执行完整恢复。',
        ui.button('去一键配置', { variant: 'secondary', disabled: ctx.busy, onClick: () => ctx.navigate('overview') })),
      actionRow('恢复最早状态并重新配置', '先恢复固定首次恢复点，再沿用当前精确节点完整配置。需要独立本机看护；失败或超时会尝试恢复网络。',
        ui.button('恢复并重新配置', { variant: 'danger', icon: 'alert', disabled: !state.baseline || !state.plan?.node || operationsBlocked(ctx), onClick: () => reconfigure(ctx) })),
    ));
}

function actionRow(title, text, action) {
  return ui.el('div', { class: 'action-row' },
    ui.el('div', { class: 'action-label' }, ui.el('strong', { text: title }), ui.helpTip(text, title + '说明')),
    action);
}

function diagnosticsCard(ctx) {
  return ui.card({ title: '检查与故障演练',
    subtitle: '检查连通性和防火墙，演练节点故障、关闭 TUN 和内核退出，并在恢复后复测。执行期间公网会短暂中断。',
    actions: ui.button('完整网络验收', { variant: 'secondary', icon: 'shield',
      disabled: !ctx.state.configured || operationsBlocked(ctx), onClick: () => audit(ctx) }) });
}

async function supplement(ctx) {
  if (operationsBlocked(ctx)) return;
  const ok = await ctx.confirm({
    title: '执行补充配置？',
    subtitle: '不重新选择节点，不关闭浏览器',
    description: '会扫描当前本地防火墙规则与全部网卡，禁用非项目的出站放行规则、关闭启用的 IPv6 绑定、系统定位、自动时区和自动校时，按所选节点地区设置国家/区域格式/时区，将改动记录到关联快照。入站规则保留。',
    items: [
      { title: '可能影响', text: '新软件、其他 VPN、Docker/WSL、远程控制和依赖 IPv6 的内网资源可能失效；三款标准浏览器的 WebRTC 策略和扩展安装策略也会补充，网页通话可能受影响。' },
      { title: '验收入口', text: '补充配置完成后，可在「检查与故障演练」手动运行完整网络验收。' },
    ],
    note: '需要已有完整配置记录和可校验的原始快照；缺失时会停止，不会覆盖首次基线。',
    confirmText: '补充配置',
    requireCheck: '我已了解补充配置会修改防火墙、网卡、定位、国家/时间设置。',
  });
  if (!ok) return;
  ctx.startJob({ action: 'supplement', confirm: 'SUPPLEMENT' });
}

async function audit(ctx) {
  if (!ctx.state.configured || operationsBlocked(ctx)) return;
  const ok = await ctx.confirm({
    title: '开始完整网络验收？',
    description: '将请求管理员权限，检查国内连通性、海外出口、DNS、IPv6 与防火墙，并演练节点故障、关闭 TUN 和内核退出。恢复后将再次核对网络与防火墙。公网连接会短暂中断，整个过程可能需要数分钟。',
    items: [{title: '独立恢复看护', text: '每个故障阶段由本机看护负责恢复；恢复失败可在「日志与验证」重试。'},
      {title: '检查未完成', text: '补齐管理员权限或修复失败项目后重新检查。'}],
    confirmText: '开始验收',
  });
  if (ok) ctx.startJob({action: 'audit', confirm: 'AUDIT'});
}

async function reconfigure(ctx) {
  if (operationsBlocked(ctx)) return;
  const state = ctx.state;
  const ok = await ctx.confirm({
    title: '恢复最早状态并重新配置？',
    subtitle: '这是一个高风险恢复动作',
    description: '将先恢复固定首次恢复点，再沿用配置记录中的精确节点、订阅和出口地区完整配置。独立本机看护会在失败、超时或主控退出时尝试恢复网络。',
    items: [
      { title: '会中断网络', text: '恢复和配置期间公网会短暂中断；正在下载、会议或远程控制可能断开。' },
      { title: '不会关闭浏览器', text: '默认浏览器都不会被关闭，但网页可能因网络中断失去连接。' },
      { title: '记录位置', text: '.local/reconfigure-runs 保存阶段、日志与恢复结果。' },
    ],
    note: '必须已有配置记录、可校验的首次恢复点和独立看护就绪；不满足时会在改动系统前停止。',
    confirmText: '确认恢复并重新配置',
    danger: true,
    requireCheck: '我已保存工作，并了解网络会短暂中断且恢复不一定成功。',
  });
  if (!ok) return;
  ctx.startJob({ action: 'reconfigure', confirm: 'RECONFIGURE' });
}
