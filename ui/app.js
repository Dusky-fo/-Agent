'use strict';
const $ = id => document.getElementById(id);
let cfg, state, prepared, processRows = [], helpArticles = [], refreshing = false;
let token = location.hash.slice(1) || sessionStorage.getItem('lingsuoToken') || '';
sessionStorage.setItem('lingsuoToken', token);
history.replaceState(null, '', location.pathname);
const pages = {
  home: ['首页', '从一件真实的小事开始。'],
  monitor: ['进程监控', '选择程序，关注启动、退出与恢复。'],
  files: ['文件发送', '先授权范围，再选择文件和接收人。'],
  notifications: ['QQ 通知', '连接、绑定和测试，一步一步完成。'],
  plugins: ['扩展插件', '基础功能独立运行，额外能力按需启用。'],
  settings: ['设置', '常用功能直接进入，技术参数集中管理。'],
  help: ['帮助中心', '每一步都有说明，遇到问题也有下一步。']
};

function node(tag, value, cls) {
  const element = document.createElement(tag);
  if (value !== undefined) element.textContent = value;
  if (cls) element.className = cls;
  return element;
}
function note(target, message, error = false) {
  $(target).textContent = message;
  $(target).classList.toggle('error', error);
}
function toast(message) {
  $('toast').textContent = message; $('toast').hidden = false;
  setTimeout(() => { $('toast').hidden = true; }, 6500);
}
async function api(path, data) {
  const response = await fetch(path, {
    method: data === undefined ? 'GET' : 'POST',
    headers: {Authorization: 'Bearer ' + token, 'Content-Type': 'application/json'},
    body: data === undefined ? undefined : JSON.stringify(data), cache: 'no-store'
  });
  let result;
  try { result = await response.json(); } catch (_) { throw Error('服务未返回有效结果，请从托盘重新打开面板。'); }
  if (!response.ok || result.error) throw Error(result.error || '操作未完成，请查看帮助中的常见问题。');
  return result;
}
async function action(fn, button, feedback) {
  if (button?.disabled) return;
  if (button) button.disabled = true;
  if (feedback) note(feedback, '正在处理，请稍候…');
  try { await fn(); }
  catch (error) {
    if (feedback) note(feedback, error.message, true);
    else toast(error.message);
  } finally { if (button) button.disabled = false; }
}
function button(label, fn, primary = false) {
  const b = node('button', label, primary ? 'primary' : '');
  b.type = 'button'; b.onclick = () => action(fn, b); return b;
}
function empty(parent, message, label, page) {
  parent.replaceChildren(node('div', message, 'empty'));
  if (label) parent.append(button(label, () => show(page)));
}
function lines(id) { return $(id).value.split(/\r?\n/).map(v => v.trim()).filter(Boolean); }
function show(id) {
  if (!pages[id]) id = 'home';
  for (const page in pages) { $(page).hidden = page !== id; $('nav-' + page).classList.toggle('active', page === id); }
  $('title').textContent = pages[id][0]; $('subtitle').textContent = pages[id][1];
  if (id === 'plugins') action(loadPlugins);
  if (id === 'help') renderHelp();
}
function showHelp(id) {
  $('helpQuery').value = ''; show('help');
  const article = $('help-' + id);
  if (article) { article.open = true; article.scrollIntoView({block: 'start', behavior: 'smooth'}); }
}
for (const id in pages) {
  const b = button(pages[id][0], () => show(id)); b.id = 'nav-' + id; $('nav').append(b);
}
document.addEventListener('click', event => {
  const target = event.target.closest('button');
  if (!target) return;
  if (target.dataset.go) show(target.dataset.go);
  if (target.dataset.help) showHelp(target.dataset.help);
  if (target.dataset.copy) action(async () => {
    try { await navigator.clipboard.writeText(target.dataset.copy); toast('已复制指令：' + target.dataset.copy); }
    catch (_) { toast('请手动向机器人发送：' + target.dataset.copy); }
  }, target);
});

async function loadConfig() {
  cfg = await api('/api/config');
  $('monitorEnabled').checked = cfg.monitor.enabled;
  $('processNames').value = cfg.monitor.process_names.join('\n');
  $('interval').value = cfg.monitor.interval;
  $('allowDesktop').checked = cfg.files.allow_desktop;
  $('roots').value = cfg.files.roots.join('\n'); $('maxMb').value = cfg.files.max_mb;
  $('qqEnabled').checked = cfg.qq.enabled; $('appid').value = cfg.qq.appid;
  $('secretState').textContent = cfg.qq.has_secret ? '已保存密钥，不回显；同一应用留空可保留。' : '尚未保存密钥。';
  $('cooldown').value = cfg.notifications.cooldown; $('attempts').value = cfg.notifications.max_attempts;
  $('port').value = cfg.panel.port; renderProcessPicker();
}
function updateRecipients() {
  const users = state.users;
  for (const id of ['recipient', 'testRecipient']) {
    const selected = $(id).value;
    $(id).replaceChildren();
    if (!users.length) { const option = node('option', '尚未绑定接收人'); option.value = ''; $(id).append(option); }
    for (const {user} of users) { const option = node('option', '本人接收人 · …' + user.slice(-8)); option.value = user; $(id).append(option); }
    if (users.some(row => row.user === selected)) $(id).value = selected;
  }
}
function renderHome(events) {
  const steps = [
    ['qq_connected', '连接自己的 QQ 机器人', state.qq.message, 'notifications', '连接 QQ'],
    ['receiver_bound', '绑定接收通知的本人', '向机器人发送“开启”，在本机核对配对码并确认。', 'notifications', '前往绑定'],
    ['monitor_added', '添加一个监控程序', '本地监控不需要先配置 QQ。', 'monitor', '选择程序'],
    ['files_authorized', '授权文件共享范围', '文件发送可稍后配置；不会自动开放桌面。', 'files', '设置共享范围']
  ];
  $('todoList').replaceChildren();
  for (const [key, title, description, page, label] of steps) {
    const row = node('div', undefined, 'task-row'); const content = node('div');
    content.append(node('div', (state.todo[key] ? '✓ ' : '○ ') + title), node('p', description));
    row.append(content, button(state.todo[key] ? '查看' : label, () => show(page))); $('todoList').append(row);
  }
  $('setupProgress').textContent = Object.values(state.todo).filter(Boolean).length + ' / 4 项已就绪';
  $('homeMonitor').replaceChildren(node('div', state.local_running ? (state.monitor.enabled ? '本地监控服务运行中' : '监控已暂停') : '本地监控服务未启动'));
  $('homeMonitor').append(node('p', state.monitor.process_names.length ? '已选择：' + state.monitor.process_names.join('、') : '尚未选择程序，请先添加监控。'));
  renderEvents($('homeEvents'), events.slice(0, 3));
}
function renderEvents(parent, events) {
  parent.replaceChildren();
  if (!events.length) { parent.append(node('div', '暂无记录。添加监控后，真实的运行变化会出现在这里。', 'empty')); return; }
  for (const row of events) { const div = node('div', undefined, 'event'); div.append(node('time', new Date(row.ts * 1000).toLocaleString()), node('div', row.text)); parent.append(div); }
}
function renderMonitor() {
  const box = $('processState'); box.replaceChildren();
  if (state.monitor_error) box.append(node('p', state.monitor_error + '。检查系统权限，稍后重新读取。', 'error-text'));
  if (!state.monitor.process_names.length) { empty(box, '尚未添加监控任务。先从当前进程中选择程序。', '选择监控程序', 'monitor'); return; }
  if (!state.monitor.enabled) box.append(node('div', '监控已暂停；重新开启并保存后建立新基线。', 'notice'));
  for (const name of state.monitor.process_names) {
    const matched = state.processes.filter(p => p.name.toLowerCase() === name.toLowerCase());
    const row = node('div', undefined, 'task-row');
    row.append(node('div', name), node('span', !state.monitor.enabled ? '已暂停' : matched.length ? '运行中 · PID ' + matched.map(p => p.pid).join('、') : '尚未采集到匹配进程', 'tag'));
    box.append(row);
  }
}
function renderPairing() {
  const pairing = state.pairing;
  $('pairingState').textContent = pairing.active ? '绑定窗口已开启，剩余 ' + pairing.seconds_left + ' 秒。请在 QQ 发“开启”，然后核对两边的配对码。' : '绑定窗口尚未开启或已结束。接收人不会因发送消息而自动获得授权。';
  $('pairingRequests').replaceChildren();
  for (const row of pairing.requests) {
    const div = node('div', undefined, 'pair-request');
    div.append(node('div', '请核对 QQ 中相同的配对码'), node('div', row.code, 'pair-code'), node('p', '请求身份尾号：…' + row.user.slice(-8)));
    div.append(button('这是我，确认绑定', async () => {
      if (!confirm('请确认你自己的 QQ 中显示配对码 ' + row.code + '。不要确认陌生人的请求。')) return;
      const r = await api('/api/pairing/confirm', {id: row.id, code: row.code});
      note('bindingFeedback', r.message); await refresh();
    }, true)); $('pairingRequests').append(div);
  }
  $('boundUsers').replaceChildren();
  if (!state.users.length) empty($('boundUsers'), '尚未绑定接收人。无需手动复制 OpenID，使用上方配对流程即可。');
  for (const row of state.users) {
    const div = node('div', undefined, 'user-row'); const info = node('div');
    info.append(node('div', '接收人 · …' + row.user.slice(-8)), node('div', row.subscribed ? '通知已订阅' : '通知已退订', 'hint'));
    const actions = node('div', undefined, 'actions');
    actions.append(button(row.subscribed ? '暂停通知' : '开启通知', async () => { await api('/api/qq/subscription', {user: row.user, enabled: !row.subscribed}); await refresh(); }));
    actions.append(button('撤销授权', async () => { if (!confirm('撤销该接收人的本机访问与通知权限？')) return; const r = await api('/api/qq/remove-user', {user: row.user}); note('bindingFeedback', r.message); await refresh(); }));
    div.append(info, actions); $('boundUsers').append(div);
  }
}
function renderTests() {
  const box = $('testResults'); box.replaceChildren();
  if (!state.tests.length) { empty(box, '尚未发送测试通知。绑定本人后，发送一条带编号的真实通知进行验证。'); return; }
  for (const test of state.tests) {
    const row = node('div', undefined, 'test-item');
    const label = test.confirmed ? '用户已确认收到' : test.accepted ? 'QQ 平台已接受，等待本人确认' : ({pending: '已入队，等待提交', failed: '提交失败，重试次数已用尽', cancelled: '已取消'}[test.state] || '等待结果');
    row.append(node('div', '测试编号 ' + test.id + ' · ' + label));
    if (test.error) row.append(node('p', test.error, 'error-text'));
    if (test.accepted && !test.confirmed) row.append(button('我确认已收到此编号通知', async () => {
      if (!confirm('请先在手机 QQ 中核对通知编号 ' + test.id + '，确实收到后再确认。')) return;
      const r = await api('/api/notify/confirm', {id: test.id}); note('testFeedback', r.message); await refresh();
    }));
    box.append(row);
  }
}
async function refresh() {
  if (refreshing) return;
  refreshing = true;
  try {
    const [fresh, events] = await Promise.all([api('/api/status'), api('/api/history')]); state = fresh;
    $('globalError').hidden = true;
    $('connection').textContent = state.qq.connected ? 'QQ 消息通道已连接' : state.qq.state === 'connecting' ? 'QQ 正在连接' : '本地运行 · QQ 未连接';
    $('qqState').textContent = state.qq.message; $('desktopPath').textContent = '系统实际桌面：' + state.desktop;
    $('sharedRoots').textContent = state.roots.length ? '当前有效共享范围：' + state.roots.join('；') : '当前没有可用共享目录，请检查目录是否存在并保存。';
    $('dataPath').textContent = '本机数据目录：' + state.data_dir; $('pluginPath').textContent = '插件安装目录：' + state.data_dir + '\\plugins';
    $('filePrerequisite').textContent = !state.users.length ? '下一步：先到 QQ 通知页绑定接收人。' : !state.qq.connected ? 'QQ 尚未连接，发送前请到 QQ 通知页检查连接。' : '发送前会展示完整接收人身份，供你核对。';
    $('startPairing').disabled = !state.qq.connected;
    $('queue').textContent = Object.entries(state.queue).map(([k,v]) => ({pending:'待发送',sent:'平台已接受',failed:'失败',cancelled:'已取消'}[k] || k) + '：' + v).join(' / ') || '暂无通知';
    updateRecipients(); renderHome(events); renderMonitor(); renderPairing(); renderTests(); renderEvents($('history'), events);
  } finally { refreshing = false; }
}
async function saveSection(section, values, feedback) {
  const result = await api('/api/settings', {section, values});
  if (feedback) note(feedback, result.message); await refresh(); return result;
}

function renderProcessPicker() {
  const names = new Map();
  for (const p of processRows) { const key = p.name.toLowerCase(); if (!names.has(key)) names.set(key, {name:p.name, count:0}); names.get(key).count++; }
  const chosen = new Set(lines('processNames').map(n => n.toLowerCase()));
  $('selectedCount').textContent = '已选择 ' + chosen.size + ' 个程序';
  if (!processRows.length) return;
  const query = $('processQuery').value.trim().toLowerCase(); const box = $('processPicker'); box.replaceChildren();
  const matches = [...names.values()].filter(p => p.name.toLowerCase().includes(query)).sort((a,b) => a.name.localeCompare(b.name));
  if (!matches.length) { empty(box, '没有匹配的运行进程。换个关键字，或在下方手动添加程序文件名。'); return; }
  for (const item of matches) {
    const label = node('label', undefined, 'process-item'); const input = document.createElement('input'); input.type = 'checkbox'; input.checked = chosen.has(item.name.toLowerCase());
    input.onchange = () => { const selected = new Map(lines('processNames').map(n => [n.toLowerCase(), n])); if (input.checked) selected.set(item.name.toLowerCase(), item.name); else selected.delete(item.name.toLowerCase()); $('processNames').value = [...selected.values()].join('\n'); $('selectedCount').textContent = '已选择 ' + selected.size + ' 个程序'; };
    label.append(input, node('span', item.name), node('small', item.count + ' 个实例')); box.append(label);
  }
}
$('refreshProcesses').onclick = () => action(async () => { processRows = await api('/api/processes'); renderProcessPicker(); note('monitorFeedback', '已读取 ' + processRows.length + ' 个进程；勾选后保存。'); }, $('refreshProcesses'), 'monitorFeedback');
$('processQuery').oninput = renderProcessPicker; $('processNames').oninput = renderProcessPicker;
$('saveMonitor').onclick = () => action(() => saveSection('monitor', {enabled:$('monitorEnabled').checked, process_names:lines('processNames'), interval:Number($('interval').value)}, 'monitorFeedback'), $('saveMonitor'), 'monitorFeedback');
$('saveFiles').onclick = () => action(() => saveSection('files', {allow_desktop:$('allowDesktop').checked, roots:lines('roots'), max_mb:Number($('maxMb').value)}, 'filesFeedback'), $('saveFiles'), 'filesFeedback');

function qqValues() { return {enabled:$('qqEnabled').checked, appid:$('appid').value.trim(), secret:$('secret').value.trim()}; }
function confirmBotChange(values) {
  return !cfg.qq.appid || values.appid === cfg.qq.appid || confirm('更换机器人将撤销旧应用的接收人绑定、订阅和待发通知，需要重新配对。是否继续？');
}
$('checkQQ').onclick = () => action(async () => { const result = await api('/api/qq/check', qqValues()); note('qqFeedback', result.message); }, $('checkQQ'), 'qqFeedback');
$('saveQQ').onclick = () => action(async () => { const values = qqValues(); if (!confirmBotChange(values)) { note('qqFeedback', '已取消更换机器人。'); return; } await saveSection('qq', values, 'qqFeedback'); $('secret').value=''; const c = await api('/api/config'); cfg.qq = c.qq; $('secretState').textContent = c.qq.has_secret ? '密钥已保存，不回显。' : '尚未保存密钥。'; }, $('saveQQ'), 'qqFeedback');
$('connectQQ').onclick = () => action(async () => {
  const values = qqValues(); values.enabled = true;
  if (!confirmBotChange(values)) { note('qqFeedback', '已取消更换机器人。'); return; }
  await api('/api/qq/check', values);
  await saveSection('qq', values);
  cfg.qq = (await api('/api/config')).qq;
  $('secret').value = ''; $('qqEnabled').checked = true;
  $('secretState').textContent = '密钥已保存，不回显。';
  const result = await api('/api/qq/connect', {}); note('qqFeedback', result.message); await refresh();
}, $('connectQQ'), 'qqFeedback');
$('disconnectQQ').onclick = () => action(async () => { const r = await api('/api/qq/disconnect', {}); note('qqFeedback', r.message); await refresh(); }, $('disconnectQQ'), 'qqFeedback');
$('startPairing').onclick = () => action(async () => { await api('/api/pairing/start', {}); note('bindingFeedback', '现在请在 QQ 私聊机器人发送“开启”，然后核对配对码。'); await refresh(); }, $('startPairing'), 'bindingFeedback');
$('cancelPairing').onclick = () => action(async () => { const r = await api('/api/pairing/cancel', {}); note('bindingFeedback', r.message); await refresh(); }, $('cancelPairing'), 'bindingFeedback');
$('testNotify').onclick = () => action(async () => { const r = await api('/api/notify/test', {user:$('testRecipient').value}); note('testFeedback', r.message + ' 编号：' + r.test.id); await refresh(); }, $('testNotify'), 'testFeedback');
$('refreshHistory').onclick = () => action(refresh, $('refreshHistory'));
$('saveSettings').onclick = () => action(async () => {
  await saveSection('notifications', {cooldown:Number($('cooldown').value), max_attempts:Number($('attempts').value)});
  const r = await saveSection('panel', {port:Number($('port').value)}); note('settingsFeedback', r.message);
}, $('saveSettings'), 'settingsFeedback');

async function searchFiles(desktop) {
  const rows = await api('/api/files?q=' + encodeURIComponent($('query').value) + '&desktop=' + (desktop?'1':'0'));
  const box = $('fileResults'); box.replaceChildren(); box.classList.remove('empty');
  if (!rows.length) { empty(box, '没有找到可发送文件。请检查授权目录、关键字、文件大小与深度限制。', '查看文件帮助', 'help'); note('filesFeedback', '搜索已完成，没有匹配的可发送文件。'); return; }
  for (const row of rows) {
    const div = node('div', undefined, 'file-row'); const info = node('div');
    info.append(node('strong', row.name), node('div', row.path + ' · ' + row.size.toLocaleString() + ' 字节', 'hint'));
    div.append(info, button('选择发送', async () => {
      if (!$('recipient').value) { show('notifications'); throw Error('请先在 QQ 通知页绑定本人接收人。'); }
      prepared = await api('/api/files/prepare', {path:row.path,user:$('recipient').value});
      const details = $('confirmDetails'); details.replaceChildren();
      details.append(node('div', '文件：' + prepared.name), node('div', '大小：' + prepared.size.toLocaleString() + ' 字节'), node('div', '接收人 OpenID：' + prepared.user), node('div', '路径：' + row.path));
      $('confirmation').showModal();
    }, true)); box.append(div);
  }
  note('filesFeedback', '已找到 ' + rows.length + ' 个文件；选择后核对接收人再发送。');
}
$('searchFiles').onclick = () => action(() => searchFiles(false), $('searchFiles'), 'filesFeedback');
$('desktopFiles').onclick = () => action(() => searchFiles(true), $('desktopFiles'), 'filesFeedback');
$('cancelSend').onclick = () => { prepared = null; $('confirmation').close(); };
$('confirmation').addEventListener('cancel', () => { prepared = null; });
$('confirmSend').onclick = () => action(async () => {
  const item = prepared; prepared = null; if (!item) return;
  $('confirmation').close();
  const result = await api('/api/files/send', {confirmation:item.confirmation}); note('filesFeedback', result.message + '；不代表接收人已阅读。');
}, $('confirmSend'), 'filesFeedback');

async function loadPlugins() {
  const rows = await api('/api/plugins'); const box = $('pluginList'); box.replaceChildren();
  if (!rows.length) { empty(box, '没有安装插件。监控、通知和文件发送可以直接使用。', '查看安装帮助', 'help'); return; }
  for (const row of rows) {
    const div = node('div', undefined, 'pair-request');
    div.append(node('h3', row.name + ' · ' + (row.version || '未知版本')), node('p', '权限声明：' + ((row.permissions || []).join('、') || '无额外权限声明')));
    div.append(node('div', row.error || (row.loaded ? '已加载' : row.enabled ? '等待重启或重新信任' : '未启用')));
    if (row.hash) div.append(button(row.enabled ? '禁用' : '信任并启用', async () => {
      if (!row.enabled && !confirm('确认已审查并信任此插件？它具有宿主程序权限；启用后重启生效。')) return;
      const r = await api('/api/plugins/configure', {id:row.id,enabled:!row.enabled,trusted:!row.enabled}); toast(r.message); await loadPlugins();
    }));
    for (const tool of row.tools || []) if (Object.keys(tool.params || {}).length === 0) div.append(button(tool.description, async () => { const r = await api('/api/plugins/call', {id:row.id,tool:tool.name,args:{}}); toast(typeof r.result === 'object' ? (r.result.message || JSON.stringify(r.result)) : String(r.result)); }));
    box.append(div);
  }
}
$('refreshPlugins').onclick = () => action(loadPlugins, $('refreshPlugins'));
function renderHelp() {
  const query = $('helpQuery').value.trim().toLowerCase(); const box = $('helpArticles'); box.replaceChildren();
  for (const article of helpArticles) {
    if (query && !JSON.stringify(article).toLowerCase().includes(query)) continue;
    const details = node('details', undefined, 'help-article'); details.id = 'help-' + article.id;
    details.append(node('summary', article.title));
    for (const paragraph of article.paragraphs || []) details.append(node('p', paragraph));
    if (article.steps) { const list = node('ol'); for (const step of article.steps) list.append(node('li', step)); details.append(list); }
    for (const command of article.commands || []) details.append(button('复制：' + command, async () => { try { await navigator.clipboard.writeText(command); toast('已复制：' + command); } catch (_) { toast('请手动输入：' + command); } }));
    if (article.page) details.append(button('前往' + pages[article.page][0], () => show(article.page)));
    for (const link of article.links || []) { const a = node('a', link.title, 'button'); a.href = link.url; a.target = '_blank'; a.rel = 'noopener noreferrer'; details.append(a); }
    box.append(details);
  }
  if (!box.childNodes.length) empty(box, '没有找到相关帮助。试试“QQ”“文件”或“权限”。');
}
$('helpQuery').oninput = renderHelp;
show('home');
action(async () => { await loadConfig(); helpArticles = await api('/api/help'); renderHelp(); await refresh(); });
setInterval(() => { refresh().catch(error => { $('globalError').hidden = false; $('globalError').textContent = error.message; }); }, 3000);
