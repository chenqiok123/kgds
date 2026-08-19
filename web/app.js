/* KGDS v4 — 云端版：账号系统 + 响应式 */
var API = '';
var state = {
  user: null,          // {id, name, email, token}
  authMode: 'register', // 'register' | 'login'
  profile:{}, questions:[], answers:{}, currentQ:0,
  nodes:[], edges:[], report:null, graphInstance:null,
  blinkTimer: null, selectedLevels: ['foundation','advanced'],
  foundationSize: 'compact',  // 兼容旧字段，配额已固定 51/38/30
  graphMode: 'report', // 'report' | 'full' — 图谱着色模式
  internalCategory: null,  // 内部知识单选
  internalCounts: {},      // 内部知识各方向题数: {方向: 数量}
  selectedProducts: [],    // 产品知识-复选的产品名列表
  learningShown: {}        // 学习推荐去重: {node_id: [tip_indices]}
};

// ── 产品知识：14 款产品（与 product_tests.json / 图谱 PROD 节点一致）──
var PRODUCTS = [
  {name:'优医保2.0', cat:'医疗险'},
  {name:'百万能量守护星', cat:'医疗险'},
  {name:'如意东风', cat:'重疾险'},
  {name:'润泽恒赢', cat:'年金险'},
  {name:'心裕人生', cat:'年金险'},
  {name:'心赢人生', cat:'年金险'},
  {name:'聚盈宝', cat:'年金险'},
  {name:'聚能宝', cat:'年金险'},
  {name:'隽永世家', cat:'终身寿险'},
  {name:'传世金樽', cat:'终身寿险'},
  {name:'鎏金世家', cat:'终身寿险'},
  {name:'勿忘我惠享版2025', cat:'护理险'},
  {name:'勿忘我爱永驻', cat:'护理险'},
  {name:'活力腾腾', cat:'意外险'}
];

// ── 全局 Toast 提示（替代 alert，区分错误/成功/信息）──
function toast(msg, type) {
  type = type || 'info';
  var icons = { info:'ℹ️', error:'⚠️', success:'✅' };
  var t = document.createElement('div');
  t.className = 'toast toast-' + type;
  t.innerHTML = '<span>' + (icons[type] || icons.info) + '</span> ' + msg;
  document.body.appendChild(t);
  requestAnimationFrame(function(){ t.classList.add('show'); });
  setTimeout(function(){ t.classList.remove('show'); setTimeout(function(){ if (t.parentNode) t.parentNode.removeChild(t); }, 300); }, 3200);
}

// ── Init ──
// 30天自动登录有效期（滑动过期：每次打开刷新起点）
var AUTO_LOGIN_MS = 30 * 24 * 60 * 60 * 1000;
(function init() {
  var stored = localStorage.getItem('kgds_user');
  if (stored) {
    try {
      var _u = JSON.parse(stored);
      // 有 token 且（无 login_at 的旧登录态 或 未超过30天）→ 保留并滑动刷新
      if (_u && _u.token && (!_u.login_at || (Date.now() - _u.login_at <= AUTO_LOGIN_MS))) {
        state.user = _u;
        _u.login_at = Date.now();
        try { localStorage.setItem('kgds_user', JSON.stringify(_u)); } catch(e) {}
      } else {
        localStorage.removeItem('kgds_user');
      }
    } catch(e) { localStorage.removeItem('kgds_user'); }
  }
  updateHeader();
  loadMeta();          // 加载行业/岗位/企业下拉
  refreshAuthUI();     // 按当前模式显示/隐藏注册字段
  if (state.user) {
    showPhase('profile');
    loadInternalCategories();
    loadDaily();  // 当日首次进入跳出每日一题弹窗
  }
  // 支持 URL 参数 ?tab=graph 直接进入图谱（兔扑导航跳转）
  var _navParams = new URLSearchParams(window.location.search);
  if (_navParams.get('tab') === 'graph') switchTab('graph');
})();

// ── 内部知识方向题数 ──
function loadInternalCategories() {
  if (!state.user) return;
  fetch(API + '/api/internal/categories', { method: 'POST', headers: authHeaders(), body: '{}' })
    .then(function(r){ return r.json(); })
    .then(function(d){
      if (d && d.categories) {
        state.internalCounts = {};
        d.categories.forEach(function(c){ state.internalCounts[c.name] = c.question_count; });
        updateInternalUI();
        updateStartBtn();
      }
    })
    .catch(function(e){ console.error('loadInternalCategories:', e); });
}

// ── Auth ──
function updateHeader() {
  var d = document.getElementById('user-display');
  var b = document.getElementById('btn-logout');
  if (state.user) {
    d.textContent = '👤 ' + state.user.name;
    b.style.display = 'inline';
  } else {
    d.textContent = '';
    b.style.display = 'none';
  }
}

// ── 多行业元数据（注册页三级下拉）──
var META = { industries: [], occupations: [], companies: [] };

function loadMeta() {
  fetch(API + '/api/meta')
    .then(function(r){ return r.json(); })
    .then(function(d){
      if (d.industries) META.industries = d.industries;
      if (d.occupations) META.occupations = d.occupations;
      if (d.companies) META.companies = d.companies;
      fillIndustrySelect();
    })
    .catch(function(e){ console.error('loadMeta:', e); });
}

function fillIndustrySelect() {
  var sel = document.getElementById('f-industry');
  if (!sel) return;
  sel.innerHTML = '';
  var opt = document.createElement('option');
  opt.value = ''; opt.textContent = '请选择行业';
  sel.appendChild(opt);
  META.industries.forEach(function(ind){
    var o = document.createElement('option');
    o.value = ind.key; o.textContent = ind.name;
    sel.appendChild(o);
  });
  sel.onchange = function(){ fillOccupationSelect(); fillCompanySelect(); };
  fillOccupationSelect();
  fillCompanySelect();
}

function fillOccupationSelect() {
  var sel = document.getElementById('f-occupation');
  if (!sel) return;
  var ind = document.getElementById('f-industry').value;
  sel.innerHTML = '';
  var o0 = document.createElement('option');
  o0.value = ''; o0.textContent = '不选择';
  sel.appendChild(o0);
  META.occupations.forEach(function(oc){
    if (ind && oc.industry_key !== ind) return;
    var o = document.createElement('option');
    o.value = oc.name; o.textContent = oc.name;
    sel.appendChild(o);
  });
}

function fillCompanySelect() {
  var sel = document.getElementById('f-company');
  if (!sel) return;
  var ind = document.getElementById('f-industry').value;
  sel.innerHTML = '';
  var o0 = document.createElement('option');
  o0.value = ''; o0.textContent = '不加入企业';
  sel.appendChild(o0);
  META.companies.forEach(function(cp){
    if (ind && cp.industry_key !== ind) return;
    var o = document.createElement('option');
    o.value = cp.name; o.textContent = cp.name;
    sel.appendChild(o);
  });
  sel.onchange = function(){ togglePhoneField(); };
}

function togglePhoneField() {
  var company = document.getElementById('f-company').value;
  var phoneField = document.getElementById('field-phone');
  if (phoneField) phoneField.style.display = company ? 'block' : 'none';
}

function fieldWrapper(id) {
  var el = document.getElementById(id);
  if (!el) return null;
  return el.classList.contains('field') ? el : el.parentElement;
}

function refreshAuthUI() {
  var isLogin = state.authMode === 'login';
  var title = document.getElementById('login-title');
  var label = document.getElementById('field-email').querySelector('label');
  var btn = document.getElementById('btn-auth');
  var sw = document.getElementById('switch-mode');
  var sub = document.getElementById('sub-hint');
  fieldWrapper('f-name').style.display = isLogin ? 'none' : 'block';
  fieldWrapper('field-industry').style.display = isLogin ? 'none' : 'block';
  fieldWrapper('field-occupation').style.display = isLogin ? 'none' : 'block';
  fieldWrapper('field-company').style.display = isLogin ? 'none' : 'block';
  togglePhoneField();
  if (isLogin) {
    title.textContent = '🔑 登录 KGDS';
    label.textContent = '邮箱';
    btn.textContent = '登录';
    sw.innerHTML = '还没有账号？<a onclick="toggleAuthMode()">立即注册</a>';
    if (sub) sub.innerHTML = '欢迎回来';
  } else {
    title.textContent = '👋 欢迎使用 KGDS';
    label.textContent = '邮箱';
    btn.textContent = '🚀 开始测评';
    sw.innerHTML = '已有账号？<a onclick="toggleAuthMode()">直接登录</a>';
    if (sub) sub.innerHTML = '知识图谱诊断系统<br>首次使用将自动注册';
  }
}

function toggleAuthMode() {
  state.authMode = state.authMode === 'register' ? 'login' : 'register';
  refreshAuthUI();
}

function handleAuth() {
  var name = document.getElementById('f-name').value.trim();
  var email = document.getElementById('f-email').value.trim();
  if (!email) { toast('请输入邮箱地址', 'error'); return; }
  if (!email.includes('@') || !email.includes('.')) { toast('请输入有效的邮箱地址', 'error'); return; }

  var endpoint, body;
  if (state.authMode === 'register') {
    if (!name) { toast('请输入姓名', 'error'); return; }
    var industry = document.getElementById('f-industry').value;
    var occupation = document.getElementById('f-occupation').value;
    var company = document.getElementById('f-company').value;
    var phone = document.getElementById('f-phone').value.trim();
    if (!industry) { toast('请选择行业', 'error'); return; }
    if (company && !phone) { toast('加入企业需填写手机号', 'error'); return; }
    endpoint = '/api/register';
    body = { name: name, email: email, industry: industry, occupation: occupation, company: company, phone: phone };
  } else {
    endpoint = '/api/login';
    body = { email: email };
  }
  var btn = document.getElementById('btn-auth');
  btn.disabled = true; btn.textContent = '处理中...';

  fetch(API + endpoint, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data.error) { toast(data.error, 'error'); btn.disabled = false; btn.textContent = state.authMode==='register'?'🚀 开始测评':'登录'; return; }
      state.user = data.user;
      state.user.login_at = Date.now();  // 记录登录时间，作为30天自动登录有效期起点
      localStorage.setItem('kgds_user', JSON.stringify(state.user));
      updateHeader();
      loadLatestSession();  // 加载最新诊断记录（图谱着色用）
      loadInternalCategories();
      loadDaily();  // 当日首次进入跳出每日一题弹窗
      showPhase('profile');
    })
    .catch(function(e) { toast('网络异常，请检查连接后重试', 'error'); btn.disabled = false; btn.textContent = state.authMode==='register'?'🚀 开始测评':'登录'; });
}

function logout() {
  if (confirm('确定退出登录？')) {
    state.user = null;
    localStorage.removeItem('kgds_user');
    state.profile = {}; state.questions = []; state.answers = {}; state.currentQ = 0; state.report = null;
    state.graphMode = 'full';
    var btn = document.getElementById('btn-full-graph');
    if (btn) { btn.style.display = 'none'; btn.classList.remove('active'); }
    updateHeader();
    showPhase('login');
    document.getElementById('report-section').innerHTML = '';
    document.getElementById('done-state').style.display = '';
    if (state.blinkTimer) { clearInterval(state.blinkTimer); state.blinkTimer = null; }
  }
}

function authHeaders() {
  return state.user ? { 'Authorization': 'Bearer ' + state.user.token, 'Content-Type': 'application/json' } : { 'Content-Type': 'application/json' };
}

// ── Navigation ──
function loadLatestSession() {
  if (!state.user) return;
  fetch(API + '/api/sessions?' + Date.now(), { headers: authHeaders() })
    .then(function(r) { return r.json(); })
    .then(function(sessions) {
      if (sessions && sessions.length > 0) {
        var latest = sessions[0];
        // 还原 node_status 和 layer_stats
        if (latest.node_status) {
          state.report = {
            total_score: latest.overall_score || 0,
            total_correct: latest.total_correct || 0,
            total_questions: latest.total_questions || 0,
            node_status: latest.node_status,
            layer_stats: latest.layer_stats || computeLayerStats(latest.node_status),
            profile: latest.profile || {},
            session_id: latest.id
          };
        }
      }
    })
    .catch(function(e) { console.error('loadLatestSession:', e); });
}

function computeLayerStats(ns) {
  var ls = { foundation:{correct:0,total:0}, advanced:{correct:0,total:0}, transcendent:{correct:0,total:0} };
  Object.values(ns||{}).forEach(function(s){
    if (ls[s.layer]) { ls[s.layer].correct += (s.correct||0); ls[s.layer].total += (s.total||0); }
  });
  return ls;
}

function switchTab(name) {
  var tabs = document.querySelectorAll('.tab');
  for (var i = 0; i < tabs.length; i++) tabs[i].classList.toggle('active', tabs[i].dataset.panel === name);
  var panels = document.querySelectorAll('.panel');
  for (var i = 0; i < panels.length; i++) panels[i].classList.toggle('active', panels[i].id === 'panel-' + name);
  if (name === 'graph') {
    var btn = document.getElementById('btn-full-graph');
    // 有诊断报告 → 默认着色模式，显示"完整图谱"按钮
    if (state.report) {
      if (btn) btn.style.display = 'inline-block';
      state.graphMode = 'report';
      if (btn) btn.classList.add('active');
    } else {
      if (btn) btn.style.display = 'none';
      state.graphMode = 'full';
    }
    if (state.graphInstance) {
      setTimeout(function(){
        var c = document.getElementById('graph-container');
        if (c && c.offsetWidth > 0) state.graphInstance.width(c.offsetWidth).height(c.offsetHeight);
        state.graphInstance.refresh();
        if (state.report && state.graphMode === 'report') applyGraphColors();
      }, 200);
    } else initGraph();
  }
}

// ── 每日一题 + 打卡（悬浮弹窗，当日首次进入跳出）──
var dailyState = { question: null, checked_in: false, correct: false, dismissed: false, streak: 0, progress: 0, bonus: 0, gained: 0, points: 0 };

function loadDaily() {
  if (!state.user) return;
  fetch(API + '/api/daily-question', { method: 'POST', headers: authHeaders(), body: '{}' })
    .then(function(r){ return r.json(); })
    .then(function(d){
      if (d.error) return;
      dailyState.streak = d.streak || 0;
      dailyState.progress = d.progress || 0;
      dailyState.points = d.points || 0;
      dailyState.checked_in = d.checked_in;
      dailyState.dismissed = d.dismissed;
      dailyState.correct = d.correct;
      dailyState.question = d.question;
      // 当日未答题且未关闭 → 跳出弹窗
      if (!dailyState.checked_in && !dailyState.dismissed && dailyState.question) {
        renderDailyModal();
        showDailyModal();
      }
    })
    .catch(function(){});
}

function dailyHead() {
  return '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;padding-right:18px">'
    + '<span style="font-size:16px;font-weight:700">📆 每日一题</span>'
    + '<span style="font-size:13px;color:#FF9500">🔥 连续 <b>' + dailyState.streak + '</b> 天</span></div>';
}

function showDailyModal() {
  var m = document.getElementById('daily-modal');
  if (m) m.classList.add('show');
}

function hideDailyModal() {
  var m = document.getElementById('daily-modal');
  if (m) m.classList.remove('show');
}

function renderDailyModal() {
  var body = document.getElementById('daily-modal-body');
  if (!body) return;
  var q = dailyState.question;
  if (!q) return;
  var opts = (q.options || []).map(function(opt, i){
    return '<button onclick="answerDaily(' + i + ')" style="display:block;width:100%;text-align:left;margin:8px 0;padding:12px 14px;background:rgba(255,255,255,.04);border:1px solid var(--bd);border-radius:10px;color:var(--tx);cursor:pointer;font-size:14px;line-height:1.4">' + opt + '</button>';
  }).join('');
  body.innerHTML = dailyHead() + '<div style="font-size:15px;line-height:1.55;margin-bottom:4px">' + q.question + '</div>' + opts + '<div style="margin-top:10px;font-size:12px;color:var(--tx2);text-align:center">答对点亮光球 · 关闭则放弃今日奖励</div>';
}

function answerDaily(idx) {
  var q = dailyState.question;
  if (!q) return;
  var correctIdx = q.correct !== undefined ? q.correct : q.correct_index;
  var correct = (idx === correctIdx);
  fetch(API + '/api/daily-checkin', { method: 'POST', headers: authHeaders(),
    body: JSON.stringify({ qid: q.qid || q.id, correct: correct }) })
    .then(function(r){ return r.json(); })
    .then(function(d){
      dailyState.checked_in = true;
      dailyState.correct = correct;
      dailyState.streak = d.streak || 0;
      dailyState.progress = d.progress || 0;
      dailyState.bonus = d.bonus || 0;
      dailyState.gained = d.gained || 0;
      dailyState.points = d.points || 0;
      if (correct) {
        toast('🎉 答对了！光球已点亮，连续 ' + dailyState.streak + ' 天', 'success');
        showDailyResult(true);
      } else {
        showDailyResult(false, idx, correctIdx);
      }
    });
}

function dailyProgressBar(progress) {
  var cells = '';
  for (var i = 1; i <= 3; i++) {
    var on = i <= progress;
    cells += '<span style="display:inline-block;width:24px;height:24px;line-height:24px;border-radius:50%;margin:0 4px;font-size:12px;font-weight:700;' + (on ? 'background:linear-gradient(135deg,#FF9500,#FFB84D);color:#fff;box-shadow:0 2px 6px rgba(255,149,0,.35)' : 'background:rgba(255,255,255,.06);color:var(--tx2);border:1px solid var(--bd)') + '">' + (on ? '✓' : i) + '</span>';
  }
  return '<div style="margin-top:10px;text-align:center"><div style="font-size:12px;color:var(--tx2);margin-bottom:6px">🎁 额外奖励 <b style="color:#FF9500">' + progress + '/3</b></div><div>' + cells + '</div></div>';
}

function dailyBonusHint(progress, bonus) {
  if (bonus > 0) return '已领额外 +3 分，进度重置，明天重新累计';
  return '再连续答对 ' + (3 - progress) + ' 天，领额外 +3 分';
}

function showDailyResult(correct, chosen, correctIdx) {
  var body = document.getElementById('daily-modal-body');
  if (!body) return;
  var q = dailyState.question;
  if (correct) {
    var gainedText = dailyState.bonus > 0
      ? '<div style="font-size:15px;color:#FF9500;font-weight:700;margin-bottom:2px">+1 分 + 🎁 额外 +3 分</div>'
      : '<div style="font-size:15px;color:#FF9500;font-weight:700;margin-bottom:2px">+1 分</div>';
    body.innerHTML = dailyHead()
      + '<div style="padding:10px;text-align:center;color:var(--tx2)">'
      + '<div style="font-size:40px;margin-bottom:6px">🎉</div>'
      + '<div style="font-size:15px;color:var(--tx);margin-bottom:6px">答对了，光球已点亮！</div>'
      + gainedText
      + '<div style="font-size:12px;color:var(--tx2);margin-bottom:4px">累计 ' + dailyState.points + ' 分</div>'
      + '<div style="font-size:18px;color:#FF9500;font-weight:700">🔥 已连续 ' + dailyState.streak + ' 天</div>'
      + dailyProgressBar(dailyState.progress)
      + '<div style="margin-top:4px;font-size:12px;color:var(--tx2)">' + dailyBonusHint(dailyState.progress, dailyState.bonus) + '</div>'
      + '<div style="margin-top:2px;font-size:12px;color:var(--tx2)">明天再来，断了会清零</div>'
      + '</div>'
      + '<div style="margin-top:14px"><button onclick="hideDailyModal()" style="width:100%;padding:11px;background:var(--accent,#4f7cff);border:none;border-radius:10px;color:#fff;font-size:14px;cursor:pointer">收下奖励</button></div>';
  } else {
    var opts = (q.options || []).map(function(opt, i){
      var style = 'display:block;width:100%;text-align:left;margin:8px 0;padding:12px 14px;border-radius:10px;color:var(--tx);font-size:14px;line-height:1.4;border:1px solid ';
      if (i === correctIdx) style += '#2ECC40;background:rgba(46,204,64,.15)';
      else if (i === chosen) style += '#FF4136;background:rgba(255,65,54,.15)';
      else style += 'var(--bd);background:rgba(255,255,255,.04)';
      return '<div style="' + style + '">' + (i===correctIdx?'✅ ':'') + (i===chosen && i!==correctIdx?'❌ ':'') + opt + '</div>';
    }).join('');
    body.innerHTML = dailyHead()
      + '<div style="font-size:15px;line-height:1.55;margin-bottom:4px">' + q.question + '</div>'
      + opts
      + '<div style="margin-top:12px;text-align:center">'
      + '<div style="font-size:18px;color:#FF9500;font-weight:700">🔥 已连续 0 天</div>'
      + dailyProgressBar(0)
      + '<div style="margin-top:4px;font-size:12px;color:var(--tx2)">额外奖励进度已清零，明天重新累计</div>'
      + '<div style="margin-top:2px;font-size:12px;color:var(--tx2)">明天再来，断了会清零</div>'
      + '</div>'
      + '<div style="margin-top:14px"><button onclick="hideDailyModal()" style="width:100%;padding:11px;background:var(--accent,#4f7cff);border:none;border-radius:10px;color:#fff;font-size:14px;cursor:pointer">知道了</button></div>';
  }
}

function dismissDaily() {
  hideDailyModal();
  fetch(API + '/api/daily-dismiss', { method: 'POST', headers: authHeaders(), body: '{}' })
    .catch(function(){});
}

function enterFullGraph() {
  // 按住显示完整图谱（恢复节点原始颜色）
  if (state.graphMode === 'full') return;
  state.graphMode = 'full';
  var btn = document.getElementById('btn-full-graph');
  if (btn) { btn.textContent = '👁️ 松开恢复'; btn.classList.remove('active'); }
  if (state.blinkTimer) { clearInterval(state.blinkTimer); state.blinkTimer = null; }
  if (state.graphInstance) {
    var layerColors = { 'foundation':'#5B9BD5', 'advanced':'#2ECC40', 'transcendent':'#9B59B6' };
    var resetNodes = state.nodes.map(function(n){
      var node = Object.assign({}, n);
      delete node.mastery; delete node._blinkGroup;
      // state.nodes 保留原始颜色，直接恢复；若无颜色则按层级着色
      if (!node.color) node.color = layerColors[n.layer] || '#5B9BD5';
      node.val = (n.weight || 3) * 1.5;
      return node;
    });
    state.graphInstance.graphData({ nodes: resetNodes, links: state.edges });
    state.graphInstance.nodeColor(function(n){ return n.color; });
    state.graphInstance.nodeVal(function(n){ return n.val; });
  }
}

function exitFullGraph() {
  // 松开恢复诊断着色图谱
  if (state.graphMode === 'report') return;
  state.graphMode = 'report';
  var btn = document.getElementById('btn-full-graph');
  if (btn) { btn.textContent = '👁️ 按住看完整图谱'; btn.classList.add('active'); }
  if (state.report && state.graphInstance) applyGraphColors();
}

function showPhase(name) {
  var phases = document.querySelectorAll('.phase');
  for (var i = 0; i < phases.length; i++) phases[i].classList.remove('active');
  var el = document.getElementById('phase-' + name);
  if (el) el.classList.add('active');
  document.getElementById('panel-test').scrollTop = 0;
}

// ── 内部知识单选 ──
function selectInternal(category) {
  // 如果当前已选中同一类别 → 取消选择
  if (state.internalCategory === category) {
    state.internalCategory = null;
  } else {
    state.internalCategory = category;
  }
  // 题库隔离：选择内部知识 → 自动清空市场竞争层级（两个题库互斥，各自内部选题逻辑独立）
  if (state.internalCategory) {
    state.selectedLevels = [];
    ['foundation','advanced','transcendent'].forEach(function(l){
      var cb = document.getElementById('cb-' + l);
      if (cb) cb.classList.remove('checked');
    });
    updateLevelInfo();
  }
  // 产品知识 → 显示产品多选区（默认不选，用户自行单选/复选产品）
  var ps = document.getElementById('product-selector');
  if (ps) ps.style.display = (state.internalCategory === '产品知识') ? 'block' : 'none';
  updateProductUI();  // 同步产品复选 checked 状态 + 刷新题数（内部会调 updateInternalUI/updateStartBtn）
}

// ── 产品知识：产品复选 ──
function toggleProduct(name) {
  var i = state.selectedProducts.indexOf(name);
  if (i >= 0) state.selectedProducts.splice(i, 1);
  else state.selectedProducts.push(name);
  updateProductUI();
}

function selectAllProducts(silent) {
  state.selectedProducts = PRODUCTS.map(function(p){ return p.name; });
  if (!silent) updateProductUI();
}

function clearProducts() {
  state.selectedProducts = [];
  updateProductUI();
}

function updateProductUI() {
  PRODUCTS.forEach(function(p){
    var cb = document.getElementById('cb-prod-' + p.name.replace(/[^a-zA-Z0-9\u4e00-\u9fa5]/g, '_'));
    if (cb) cb.classList.toggle('checked', state.selectedProducts.indexOf(p.name) >= 0);
  });
  updateInternalUI();
}

function updateInternalUI() {
  var cats = ['基础知识', '产品知识', '合规知识'];
  var ids = ['basic', 'product', 'compliance'];
  cats.forEach(function(cat, i) {
    var el = document.getElementById('cb-internal-' + ids[i]);
    var radio = document.getElementById('radio-' + ids[i]);
    if (el) {
      el.classList.toggle('checked', state.internalCategory === cat);
    }
    if (radio) {
      radio.textContent = state.internalCategory === cat ? '●' : '○';
    }
  });
  var info = document.getElementById('internal-info');
  if (info) {
    if (state.internalCategory === '产品知识') {
      var n = state.selectedProducts.length;
      var label = document.getElementById('product-selector-label');
      if (label) label.textContent = n === 0 ? '请选择 1 个或多个产品（每产品 12 题）' : ('已选 ' + n + ' 个产品 · 共 ' + (n * 12) + ' 题');
      info.textContent = n === 0 ? '已选：产品知识 · 请先选择产品' : ('已选：产品知识 · 共 ' + (n * 12) + ' 题');
      info.style.color = n === 0 ? 'var(--tx2)' : 'var(--tx)';
    } else if (state.internalCategory) {
      // 获取该类别题目数
      var cnt = state.internalCounts[state.internalCategory] || 0;
      info.textContent = '已选：' + state.internalCategory + ' · 共 ' + cnt + ' 题';
      info.style.color = 'var(--tx)';
    } else {
      info.textContent = '未选择内部知识方向';
      info.style.color = 'var(--tx2)';
    }
  }
  updateStartBtn();
}

function updateStartBtn() {
  // 必须匹配 profile 页的开始按钮（登录页 btn-auth 也是 .btn-block，需排除）
  var btn = document.querySelector('#phase-profile .btn-primary');
  if (!btn) return;
  var marketTotal = 0;
  var counts = { foundation: 51, advanced: 38, transcendent: 30 };
  state.selectedLevels.forEach(function(l){ marketTotal += counts[l]; });
  var internalTotal = 0;
  if (state.internalCategory === '产品知识') {
    internalTotal = state.selectedProducts.length * 12;  // 每个产品完整 12 题
  } else if (state.internalCategory) {
    internalTotal = state.internalCounts[state.internalCategory] || 0;
  }
  var parts = [];
  if (marketTotal > 0) parts.push('市场' + marketTotal + '题');
  if (state.internalCategory) parts.push('内部' + internalTotal + '题');
  var total = marketTotal + internalTotal;
  if (total === 0) {
    if (state.internalCategory === '产品知识') btn.textContent = '🚀 请先选择产品';
    else btn.textContent = '🚀 请先选择诊断范围';
    btn.disabled = true;
  } else {
    btn.textContent = '🚀 开始诊断（共 ' + total + ' 题：' + parts.join(' + ') + '）';
    btn.disabled = false;
  }
}

function toggleLevel(level) {
  var idx = state.selectedLevels.indexOf(level);
  // 允许取消到 0 个层级：内部知识可单独作答（无需市场竞争层级）
  if (idx >= 0) state.selectedLevels.splice(idx, 1);
  else state.selectedLevels.push(level);
  // 题库隔离：选择市场竞争 → 自动取消内部知识（两个题库互斥，选题逻辑互不干扰）
  if (state.selectedLevels.length > 0 && state.internalCategory) {
    state.internalCategory = null;
    var ps = document.getElementById('product-selector');
    if (ps) ps.style.display = 'none';
    updateInternalUI();
  }
  updateLevelInfo();
  ['foundation','advanced','transcendent'].forEach(function(l){
    var cb = document.getElementById('cb-' + l);
    if (cb) cb.classList.toggle('checked', state.selectedLevels.indexOf(l) >= 0);
  });
  updateStartBtn();
}

function setFoundationSize(size) {
  // 配额已固定 51/38/30，此函数保留为空兼容旧调用
}

function updateLevelInfo() {
  var names = { foundation:'基础', advanced:'提升', transcendent:'升华' };
  var counts = { foundation: 51, advanced: 38, transcendent: 30 };
  var total = 0;
  state.selectedLevels.forEach(function(l){ total += counts[l]; });
  document.getElementById('level-info').textContent = '已选：' + state.selectedLevels.map(function(l){ return names[l]; }).join('+') + ' · 共 ' + total + ' 题';
  updateInternalUI();
  updateStartBtn();
}

// ── Graph ──
// 上下游映射：nodeId -> [{id, edgeLabel}]（边 source→target：target 的上游是 source，source 的下游是 target）
var nodeLabelMap = {};
var nodeUpstream = {};
var nodeDownstream = {};
function buildAdjacency() {
  nodeLabelMap = {}; nodeUpstream = {}; nodeDownstream = {};
  (state.nodes || []).forEach(function(n){ nodeLabelMap[n.id] = n.label; });
  (state.edges || []).forEach(function(e){
    var s = e.source, t = e.target;
    if (!s || !t) return;
    if (!nodeUpstream[t]) nodeUpstream[t] = [];
    nodeUpstream[t].push({ id: s, edgeLabel: e.label });
    if (!nodeDownstream[s]) nodeDownstream[s] = [];
    nodeDownstream[s].push({ id: t, edgeLabel: e.label });
  });
}
function showNodeDetail(n) {
  var old = document.querySelector('.node-detail-overlay');
  if (old) old.remove();
  var ups = nodeUpstream[n.id] || [];
  var downs = nodeDownstream[n.id] || [];
  function linkList(arr, arrow) {
    if (!arr || !arr.length) return '<div style="color:var(--tx2);font-size:12px;padding:4px 0">（暂无）</div>';
    return arr.map(function(x){
      return '<div style="display:flex;align-items:center;gap:10px;padding:9px 10px;margin-bottom:6px;background:var(--card);border:1px solid var(--bd2);border-radius:10px;cursor:pointer" onclick="openNodeById(\'' + x.id + '\')">' +
        '<span style="font-size:15px;flex-shrink:0">' + arrow + '</span>' +
        '<div style="flex:1;min-width:0"><div style="font-size:13px;font-weight:600;color:var(--tx)">' + (nodeLabelMap[x.id] || x.id) + '</div>' +
        (x.edgeLabel ? '<div style="font-size:11px;color:var(--tx2);margin-top:1px">' + x.edgeLabel + '</div>' : '') +
        '</div></div>';
    }).join('');
  }
  var layerText = n.layer ? ({foundation:'基础', advanced:'提升', transcendent:'升华'}[n.layer] || n.layer) : '';
  var overlay = document.createElement('div');
  overlay.className = 'node-detail-overlay';
  overlay.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.45);z-index:300;display:flex;align-items:flex-end;justify-content:center';
  overlay.onclick = function(e){ if (e.target === overlay) overlay.remove(); };
  overlay.innerHTML =
    '<div style="background:var(--bg);width:100%;max-width:480px;max-height:82vh;border-radius:16px 16px 0 0;overflow-y:auto;-webkit-overflow-scrolling:touch;padding:20px 18px calc(20px + var(--safe-b))" onclick="event.stopPropagation()">' +
      '<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:12px">' +
        '<div style="min-width:0"><div style="font-size:18px;font-weight:700;line-height:1.3;color:var(--tx)">' + n.label + '</div>' +
        (layerText ? '<div style="font-size:11px;color:var(--acc);margin-top:4px">' + layerText + (n.category ? ' · ' + n.category : '') + '</div>' : '') +
        '</div>' +
        '<button onclick="this.closest(\'.node-detail-overlay\').remove()" style="background:none;border:none;font-size:22px;color:var(--tx2);cursor:pointer;padding:4px;flex-shrink:0">✕</button>' +
      '</div>' +
      '<div style="font-size:13px;line-height:1.75;color:var(--tx);margin-bottom:18px">' + (n.content || '暂无简介') + '</div>' +
      '<div style="font-size:13px;font-weight:700;color:var(--tx);margin-bottom:8px">⬆️ 上游知识 <span style="font-weight:400;color:var(--tx2);font-size:11px">（它依赖什么）</span></div>' +
      linkList(ups, '⬆️') +
      '<div style="font-size:13px;font-weight:700;color:var(--tx);margin:14px 0 8px">⬇️ 下游知识 <span style="font-weight:400;color:var(--tx2);font-size:11px">（它支撑什么）</span></div>' +
      linkList(downs, '⬇️') +
    '</div>';
  document.body.appendChild(overlay);
}
function openNodeById(id) {
  var node = null;
  (state.nodes || []).forEach(function(n){ if (n.id === id) node = n; });
  if (!node) return;
  var overlay = document.querySelector('.node-detail-overlay');
  if (overlay) overlay.remove();
  showNodeDetail(node);
}
/* 2D 图谱降级（移动端 / 3D 库不可用）：分层 SVG 布局，节点可点、保上下游详情 */
function renderGraph2D() {
  var container = document.getElementById('graph-container');
  if (!container) return;
  var nodes = state.nodes, edges = state.edges;
  if (!nodes || !nodes.length) {
    container.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--tx2);font-size:14px">暂无图谱数据</div>';
    return;
  }
  var layerOrder = ['transcendent', 'advanced', 'foundation', 'unknown'];
  var layers = { foundation: [], advanced: [], transcendent: [], unknown: [] };
  nodes.forEach(function(n){ (layers[n.layer] || layers.unknown).push(n); });

  var W = container.clientWidth || 360;
  var H = container.clientHeight || 500;
  var pad = 34;
  var layerH = (H - 2 * pad) / layerOrder.length;

  var pos = {};
  layerOrder.forEach(function(ly, li){
    var arr = layers[ly];
    if (!arr.length) return;
    var y = pad + li * layerH + layerH / 2;
    var spacing = (W - 2 * pad) / (arr.length + 1);
    arr.forEach(function(n, i){ pos[n.id] = { x: pad + spacing * (i + 1), y: y }; });
  });

  var edgeHTML = (edges || []).map(function(e){
    var s = pos[e.source], t = pos[e.target];
    if (!s || !t) return '';
    var w = (e.strength ? e.strength : 0.5);
    return '<line x1="'+s.x+'" y1="'+s.y+'" x2="'+t.x+'" y2="'+t.y+'" stroke="rgba(91,155,213,'+(0.12+w*0.15)+')" stroke-width="'+(0.5+w)+'"/>';
  }).join('');

  var nodeHTML = nodes.map(function(n){
    var p = pos[n.id];
    if (!p) return '';
    var r = 7 + (n.weight || 3) * 0.9;
    var color = n.color || '#888';
    var label = (n.label || n.id);
    return '<g class="g2d-node" data-id="'+n.id+'" style="cursor:pointer">' +
      '<circle cx="'+p.x+'" cy="'+p.y+'" r="'+r+'" fill="'+color+'" stroke="rgba(255,255,255,0.45)" stroke-width="1.2"/>' +
      '<text x="'+p.x+'" y="'+(p.y + r + 11)+'" text-anchor="middle" font-size="9" fill="#c8c8d0" style="pointer-events:none">'+label+'</text>' +
      '</g>';
  }).join('');

  container.innerHTML = '<svg width="100%" height="100%" viewBox="0 0 '+W+' '+H+'" preserveAspectRatio="xMidYMid meet" style="background:#0a0a14;display:block">' +
    edgeHTML + nodeHTML + '</svg>';

  container.querySelectorAll('.g2d-node').forEach(function(g){
    g.addEventListener('click', function(){
      var id = g.getAttribute('data-id');
      var node = null;
      nodes.forEach(function(n){ if (n.id === id) node = n; });
      if (node) showNodeDetail(node);
    });
  });
}

function initGraph() {
  var container = document.getElementById('graph-container');
  if (!container) return;
  container.innerHTML = '<div class="submitting"><div class="spinner"></div><div>加载3D图谱...</div></div>';

  function loadData() {
    return fetch(API + '/api/nodes').then(function(r){ return r.json(); }).then(function(n){ state.nodes = n; })
    .then(function(){ return fetch(API + '/api/edges').then(function(r){ return r.json(); }); })
    .then(function(e){ state.edges = e; })
    .catch(function(){
      return fetch('../data/roles/insurance-agent/nodes.json').then(function(r){ return r.json(); })
      .then(function(n){ state.nodes = n; })
      .then(function(){ return fetch('../data/roles/insurance-agent/edges.json').then(function(r){ return r.json(); }); })
      .then(function(e){ state.edges = e; });
    });
  }

  loadData().then(function(){
    buildAdjacency();
    // 移动端或 3D 库不可用 → 2D 分层降级（保光球可点 + 上下游详情）
    if (window.innerWidth <= 640 || typeof ForceGraph3D === 'undefined') {
      renderGraph2D();
      return;
    }
    var timedOut = false;
    var timeoutId = setTimeout(function(){ timedOut = true; renderGraph2D(); }, 8000);
    container.innerHTML = '';
    var Graph = ForceGraph3D()(container)
      .graphData({ nodes: state.nodes, links: state.edges })
      .nodeLabel(function(n){ return (n.label||n.id) + (n.mastery !== undefined ? ' [' + Math.round(n.mastery*100) + '%]' : ''); })
      .linkColor(function(){ return '#5B9BD5'; })
      .linkWidth(function(l){ return l.strength ? l.strength * 1.5 : 1; })
      .nodeColor(function(n){ return n.color || '#888'; })
      .nodeVal(function(n){ return (n.weight || 3) * 1.5; })
      .backgroundColor('#0a0a14')
      .linkDirectionalParticles(function(l){ return (l.strength || 0) > 0.8 ? 2 : 0; })
      .linkDirectionalParticleWidth(1.5)
      .linkDirectionalParticleSpeed(0.004)
      .onNodeClick(function(n){ showNodeDetail(n); });
    state.graphInstance = Graph;
    clearTimeout(timeoutId);
    setTimeout(function(){ Graph.cameraPosition({ x:100, y:50, z:150 }, { x:0, y:0, z:0 }, 2000); }, 300);
    if (state.report && state.graphMode === 'report') applyGraphColors();
  }).catch(function(e){
    container.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--tx2);font-size:14px;flex-direction:column;gap:12px"><div style="font-size:48px">🧠</div><div>3D 图谱暂不可用</div><div style="font-size:11px">测试功能不受影响</div></div>';
  });
}

// ── 图谱缩放滑条（手机无滚轮，用于缩小看全景/放大看细节）──
function setGraphZoom(v) {
  var g = state.graphInstance;
  if (!g) return;
  v = parseFloat(v);
  if (isNaN(v)) v = 60;
  var cam = g.camera();
  var dir = cam.position.clone().normalize();
  var dist = 30 + (100 - v) * 4;   // v=100 → 30（最近）; v=1 → 426（最远）
  g.cameraPosition({ x: dir.x * dist, y: dir.y * dist, z: dir.z * dist }, { x: 0, y: 0, z: 0 }, 200);
}

// ── Test Flow ──
function startTest() {
  state.profile = {
    name: state.user ? state.user.name : (document.getElementById('f-name').value || '匿名用户'),
    target: document.getElementById('f-target').value,
    customer: document.getElementById('f-customer').value,
    levels: state.selectedLevels
  };
  showPhase('questions');
  document.getElementById('q-card-area').innerHTML = '<div class="submitting"><div class="spinner"></div><div>正在生成试题…</div></div>';

  function loadTests() {
    return fetch(API + '/api/generate-test', { method:'POST', headers:authHeaders(),
      body: JSON.stringify({ role:'insurance-agent', variant_ratio:1/3, seed:Date.now(), levels:state.selectedLevels, internal_category: state.internalCategory, products: state.selectedProducts })
    }).then(function(r){ return r.json(); }).then(function(d){
      if (d.error) { toast(d.error, 'error'); throw new Error(d.error); }
      return d.questions;
    })
    .catch(function(){
      return fetch(API + '/api/tests').then(function(r){ if(!r.ok) throw new Error('no'); return r.json(); })
      .catch(function(){ return fetch('../data/roles/insurance-agent/tests.json').then(function(r){ return r.json(); }); });
    });
  }

  loadTests().then(function(questions){
    state.questions = questions; state.answers = {}; state.currentQ = 0;
    renderQuestion();
  }).catch(function(){ state.questions = []; toast('网络异常，无法加载试题，请检查连接后重试', 'error'); showPhase('profile'); });
}

function renderQuestion() {
  var q = state.questions[state.currentQ];
  if (!q) return;
  var total = state.questions.length, idx = state.currentQ + 1, pct = Math.round(idx / total * 100);
  document.getElementById('q-bar').style.width = pct + '%';
  document.getElementById('q-counter').textContent = idx + ' / ' + total;
  document.getElementById('q-hint').textContent = q.is_variant ? '✨ AI 生成变体题' : '';
  var chosen = state.answers[q.id];
  var optsHTML = q.options.map(function(opt,i){ return '<div class="q-opt'+(chosen===i?' chosen':'')+'" onclick="selectOption('+i+')">'+String.fromCharCode(65+i)+'. '+opt+'</div>'; }).join('');
  var tl = { knowledge:'概念理解', scenario:'场景判断', application:'实际应用', internal:'内部知识', judge:'判断', choice:'单选' };
  var stars = ''; for (var s=0; s<(q.difficulty||2); s++) stars += '★';
  document.getElementById('q-card-area').innerHTML =
    '<div class="q-card"><div class="q-meta">' +
      '<span class="q-tag '+q.type+'">'+(tl[q.type]||q.type)+'</span>' +
      (q.is_variant?'<span class="q-tag variant">AI变体</span>':'') +
      '<span style="font-size:11px;color:var(--tx2);margin-left:auto">难度: '+stars+'</span>' +
    '</div><div class="q-text">'+q.question+'</div><div class="q-options">'+optsHTML+'</div></div>';
  document.getElementById('btn-prev').style.display = idx===1?'none':'';
  var btnN = document.getElementById('btn-next');
  if (idx===total) { btnN.textContent='✅ 提交诊断'; btnN.onclick=submitTest; }
  else { btnN.textContent='下一题 →'; btnN.onclick=nextQuestion; }
  btnN.disabled = chosen === undefined;
}

function selectOption(i) {
  var q = state.questions[state.currentQ];
  state.answers[q.id] = i;
  document.getElementById('btn-next').disabled = false;
  var opts = document.querySelectorAll('.q-opt');
  for (var j=0; j<opts.length; j++) opts[j].classList.toggle('chosen', j===i);
}
function nextQuestion() { if (state.currentQ < state.questions.length-1) { state.currentQ++; renderQuestion(); } }
function prevQuestion() { if (state.currentQ > 0) { state.currentQ--; renderQuestion(); } }

function submitTest() {
  showPhase('done');
  var token = state.user ? state.user.token : '';
  fetch(API + '/api/submit', { method:'POST', headers: authHeaders(),
    body: JSON.stringify({ answers:state.answers, questions:state.questions, profile:state.profile, token: token })
  }).then(function(r){ return r.json(); }).then(function(report){
    state.report = report;
    renderReport();
    checkRankUp(report.rank);
    applyGraphColors();
    saveResult();
    loadFlywheelFeedback();
  }).catch(function(){
    // Local fallback
    state.report = localScore();
    renderReport();
    applyGraphColors();
    saveResult();
  });
}

/* 飞轮反馈：告诉用户"你的答题正在帮助系统进化" */
function loadFlywheelFeedback() {
  if (!state.user) return;
  fetch(API + '/api/flywheel/public-stats', { headers: authHeaders() })
    .then(function(r){ return r.ok ? r.json() : null; })
    .then(function(s){
      if (!s || !s.total_sessions) return;
      var sufMap = { insufficient:'积累中', minimal:'起步', moderate:'中等', rich:'丰富' };
      var suf = sufMap[s.data_sufficiency] || s.data_sufficiency;
      var html = '<div class="fw-card">'
        + '<div class="fw-title">🌀 你的答题已帮助系统进化</div>'
        + '<div class="fw-line">全网 <b>' + s.total_users + '</b> 位用户 · 累计 <b>' + s.total_sessions + '</b> 次诊断 · 数据充足度：<b>' + suf + '</b></div>';
      if (s.auto_actions_applied > 0) {
        html += '<div class="fw-line">系统已基于群体数据自动优化 <b>' + s.auto_actions_applied + '</b> 次</div>';
      }
      if (s.recent_trend) {
        html += '<div class="fw-trend">' + s.recent_trend + '</div>';
      }
      html += '</div>';
      var sec = document.getElementById('report-section');
      if (sec && !sec.querySelector('.fw-card')) {
        sec.insertAdjacentHTML('afterbegin', html);
      }
    })
    .catch(function(){ /* 静默降级，不影响报告 */ });
}

function localScore() {
  var st={}, tc=0, ta=0;
  state.questions.forEach(function(q){
    var correct = state.answers[q.id]===q.correct_index;
    if (correct) tc++; ta++;
    var s = st[q.node_id] = st[q.node_id] || { correct:0, total:0, label:q.node_id, layer:q.layer||'unknown' };
    s.total++; if (correct) s.correct++;
  });
  Object.values(st).forEach(function(s){ s.confidence=s.total>0?s.correct/s.total:0; s.mastered=s.confidence>=0.67; });
  var ls = { foundation:{correct:0,total:0}, advanced:{correct:0,total:0}, transcendent:{correct:0,total:0} };
  Object.values(st).forEach(function(s){ if (ls[s.layer]) { ls[s.layer].correct+=s.correct; ls[s.layer].total+=s.total; } });
  return { total_score:ta>0?Math.round(tc/ta*100):0, total_correct:tc, total_questions:ta, node_status:st, layer_stats:ls, profile:state.profile, rank: computeRankLocal(ls) };
}

/* 前端段位计算（历史记录兜底 / 本地打分）—— 与后端 compute_rank 同构 */
function computeRankLocal(layerStats) {
  function rate(ly) {
    var s = (layerStats || {})[ly] || {};
    return s.total > 0 ? s.correct / s.total : 0;
  }
  var f = rate('foundation'), a = rate('advanced'), t = rate('transcendent');
  var tiers = [
    ['king','王者','🌟','15年+', function(){ return t >= 0.80; }],
    ['diamond','钻石','👑','10年+', function(){ return a >= 0.85 && t >= 0.60; }],
    ['platinum','铂金','💎','5-10年', function(){ return f >= 0.85 && a >= 0.70; }],
    ['gold','黄金','🥇','3-5年', function(){ return f >= 0.75 && a >= 0.50; }],
    ['silver','白银','🥈','1-3年', function(){ return f >= 0.60; }],
    ['bronze','青铜','🥉','0-1年', function(){ return true; }]
  ];
  for (var i = 0; i < tiers.length; i++) {
    if (tiers[i][4]()) return { rank: tiers[i][0], title: tiers[i][1], icon: tiers[i][2], years: tiers[i][3] };
  }
  return { rank: 'bronze', title: '青铜', icon: '🥉', years: '0-1年' };
}

/* 段位晋级检测：对比上次段位，升段触发晋级仪式 */
function checkRankUp(rank) {
  if (!rank || !rank.rank) return;
  var order = ['bronze','silver','gold','platinum','diamond','king'];
  var prevKey = null;
  try { prevKey = localStorage.getItem('kgds_last_rank'); } catch(e){}
  try { localStorage.setItem('kgds_last_rank', rank.rank); } catch(e){}
  if (!prevKey || order.indexOf(prevKey) < 0) return; // 首次诊断或异常，无晋级判断
  var pi = order.indexOf(prevKey), ci = order.indexOf(rank.rank);
  if (ci > pi) showRankUpModal(rank);
}

function showRankUpModal(rank) {
  var overlay = document.createElement('div');
  overlay.id = 'rankup-overlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.75);z-index:9999;display:flex;align-items:center;justify-content:center';
  overlay.innerHTML =
    '<div style="background:linear-gradient(160deg,#FFF8E1,#FFF3C4);border-radius:20px;padding:32px 28px;text-align:center;max-width:330px;width:90%;box-shadow:0 20px 60px rgba(0,0,0,0.4)">' +
    '<div style="font-size:56px;line-height:1">'+rank.icon+'</div>' +
    '<div style="font-size:22px;font-weight:800;color:#3a2d00;margin-top:10px">🎉 恭喜晋级！</div>' +
    '<div style="font-size:15px;color:#5a4a10;margin-top:12px;line-height:1.7">你已晋升为<br><strong style="font-size:19px">'+rank.icon+' '+rank.title+'</strong><br>相当于从业 <strong>'+rank.years+'</strong> 的老手</div>' +
    '<div style="font-size:12px;color:#8a7a30;margin-top:10px">知识图谱正在变绿，继续点亮光球 👊</div>' +
    '<button onclick="document.getElementById(\'rankup-overlay\').remove()" style="margin-top:18px;padding:11px 30px;border:none;border-radius:10px;background:linear-gradient(135deg,#ffb300,#ff8f00);color:#fff;font-size:15px;font-weight:700;cursor:pointer">收下段位 💪</button>' +
    '</div>';
  document.body.appendChild(overlay);
}

function renderReport() {
  var r = state.report, score = r.total_score||0;
  var sl, sbc;
  if (score>=85) { sl='优秀'; sbc='excellent'; }
  else if (score>=70) { sl='良好'; sbc='good'; }
  else if (score>=55) { sl='待提升'; sbc='warning'; }
  else { sl='需系统学习'; sbc='danger'; }

  var ls = r.layer_stats||{}, lp={};
  ['foundation','advanced','transcendent'].forEach(function(l){ var s=ls[l]||{correct:0,total:0}; lp[l]=s.total>0?Math.round(s.correct/s.total*100):0; });

  var ns = r.node_status||{}, strengths=[], gaps=[];
  Object.keys(ns).forEach(function(nid){
    var s=ns[nid];
    if (s.mastered) strengths.push({ label:s.label||nid, pct:Math.round((s.confidence||0)*100) });
    else gaps.push({ label:s.label||nid, pct:Math.round((s.confidence||0)*100), layer:s.layer, content:s.content||'' });
  });
  strengths.sort(function(a,b){ return b.pct-a.pct; });
  gaps.sort(function(a,b){ return a.pct-b.pct; });

  var books = getBooks();
  var praise = getPraise(score, strengths, r.profile);
  var scoreColor = score>=70?'var(--gr)':(score>=55?'var(--ye)':'var(--rd)');

  // 段位（后端 compute_rank 返回，取代自报年限）
  var rk = r.rank || {icon:'🥉', title:'青铜', years:'0-1年', rank:'bronze'};

  var html = '';
  html += '<div class="rp-header"><div class="rp-score-ring"><span class="score-text" style="color:'+scoreColor+'">'+score+'</span><span class="score-label">综合评分</span></div><div class="rp-summary"><h2>'+praise.title+'</h2><div class="sub">'+praise.subtitle+'</div><div class="rp-badges"><span class="rp-badge '+sbc+'">综合 '+sl+'</span><span class="rp-badge '+(lp.foundation>=75?'excellent':'warning')+'">基础 '+(lp.foundation||0)+'%</span><span class="rp-badge '+(lp.advanced>=65?'good':'warning')+'">提升 '+(lp.advanced||0)+'%</span><span class="rp-badge '+(lp.transcendent>=50?'good':'danger')+'">升华 '+(lp.transcendent||0)+'%</span></div></div></div>';
  // 段位卡片
  html += '<div style="display:flex;align-items:center;gap:14px;background:linear-gradient(135deg,#FFF8E1,#FFFDE7);border:1px solid #F0E6B8;border-radius:14px;padding:14px 16px;margin:12px 0">' +
    '<div style="font-size:40px;line-height:1">'+rk.icon+'</div>' +
    '<div style="flex:1"><div style="font-size:17px;font-weight:700;color:var(--tx)">'+rk.title+' <span style="font-weight:400;font-size:12px;color:var(--tx2)">相当于从业 '+rk.years+'</span></div>' +
    '<div style="font-size:11px;color:var(--tx2);margin-top:3px">系统按分层掌握率自动评定 · 非自报年限</div></div></div>';

  var layerNames = ['🏗️ 基础技能','📈 提升技能','🚀 升华技能'];
  var layerKeys = ['foundation','advanced','transcendent'];
  html += '<div class="rp-layers">';
  for (var i=0; i<3; i++) {
    var lk=layerKeys[i], pct=lp[lk]||0;
    var c = pct>=75?'var(--gr)':(pct>=50?'var(--ye)':'var(--rd)');
    html += '<div class="rp-layer"><div class="layer-name">'+layerNames[i]+'</div><div class="layer-pct" style="color:'+c+'">'+pct+'%</div><div class="layer-bar"><div class="layer-bar-fill" style="width:'+pct+'%;background:'+c+'"></div></div></div>';
  }
  html += '</div>';

  html += '<div style="text-align:center;margin:12px 0;font-size:11px;color:var(--tx2)">切换到「知识图谱」查看彩色对比：<span style="color:#2ECC40">🟢已掌握</span> · <span style="color:#FFDC00">🟡部分掌握</span> · <span style="color:#FF4136">🔴需加强（闪烁）</span> · <span style="color:#555">⬜未选测</span></div>';

  html += '<div class="rp-cards"><div class="rp-card"><h4>🌟 优势领域</h4>';
  if (strengths.length>0) strengths.slice(0,5).forEach(function(s){ html+='<div class="rp-item"><div class="name rp-strength">'+s.label+'</div><div class="detail">掌握度 '+s.pct+'%</div></div>'; });
  else html+='<div class="rp-item"><div class="name" style="color:var(--tx2)">继续加油，优势正在形成</div></div>';
  html+='</div>';

  // 知识线诊断（按层汇总薄弱点，不逐条列举）
  var gapByLayer = { foundation:[], advanced:[], transcendent:[] };
  gaps.forEach(function(g){ if (gapByLayer[g.layer]) gapByLayer[g.layer].push(g); });
  var layerIcons = { foundation:'🏗️', advanced:'📈', transcendent:'🚀' };
  var layerDesc = {
    foundation: '产品知识（寿险/健康险/意外险/年金险）+ 合规要求（保险法、销售规范、反洗钱），执业基础。',
    advanced: '销售方法论（需求分析、促成技巧、异议处理、转介绍）+ 客户经营能力，决定转化率。',
    transcendent: '高客经营、团队管理和行业洞察，决定职业天花板。'
  };
  var layerActions = {
    foundation: '通过公司培训 + 产品手册 + 每日刷题来巩固。',
    advanced: '通过实战复盘 + 同业交流 + 专项工作坊来突破。',
    transcendent: '通过行业论坛 + 高端培训 + 导师指导来拓展。'
  };

  html += '<div class="rp-card"><h4>📋 知识线诊断</h4>';
  for (var i=0; i<3; i++) {
    var lk = layerKeys[i], gs = gapByLayer[lk] || [];
    if (gs.length === 0) {
      var pct = lp[lk] || 0;
      if (pct >= 75) {
        html += '<div class="rp-item"><div class="name rp-strength">'+layerIcons[lk]+' '+layerNames[i]+'：已掌握</div><div class="detail">'+pct+'% · 继续保持</div></div>';
      } else if (pct > 0) {
        html += '<div class="rp-item"><div class="name" style="color:var(--ye)">'+layerIcons[lk]+' '+layerNames[i]+'：需关注</div><div class="detail">'+(layerDesc[lk]||'')+'</div></div>';
      } else {
        html += '<div class="rp-item"><div class="name" style="color:var(--tx2)">'+layerIcons[lk]+' '+layerNames[i]+'：未选测</div></div>';
      }
    } else {
      var topLabels = gs.slice(0,3).map(function(g){ return g.label; }).join('、');
      var more = gs.length > 3 ? '…等 '+gs.length+' 项' : (gs.length+' 项');
      html += '<div class="rp-item" style="border-bottom:1px solid var(--bd);padding-bottom:10px;margin-bottom:8px">';
      html += '<div class="name rp-gap">'+layerIcons[lk]+' '+layerNames[i]+'：薄弱 '+more+'</div>';
      html += '<div class="detail" style="margin-top:2px">'+ (layerDesc[lk]||'') +'</div>';
      html += '<div class="detail" style="margin-top:4px;color:var(--rd);font-weight:500">⚠ 较弱：'+topLabels+'</div>';
      html += '<div class="detail" style="margin-top:2px;color:var(--tx2)">👉 '+ (layerActions[lk]||'') +'</div>';
      html += '</div>';
    }
  }
  html += '</div>';

  // ── 错题解析（刻意练习闭环：错题追着你直到掌握）──
  var wrongItems = (r.item_results || []).filter(function(it){ return !it.is_correct; });
  if (wrongItems.length > 0) {
    html += '<div class="rp-card"><h4>📝 错题解析 <span style="font-size:11px;color:var(--tx2);font-weight:400">共 '+wrongItems.length+' 题</span></h4>';
    wrongItems.forEach(function(it, idx){
      var optTxt = (it.options || []).map(function(o, oi){
        var mark = '';
        if (oi === it.correct_answer) mark = ' <span style="color:var(--gr)">✅</span>';
        if (oi === it.your_answer && oi !== it.correct_answer) mark = ' <span style="color:var(--rd)">❌</span>';
        return '<div style="font-size:12px;color:var(--tx2);padding:2px 0">'+String.fromCharCode(65+oi)+'. '+o+mark+'</div>';
      }).join('');
      var yourTxt = (it.your_answer === undefined || it.your_answer === null) ? '未作答' : String.fromCharCode(65+it.your_answer);
      var correctTxt = (it.correct_answer === undefined || it.correct_answer === null) ? '—' : String.fromCharCode(65+it.correct_answer);
      html += '<div style="border:1px solid var(--bd);border-radius:10px;padding:12px;margin-bottom:10px">';
      html += '<div style="font-size:13px;font-weight:600;color:var(--tx);margin-bottom:6px">'+(idx+1)+'. '+it.question+'</div>';
      html += optTxt;
      html += '<div style="font-size:12px;margin-top:6px;padding:8px;border-radius:8px;background:var(--bg2)">';
      html += '<div style="color:var(--rd)">你的答案：'+yourTxt+'</div>';
      html += '<div style="color:var(--gr)">正确答案：'+correctTxt+'</div>';
      html += '<div style="margin-top:6px;color:var(--tx);line-height:1.5">💡 '+((it.explanation||'').trim() || '（暂无解析，建议复习该知识点）')+'</div>';
      html += '</div></div>';
    });
    html += '</div>';
  }

  // ── 学习推荐（替代书单）──
  html += '<div class="rp-card" id="learning-card"><h4>📖 推荐学习</h4><div id="learning-tips" style="min-height:80px;display:flex;align-items:center;justify-content:center"><div class="spinner" style="width:20px;height:20px;border-width:2px"></div></div></div>';
  html+='</div></div>';

  document.getElementById('report-section').innerHTML = html;
  // 异步加载学习推荐
  loadLearningTips();
}

// ── 学习推荐引擎 ──
function loadLearningTips() {
  var container = document.getElementById('learning-tips');
  if (!container) return;
  
  // 从 node_status 中提取薄弱节点（confidence < 0.67），按 confidence 升序取前 5
  var ns = state.report.node_status || {};
  var weakNodes = Object.keys(ns)
    .filter(function(nid){ return (ns[nid].confidence || 0) < 0.67; })
    .sort(function(a, b){ return (ns[a].confidence || 0) - (ns[b].confidence || 0); })
    .slice(0, 5);
  
  if (weakNodes.length === 0) {
    container.innerHTML = '<div style="text-align:center;padding:20px;color:var(--gr)"><div style="font-size:32px;margin-bottom:8px">🎉</div><div style="font-weight:600">全部掌握！</div><div style="font-size:12px;color:var(--tx2);margin-top:4px">你已经掌握了所有测试节点的知识，继续保持</div></div>';
    return;
  }
  
  // 加载 tips — 前端直接加载本地 JSON，无服务端依赖
  var shown = state.learningShown || {};
  var nodeLabels = {};
  (state.nodes || []).forEach(function(n){ nodeLabels[n.id] = n.label || n.layer || ''; });

  function renderTips(tipsData) {
    var html = '';
    weakNodes.forEach(function(nid){
      var pool = tipsData[nid];
      var conf = Math.round((ns[nid].confidence || 0) * 100);
      var color = conf <= 35 ? 'var(--rd)' : 'var(--ye)';
      var label = nodeLabels[nid] || nid;

      if (!pool || !pool.length) {
        html += '<div class="learning-item" style="padding:14px;margin-bottom:10px;background:var(--card);border-radius:10px;border-left:3px solid ' + color + '">';
        html += '<span style="font-weight:600;font-size:13px">' + label + '</span>';
        html += '<div style="margin-top:6px;font-size:13px;color:var(--tx)">💡 建议深入学习该知识点。</div>';
        html += '</div>';
        return;
      }
      // 选择未展示过的 tip
      var shownForNode = shown[nid] || [];
      var chosenIdx = 0;
      for (var k = 0; k < pool.length; k++) {
        if (shownForNode.indexOf(k) < 0) { chosenIdx = k; break; }
      }
      var chosen = pool[chosenIdx] || pool[0];
      shownForNode.push(chosenIdx);
      if (shownForNode.length > pool.length) shownForNode = [chosenIdx];
      shown[nid] = shownForNode;

      html += '<div class="learning-item" style="padding:14px;margin-bottom:10px;background:var(--card);border-radius:10px;border-left:3px solid ' + color + '">';
      html += '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">';
      html += '<span style="font-weight:600;font-size:13px">' + label + '</span>';
      html += '<span style="font-size:11px;color:' + color + '">掌握度 ' + conf + '%</span>';
      html += '</div>';
      html += '<div style="font-size:13px;line-height:1.6;color:var(--tx)">💡 ' + chosen.tip + '</div>';
      if (chosen.example) {
        html += '<div style="margin-top:6px;font-size:12px;color:var(--tx2)">📌 ' + chosen.example + '</div>';
      }
      if (chosen.mnemonic) {
        html += '<div style="margin-top:4px;font-size:11px;color:var(--acc);font-style:italic">🧠 ' + chosen.mnemonic + '</div>';
      }
      html += '</div>';
    });
    container.innerHTML = html || '<div style="text-align:center;padding:20px;color:var(--tx2)">暂无推荐内容</div>';
    state.learningShown = shown;
    try { localStorage.setItem('kgds_learning_shown', JSON.stringify(shown)); } catch(e) {}
  }

  // 直接使用嵌入的 __LEARNING_TIPS 变量，零网络请求
  if (typeof __LEARNING_TIPS !== 'undefined') {
    renderTips(__LEARNING_TIPS);
  } else {
      var html = '';
      weakNodes.forEach(function(nid){
        var conf = Math.round((ns[nid].confidence || 0) * 100);
        html += '<div class="learning-item" style="padding:14px;margin-bottom:10px;background:var(--card);border-radius:10px;border-left:3px solid ' + (conf<=35?'var(--rd)':'var(--ye)') + '">';
        html += '<span style="font-weight:600;font-size:13px">' + (nodeLabels[nid]||nid) + '</span>';
        html += '<span style="font-size:11px;margin-left:8px;color:' + (conf<=35?'var(--rd)':'var(--ye)') + '">掌握度 ' + conf + '%</span>';
        html += '<div style="margin-top:6px;font-size:13px;color:var(--tx)">💡 建议深入学习该知识点。</div>';
        html += '</div>';
      });
      container.innerHTML = html || '<div style="text-align:center;padding:20px;color:var(--tx2)">暂无推荐内容</div>';
  }
}

function applyGraphColors() {
  if (!state.graphInstance || !state.report) return;
  if (state.blinkTimer) { clearInterval(state.blinkTimer); state.blinkTimer = null; }
  var ns = state.report.node_status || {};
  var updatedNodes = state.nodes.map(function(n){
    var node = Object.assign({}, n);
    var s = ns[n.id];
    if (s) {
      node.mastery = s.confidence || 0;
      if (node.mastery >= 0.67) { node.color = '#2ECC40'; node._blinkGroup = 'mastered'; }
      else if (node.mastery >= 0.4) { node.color = '#FFDC00'; node._blinkGroup = 'partial'; }
      else { node.color = '#FF4136'; node._blinkGroup = 'gap'; }
      node.val = (n.weight||3)*1.5*(0.5+node.mastery*0.5);
    } else {
      node.color = '#444466'; node._blinkGroup = 'untested';
      node.val = (n.weight||3)*1.0;
    }
    return node;
  });
  state.graphInstance.graphData({ nodes:updatedNodes, links:state.edges });
  startBlinkAnimation();
  setTimeout(function(){ state.graphInstance.cameraPosition({ x:80, y:40, z:120 }, { x:0, y:0, z:0 }, 1500); }, 500);
}

function startBlinkAnimation() {
  if (!state.graphInstance) return;
  var blinkPhase = false;
  state.blinkTimer = setInterval(function(){
    if (!state.graphInstance) { clearInterval(state.blinkTimer); state.blinkTimer = null; return; }
    blinkPhase = !blinkPhase;
    var gd = state.graphInstance.graphData();
    if (!gd || !gd.nodes) return;
    var changed = false;
    for (var i=0; i<gd.nodes.length; i++) {
      if (gd.nodes[i]._blinkGroup === 'gap') { gd.nodes[i].color = blinkPhase ? '#FF0000' : '#881111'; changed = true; }
    }
    if (changed) state.graphInstance.nodeColor(function(n){ return n.color; });
  }, 600);
}


/* ═══ 我的进步面板 ═══ */
function showProgress() {
  var overlay = document.getElementById('progress-overlay');
  if (!overlay) return;
  overlay.classList.add('show');
  renderProgress();
}
function hideProgress() {
  var overlay = document.getElementById('progress-overlay');
  if (overlay) overlay.classList.remove('show');
}

function renderProgress() {
  var container = document.getElementById('progress-body');
  if (!container) return;

  function render(sessions) {
    // 规范化字段：兼容服务端(overall_score/created_at)和本地(score/date)
    sessions.forEach(function(s){
      s.score = s.overall_score || s.total_score || s.score || 0;
      s.date = s.created_at || s.timestamp || s.date || '';
    });
    if (!sessions || sessions.length === 0) {
      container.innerHTML = '<div style="text-align:center;padding:40px 20px;color:var(--tx2)"><div style="font-size:48px;margin-bottom:12px">🌱</div><div style="font-size:15px;font-weight:600;margin-bottom:4px">还没有诊断记录</div><div style="font-size:13px">完成第一次诊断后，这里会展示你的进步轨迹</div></div>';
      return;
    }

    // 最新的在前面，反转为时间正序画趋势
    var sorted = sessions.slice().reverse();
    var scores = sorted.map(function(s){ var sc = s.overall_score || s.total_score || s.score || 0; return sc; });
    var latest = sessions[0];
    var prev = sessions.length > 1 ? sessions[1] : null;

    var totalTests = sessions.length;
    var maxScore = Math.max.apply(null, scores);
    var avgScore = Math.round(scores.reduce(function(a,b){return a+b;},0) / scores.length);
    var masteredCount = 0;
    if (latest.node_status) {
      Object.keys(latest.node_status).forEach(function(k){
        if (latest.node_status[k].confidence >= 0.67) masteredCount++;
      });
    }

    // 对比上次
    var diffHtml = '';
    if (prev) {
      var diff = (latest.overall_score || latest.total_score || latest.score || 0) - (prev.overall_score || prev.total_score || prev.score || 0);
      if (diff > 0) diffHtml = '<div style="color:var(--gr);font-size:14px;font-weight:700">↑ 比上次提高 ' + diff + ' 分 🎉</div>';
      else if (diff < 0) diffHtml = '<div style="color:var(--rd);font-size:14px;font-weight:700">↓ 比上次下降 ' + Math.abs(diff) + ' 分</div>';
      else diffHtml = '<div style="color:var(--tx2);font-size:14px">— 与上次持平</div>';
    }

    // 趋势 SVG
    var chartW = 320, chartH = 120, padX = 30, padY = 20;
    var plotW = chartW - padX * 2, plotH = chartH - padY * 2;
    var svgMin = Math.max(0, Math.min.apply(null, scores) - 10);
    var svgMax = Math.min(100, Math.max.apply(null, scores) + 10);
    if (svgMax === svgMin) svgMax = svgMin + 1;

    var points = scores.map(function(s, i){
      var x = padX + (i / Math.max(scores.length - 1, 1)) * plotW;
      var y = padY + plotH - ((s - svgMin) / (svgMax - svgMin)) * plotH;
      return x + ',' + y;
    }).join(' ');

    var dotsHtml = scores.map(function(s, i){
      var x = padX + (i / Math.max(scores.length - 1, 1)) * plotW;
      var y = padY + plotH - ((s - svgMin) / (svgMax - svgMin)) * plotH;
      var isLast = (i === scores.length - 1);
      return '<circle cx="' + x + '" cy="' + y + '" r="' + (isLast ? 5 : 3) + '" fill="' + (isLast ? '#007AFF' : '#5B9BD5') + '" opacity="' + (isLast ? 1 : 0.6) + '"/>' +
        (isLast ? '<text x="' + x + '" y="' + (y - 10) + '" text-anchor="middle" fill="#007AFF" font-size="12" font-weight="700">' + s + '</text>' : '');
    }).join('');

    // 里程碑
    var badges = [];
    if (totalTests >= 1) badges.push({ icon: '🎯', label: '首次诊断' });
    if (totalTests >= 3) badges.push({ icon: '🔥', label: '三次诊断' });
    if (totalTests >= 5) badges.push({ icon: '⭐', label: '五次诊断' });
    if (totalTests >= 10) badges.push({ icon: '💎', label: '十次诊断' });
    if (maxScore >= 60) badges.push({ icon: '🥉', label: '突破60' });
    if (maxScore >= 75) badges.push({ icon: '🥈', label: '突破75' });
    if (maxScore >= 90) badges.push({ icon: '🥇', label: '突破90' });
    if (maxScore >= 95) badges.push({ icon: '👑', label: '接近满分' });
    if (prev && latest.score > prev.score) badges.push({ icon: '📈', label: '进步之星' });

    var badgeHtml = badges.map(function(b){
      return '<div style="text-align:center;min-width:56px"><div style="font-size:22px">' + b.icon + '</div><div style="font-size:10px;color:var(--tx2);margin-top:2px">' + b.label + '</div></div>';
    }).join('');

    // 历史列表
    var histHtml = sessions.slice(0, 10).map(function(s, i){
      var d = s.date ? new Date(s.date).toLocaleDateString('zh-CN', {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}) : '';
      var sc = s.score >= 70 ? 'var(--gr)' : s.score >= 50 ? 'var(--ye)' : 'var(--rd)';
      var trend = '';
      if (i < sessions.length - 1) {
        var d2 = s.score - sessions[i+1].score;
        trend = d2 > 0 ? ' <span style="color:var(--gr);font-size:10px">↑' + d2 + '</span>' : d2 < 0 ? ' <span style="color:var(--rd);font-size:10px">↓' + Math.abs(d2) + '</span>' : '';
      }
      return '<div style="display:flex;justify-content:space-between;align-items:center;padding:10px 0;border-bottom:1px solid var(--bd2)">' +
        '<div><div style="font-size:13px;color:var(--tx)">第 ' + (sessions.length - i) + ' 次诊断</div><div style="font-size:11px;color:var(--tx2)">' + d + '</div></div>' +
        '<div style="text-align:right"><span style="font-size:18px;font-weight:700;color:' + sc + '">' + s.score + '</span><span style="font-size:11px;color:var(--tx2)">分</span>' + trend + '</div></div>';
    }).join('');

    container.innerHTML =
      // 顶部：最近一次
      '<div style="text-align:center;padding:20px 0 8px">' +
        '<div style="font-size:48px;font-weight:800;color:var(--acc)">' + latest.score + '<span style="font-size:16px;color:var(--tx2)">分</span></div>' +
        '<div style="font-size:12px;color:var(--tx2);margin-top:2px">最近一次 · ' + (latest.date ? new Date(latest.date).toLocaleDateString('zh-CN') : '') + '</div>' +
        '<div style="margin-top:6px">' + diffHtml + '</div>' +
      '</div>' +
      // 统计卡片
      '<div style="display:flex;gap:8px;padding:12px 0;flex-wrap:wrap">' +
        '<div style="flex:1;min-width:70px;background:var(--acc2);border-radius:10px;padding:12px;text-align:center"><div style="font-size:22px;font-weight:700;color:var(--acc)">' + totalTests + '</div><div style="font-size:11px;color:var(--tx2)">总诊断</div></div>' +
        '<div style="flex:1;min-width:70px;background:rgba(52,199,89,0.1);border-radius:10px;padding:12px;text-align:center"><div style="font-size:22px;font-weight:700;color:var(--gr)">' + maxScore + '</div><div style="font-size:11px;color:var(--tx2)">最高分</div></div>' +
        '<div style="flex:1;min-width:70px;background:rgba(255,204,0,0.1);border-radius:10px;padding:12px;text-align:center"><div style="font-size:22px;font-weight:700;color:#B8860B">' + avgScore + '</div><div style="font-size:11px;color:var(--tx2)">平均分</div></div>' +
        (masteredCount > 0 ? '<div style="flex:1;min-width:70px;background:rgba(155,89,182,0.1);border-radius:10px;padding:12px;text-align:center"><div style="font-size:22px;font-weight:700;color:#9B59B6">' + masteredCount + '</div><div style="font-size:11px;color:var(--tx2)">已掌握</div></div>' : '') +
      '</div>' +
      // 趋势图
      '<div style="margin:16px 0"><div style="font-size:13px;font-weight:600;margin-bottom:8px">📈 分数趋势</div>' +
      '<div style="background:var(--card);border-radius:10px;padding:8px;overflow-x:auto;-webkit-overflow-scrolling:touch">' +
      '<svg width="' + chartW + '" height="' + chartH + '" style="display:block;margin:0 auto">' +
        '<line x1="' + padX + '" y1="' + (chartH - padY) + '" x2="' + (chartW - padX) + '" y2="' + (chartH - padY) + '" stroke="#ddd" stroke-width="0.5"/>' +
        '<line x1="' + padX + '" y1="' + padY + '" x2="' + padX + '" y2="' + (chartH - padY) + '" stroke="#ddd" stroke-width="0.5"/>' +
        '<text x="' + (padX - 5) + '" y="' + (padY + 4) + '" text-anchor="end" fill="#aaa" font-size="9">' + svgMax + '</text>' +
        '<text x="' + (padX - 5) + '" y="' + (chartH - padY + 4) + '" text-anchor="end" fill="#aaa" font-size="9">' + svgMin + '</text>' +
        '<polyline points="' + points + '" fill="none" stroke="#007AFF" stroke-width="2" stroke-linejoin="round"/>' +
        dotsHtml +
      '</svg></div></div>' +
      // 里程碑
      (badges.length > 0 ? '<div style="margin:16px 0"><div style="font-size:13px;font-weight:600;margin-bottom:8px">🏆 里程碑</div><div style="display:flex;gap:10px;flex-wrap:wrap;justify-content:center;padding:8px 0">' + badgeHtml + '</div></div>' : '') +
      // 历史记录
      '<div style="margin:16px 0"><div style="font-size:13px;font-weight:600;margin-bottom:8px">📋 历次记录</div>' + histHtml + '</div>';
  }

  // 加载数据
  var token = localStorage.getItem('kgds_token');
  if (token) {
    fetch(API + '/api/sessions', { headers: authHeaders() })
      .then(function(r){ return r.json(); })
      .then(function(sessions){
        if (Array.isArray(sessions) && sessions.length > 0) { render(sessions); return; }
        throw new Error('empty');
      })
      .catch(function(){
        var h = JSON.parse(localStorage.getItem('kgds_history') || '[]');
        render(h);
      });
  } else {
    var h = JSON.parse(localStorage.getItem('kgds_history') || '[]');
    render(h);
  }
}

function retakeTest() {
  state.questions=[]; state.answers={}; state.currentQ=0; state.report=null;
  state.graphMode = 'full';
  state.internalCategory = null;
  updateInternalUI();
  var btn = document.getElementById('btn-full-graph');
  if (btn) { btn.style.display = 'none'; btn.classList.remove('active'); }
  document.getElementById('report-section').innerHTML = '';
  document.getElementById('q-card-area').innerHTML = '';
  document.getElementById('done-state').style.display = '';
  showPhase('profile');
  if (state.blinkTimer) { clearInterval(state.blinkTimer); state.blinkTimer = null; }
  if (state.graphInstance) {
    var resetNodes = state.nodes.map(function(n){ var node=Object.assign({},n); delete node.mastery; delete node._blinkGroup; return node; });
    state.graphInstance.graphData({ nodes:resetNodes, links:state.edges });
    state.graphInstance.nodeColor(function(n){ return n.color || '#888'; });
    state.graphInstance.nodeVal(function(n){ return (n.weight || 3) * 1.5; });
    state.graphInstance.cameraPosition({ x:100, y:50, z:150 }, { x:0, y:0, z:0 }, 1500);
  }
}

// ── Report Helpers ──
function getPraise(score, strengths, profile) {
  var name = (profile&&profile.name) ? profile.name : '伙伴';
  if (score>=85) return { title:'太棒了，'+name+'！', subtitle:'综合得分 '+score+' 分 | 已具备向高客经营和团队管理发展的知识储备' };
  if (score>=70) { var topLabel = strengths.length>0 ? (strengths[0].label||'基础知识') : '基础知识'; return { title:'不错的基础，'+name+'！', subtitle:'综合得分 '+score+' 分 | 优势在「'+topLabel+'」| 建议补强提升技能层' }; }
  if (score>=55) return { title:name+'，你的旅程刚刚开始', subtitle:'综合得分 '+score+' 分 | 基础知识有待巩固 | 别灰心，每个专家都从这里走过' };
  return { title:name+'，现在开始就是最好的时机', subtitle:'综合得分 '+score+' 分 | 建议从基础产品知识和合规要求开始 | 坚持三个月' };
}

function getBooks() {
  return [
    { title:'《保险学》',author:'魏华林、林宝清',reason:'保险基础理论的经典教材' },
    { title:'《人寿保险》',author:'Kenneth Black',reason:'寿险精算与产品设计的权威参考' },
    { title:'《重疾险革命》',author:'丁云生',reason:'重疾险销售实战心法' },
    { title:'《保险法》',author:'全国人大',reason:'保险从业者的法律底线' },
    { title:'《SPIN销售巨人》',author:'Neil Rackham',reason:'顾问式销售方法论的奠基之作' },
    { title:'《需求唤醒》',author:'Oren Klaff',reason:'高客面谈的框架设计和心理博弈' },
    { title:'《家族信托》',author:'韩良',reason:'保险金信托实操指南' },
    { title:'《影响力》',author:'Robert Cialdini',reason:'理解客户决策心理' },
  ];
}

// ── History ──
function saveResult() {
  var record = {
    session_id: state.report.session_id || ('local_'+Date.now()),
    timestamp: new Date().toISOString(),
    profile: state.profile,
    overall_score: state.report.total_score,
    total_correct: state.report.total_correct,
    total_questions: state.report.total_questions,
    layer_stats: state.report.layer_stats,
    node_status: state.report.node_status,
    rank: state.report.rank
  };
  try {
    var h = JSON.parse(localStorage.getItem('kgds_history')||'[]');
    h.unshift(record);
    if (h.length > 20) h = h.slice(0, 20);
    localStorage.setItem('kgds_history', JSON.stringify(h));
  } catch(e) {}
}

function loadHistory() {
  try { return JSON.parse(localStorage.getItem('kgds_history')||'[]'); }
  catch(e) { return []; }
}

function renderHistory() {
  var p = document.getElementById('history-list');
  if (!p) return;

  // Try server first
  if (state.user) {
    fetch(API + '/api/sessions', { headers: authHeaders() })
      .then(function(r) { return r.json(); })
      .then(function(sessions) {
        if (!Array.isArray(sessions)) throw new Error('not array');
        renderSessions(sessions, p);
      })
      .catch(function() { renderSessions([], p); });
  } else {
    renderSessions([], p);
  }
}

function renderSessions(sessions, p) {
  var h = loadHistory();
  if (sessions.length === 0 && h.length === 0) {
    p.innerHTML = '<div style="color:var(--tx2);font-size:13px;padding:20px;text-align:center">暂无历史记录</div>';
    return;
  }
  // Merge: prefer server sessions
  var serverIds = {};
  sessions.forEach(function(s) { serverIds[s.session_id] = true; });

  var all = sessions.concat(
    h.filter(function(r) { return !serverIds[r.session_id]; })
  );
  // Sort by timestamp desc
  all.sort(function(a, b) {
    var ta = a.timestamp || a.created_at || '';
    var tb = b.timestamp || b.created_at || '';
    return ta > tb ? -1 : 1;
  });

  var html = '';
  all.slice(0, 20).forEach(function(r, i) {
    var ts = r.timestamp || r.created_at || '';
    var d = new Date(ts);
    var ds = isNaN(d.getTime()) ? '' : (d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0')+' '+String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0'));
    var sc = r.overall_score || r.total_score || 0;
    var sco = sc>=70?'var(--gr)':(sc>=55?'var(--ye)':'var(--rd)');
    var nm = (r.profile&&r.profile.name) ? r.profile.name : '匿名';
    var rk = r.rank || computeRankLocal(r.layer_stats || {});
    html += '<div class="history-item" onclick="viewHistoryDetail(\''+r.session_id+'\',\''+i+'\')" style="cursor:pointer"><div style="display:flex;justify-content:space-between;align-items:center"><div><span style="color:var(--tx);font-weight:600">'+nm+'</span> <span style="font-size:12px">'+rk.icon+'</span> <span style="color:var(--tx2);font-size:11px">'+rk.title+' · '+ds+'</span></div><span style="color:'+sco+';font-weight:700;font-size:16px">'+sc+'</span></div><div style="font-size:11px;color:var(--tx2);margin-top:2px">'+(r.total_correct||0)+'/'+(r.total_questions||0)+' 题</div></div>';
  });
  html += '<div style="padding:12px;text-align:center"><button class="btn btn-secondary" onclick="clearHistory()" style="font-size:11px;padding:6px 14px">🗑 清除本地</button> <button class="btn btn-secondary" onclick="exportHistory()" style="font-size:11px;padding:6px 14px">📥 导出</button></div>';
  p.innerHTML = html;
  // Store sessions for detail view
  state._historyCache = all;
}

function viewHistoryDetail(sid, idx) {
  var h = state._historyCache;
  if (!h) { h = loadHistory(); }
  var r = null;
  for (var i = 0; i < h.length; i++) {
    if (String(h[i].session_id) === String(sid)) { r = h[i]; break; }
  }
  if (!r) r = h[idx];
  if (!r) return;

  state.report = {
    total_score: r.overall_score || r.total_score || 0,
    total_correct: r.total_correct || 0,
    total_questions: r.total_questions || 0,
    layer_stats: r.layer_stats || {},
    node_status: r.node_status || {},
    profile: r.profile || {},
    rank: r.rank || computeRankLocal(r.layer_stats || {})
  };
  state.profile = r.profile || {};
  state.answers = r.answers || {};
  state.questions = r.questions || [];
  showPhase('done');
  document.getElementById('done-state').innerHTML = '<div class="check">📋</div><div style="font-size:16px;font-weight:600;margin-bottom:4px">历史记录</div><button class="btn btn-primary" onclick="retakeTest()">🔄 重新测试</button>';
  renderReport();
  applyGraphColors();
}

function clearHistory() {
  if (confirm('确定清除所有本地测试记录？')) { localStorage.removeItem('kgds_history'); renderHistory(); }
}

function exportHistory() {
  var h = loadHistory();
  var blob = new Blob([JSON.stringify(h, null, 2)], { type: 'application/json' });
  var url = URL.createObjectURL(blob);
  var a = document.createElement('a');
  a.href = url;
  a.download = 'kgds_history_' + new Date().toISOString().slice(0, 10) + '.json';
  a.click();
  URL.revokeObjectURL(url);
}

function toggleHistory() {
  var s = document.getElementById('history-section');
  var b = document.getElementById('btn-toggle-history');
  if (s.style.display === 'none' || !s.style.display) {
    s.style.display = 'block';
    renderHistory();
    b.textContent = '📋 收起历史';
  } else {
    s.style.display = 'none';
    b.textContent = '📋 历史记录';
  }
}

// ── Keyboard ──
document.addEventListener('keydown', function(e) {
  if (state.questions.length && state.currentQ < state.questions.length) {
    if (e.key >= '1' && e.key <= '4') selectOption(parseInt(e.key) - 1);
    if (e.key === 'ArrowRight' || e.key === 'Enter') {
      var q = state.questions[state.currentQ];
      if (state.answers[q.id] !== undefined) nextQuestion();
    }
    if (e.key === 'ArrowLeft') prevQuestion();
  }
});

window.handleAuth = handleAuth;
window.showProgress = showProgress;
window.selectInternal = selectInternal;
window.updateInternalUI = updateInternalUI;
window.hideProgress = hideProgress;
window.renderProgress = renderProgress;
window.enterFullGraph = enterFullGraph;
window.exitFullGraph = exitFullGraph;