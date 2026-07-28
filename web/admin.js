/* KGDS Admin v1 */
var API = '';
var admin = JSON.parse(localStorage.getItem('kgds_admin') || 'null');
var s = { roles: [], questions: [], settings: {}, cr: '' };

function authH() { return admin ? { 'Authorization': 'Bearer ' + admin.token, 'Content-Type': 'application/json' } : { 'Content-Type': 'application/json' }; }
function toast(msg, type) { var t = document.getElementById('toast'); t.textContent = msg; t.className = 'toast ' + (type || 'success') + ' show'; setTimeout(function () { t.classList.remove('show'); }, 2500); }
function closeModal() { document.getElementById('modal').classList.remove('active'); }

// ── Init ──
(function () {
  if (admin) {
    document.getElementById('admin-name').textContent = '👤 ' + admin.name;
    document.getElementById('page-login').classList.remove('active');
    navTo('dashboard');
  }
})();

function navTo(p) {
  if (!admin && p !== 'login') p = 'login';
  document.querySelectorAll('.side-item').forEach(function (e) { e.classList.toggle('active', e.dataset.page === p); });
  document.querySelectorAll('.page,.login-page').forEach(function (e) { e.classList.remove('active'); });
  var t = document.getElementById('page-' + p); if (t) t.classList.add('active');
  if (p === 'dashboard') loadDashboard();
  if (p === 'roles') loadRoles();
  if (p === 'questions') loadQuestionRoles();
  if (p === 'users') loadUsers();
  if (p === 'flywheel') loadFlywheel();
  if (p === 'settings') loadSettings();
}

// ── Auth ──
function doLogin() {
  var em = document.getElementById('l-email').value.trim(), pw = document.getElementById('l-pw').value.trim();
  if (!em || !pw) return toast('请输入邮箱和密码', 'error');
  fetch(API + '/admin/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email: em, password: pw }) })
    .then(function (r) { return r.json(); }).then(function (d) {
      if (d.error) return toast(d.error, 'error');
      admin = d.user; localStorage.setItem('kgds_admin', JSON.stringify(admin));
      document.getElementById('admin-name').textContent = '👤 ' + admin.name;
      document.getElementById('page-login').classList.remove('active'); navTo('dashboard');
    }).catch(function (e) { toast('登录失败', 'error'); });
}
function doLogout() { localStorage.removeItem('kgds_admin'); admin = null; location.reload(); }

// ── Dashboard ──
function loadDashboard() {
  Promise.all([
    fetch(API + '/admin/roles', { headers: authH() }).then(function (r) { return r.json(); }).catch(function () { return []; }),
    fetch(API + '/admin/users', { headers: authH() }).then(function (r) { return r.json(); }).catch(function () { return []; }),
    fetch(API + '/admin/settings', { headers: authH() }).then(function (r) { return r.json(); }).catch(function () { return {}; })
  ]).then(function (r) {
    var rls = Array.isArray(r[0]) ? r[0] : [], us = Array.isArray(r[1]) ? r[1] : [], tq = 0;
    rls.forEach(function (x) { tq += x.question_count || 0; });
    document.getElementById('dash-cards').innerHTML =
      '<div class="card"><div class="num">' + rls.length + '</div><div class="lbl">岗位</div></div>' +
      '<div class="card"><div class="num">' + tq + '</div><div class="lbl">试题</div></div>' +
      '<div class="card"><div class="num">' + us.length + '</div><div class="lbl">用户</div></div>' +
      '<div class="card"><div class="num">' + (r[2].llm_model || '未配置') + '</div><div class="lbl">模型</div></div>';
  });
}

// ── Roles ──
function loadRoles() {
  fetch(API + '/admin/roles', { headers: authH() }).then(function (r) { return r.json(); }).then(function (roles) {
    if (!Array.isArray(roles)) return toast('加载失败', 'error');
    s.roles = roles; var sr = (document.getElementById('role-search') ? document.getElementById('role-search').value : '').toLowerCase();
    if (sr) roles = roles.filter(function (r) { return r.id.includes(sr) || (r.title || '').includes(sr); });
    if (!roles.length) { document.getElementById('role-list').innerHTML = '<div style="color:var(--tx2);padding:20px;text-align:center">暂无岗位</div>'; return; }
    var h = '<table class="tbl"><tr><th>ID</th><th>名称</th><th>行业</th><th>节点</th><th>试题</th><th style="width:120px">操作</th></tr>';
    roles.forEach(function (r) {
      h += '<tr><td><code style="color:var(--acc)">' + r.id + '</code></td><td>' + r.title + '</td><td>' + (r.industry || '-') + '</td><td>' + r.node_count + '</td><td>' + r.question_count + '</td><td>' +
        '<button class="btn btn-secondary btn-sm" onclick="editRoleMeta(\'' + r.id + '\')">✏️</button> ' +
        '<button class="btn btn-secondary btn-sm" onclick="editRoleGraph(\'' + r.id + '\')">🔗</button> ' +
        '<button class="btn btn-danger btn-sm" onclick="delRole(\'' + r.id + '\')">🗑</button></td></tr>';
    });
    document.getElementById('role-list').innerHTML = h + '</table>';
  });
}
function showRoleModal(rid) {
  var r = rid ? s.roles.find(function (x) { return x.id === rid; }) : null;
  var h = '<h3>' + (r ? '编辑' : '新建') + '岗位</h3>';
  h += '<div class="field"><label>岗位ID</label><input id="rm-id" value="' + (r ? r.id : '') + '" ' + (r ? 'readonly' : '') + '></div>';
  h += '<div class="field"><label>名称</label><input id="rm-title" value="' + (r ? r.title : '') + '"></div>';
  h += '<div class="field"><label>行业</label><input id="rm-industry" value="' + (r ? r.industry || '' : '') + '"></div>';
  h += '<div class="field"><label>描述</label><textarea id="rm-desc">' + (r ? r.description || '' : '') + '</textarea></div>';
  h += '<div class="modal-actions"><button class="btn btn-secondary" onclick="closeModal()">取消</button><button class="btn btn-primary" onclick="saveRole(\'' + (rid || '') + '\')">保存</button></div>';
  document.getElementById('modal-content').innerHTML = h; document.getElementById('modal').classList.add('active');
}
function saveRole(rid) {
  var id = document.getElementById('rm-id').value.trim(), title = document.getElementById('rm-title').value.trim();
  if (!id || !title) return toast('ID和名称不能为空', 'error');
  if (rid) {
    fetch(API + '/admin/roles/' + rid + '/meta', { method: 'PUT', headers: authH(), body: JSON.stringify({ title: title, industry: document.getElementById('rm-industry').value.trim(), description: document.getElementById('rm-desc').value.trim() }) })
      .then(function (r) { return r.json(); }).then(function (d) { if (d.ok) { closeModal(); loadRoles(); toast('已更新'); } else toast(d.error, 'error'); });
  } else {
    fetch(API + '/admin/roles', { method: 'POST', headers: authH(), body: JSON.stringify({ id: id, title: title, industry: document.getElementById('rm-industry').value.trim(), description: document.getElementById('rm-desc').value.trim() }) })
      .then(function (r) { return r.json(); }).then(function (d) { if (d.error) return toast(d.error, 'error'); closeModal(); loadRoles(); toast('已创建'); });
  }
}
function delRole(id) { if (!confirm('删除 "' + id + '"？')) return;
  fetch(API + '/admin/roles/' + id + '?token=' + admin.token, { method: 'DELETE', headers: authH() }).then(function (r) { return r.json(); }).then(function (d) {
    if (d.ok) { loadRoles(); toast('已删除'); } else toast(d.error, 'error');
  }); }
function editRoleMeta(rid) {
  fetch(API + '/admin/roles/' + rid + '/data', { headers: authH() }).then(function (r) { return r.json(); }).then(function (d) {
    if (d.error) return toast(d.error, 'error');
    var m = d.meta || {};
    var h = '<h3>编辑: ' + rid + '</h3>';
    h += '<div class="form-row"><div class="field"><label>名称</label><input id="rr-title" value="' + m.title + '"></div><div class="field"><label>行业</label><input id="rr-industry" value="' + (m.industry || '') + '"></div></div>';
    h += '<div class="field"><label>描述</label><textarea id="rr-desc">' + (m.description || '') + '</textarea></div>';
    h += '<div class="field"><label>题库 (' + (d.tests ? d.tests.length : 0) + '题)</label><textarea class="json-editor" id="rr-tests" style="min-height:250px">' + JSON.stringify(d.tests || [], null, 2) + '</textarea></div>';
    h += '<div class="modal-actions"><button class="btn btn-secondary" onclick="closeModal()">取消</button><button class="btn btn-primary" onclick="saveRoleFull(\'' + rid + '\')">保存</button></div>';
    document.getElementById('modal-content').innerHTML = h; document.getElementById('modal').classList.add('active');
  });
}
function saveRoleFull(id) {
  var meta = { title: document.getElementById('rr-title').value.trim(), industry: document.getElementById('rr-industry').value.trim(), description: document.getElementById('rr-desc').value.trim() };
  var tests; try { tests = JSON.parse(document.getElementById('rr-tests').value); } catch (e) { return toast('JSON错误', 'error'); }
  Promise.all([
    fetch(API + '/admin/roles/' + id + '/meta', { method: 'PUT', headers: authH(), body: JSON.stringify(meta) }),
    fetch(API + '/admin/roles/' + id + '/tests', { method: 'PUT', headers: authH(), body: JSON.stringify(tests) })
  ]).then(function () { closeModal(); loadRoles(); toast('已保存'); });
}
function editRoleGraph(rid) {
  fetch(API + '/admin/roles/' + rid + '/data', { headers: authH() }).then(function (r) { return r.json(); }).then(function (d) {
    if (d.error) return toast(d.error, 'error');
    var h = '<h3>图谱编辑: ' + rid + '</h3>';
    h += '<div class="field"><label>节点</label><textarea class="json-editor" id="rn-nodes" style="min-height:200px">' + JSON.stringify(d.nodes || [], null, 2) + '</textarea></div>';
    h += '<div class="field"><label>连线</label><textarea class="json-editor" id="rn-edges" style="min-height:150px">' + JSON.stringify(d.edges || [], null, 2) + '</textarea></div>';
    h += '<div class="modal-actions"><button class="btn btn-secondary" onclick="closeModal()">取消</button><button class="btn btn-primary" onclick="saveGraph(\'' + rid + '\')">保存</button></div>';
    document.getElementById('modal-content').innerHTML = h; document.getElementById('modal').classList.add('active');
  });
}
function saveGraph(rid) {
  var nd, ed;
  try { nd = JSON.parse(document.getElementById('rn-nodes').value); } catch (e) { return toast('节点JSON错误', 'error'); }
  try { ed = JSON.parse(document.getElementById('rn-edges').value); } catch (e) { return toast('连线JSON错误', 'error'); }
  Promise.all([
    fetch(API + '/admin/roles/' + rid + '/nodes', { method: 'PUT', headers: authH(), body: JSON.stringify(nd) }),
    fetch(API + '/admin/roles/' + rid + '/edges', { method: 'PUT', headers: authH(), body: JSON.stringify(ed) })
  ]).then(function () { closeModal(); loadRoles(); toast('图谱已保存'); });
}

// ── Questions ──
function loadQuestionRoles() {
  fetch(API + '/admin/roles', { headers: authH() }).then(function (r) { return r.json(); }).then(function (roles) {
    if (!Array.isArray(roles)) return;
    var sel = document.getElementById('q-role'); sel.innerHTML = '<option value="">-- 选择 --</option>';
    roles.forEach(function (r) { sel.innerHTML += '<option value="' + r.id + '">' + r.title + ' (' + (r.question_count || 0) + '题)</option>'; });
  });
}
function loadQuestions() {
  var rid = document.getElementById('q-role').value; if (!rid) { document.getElementById('question-list').innerHTML = ''; return; }
  s.cr = rid;
  fetch(API + '/admin/roles/' + rid + '/data', { headers: authH() }).then(function (r) { return r.json(); }).then(function (d) {
    if (d.error) return toast(d.error, 'error');
    var qs = d.tests || [], ly = document.getElementById('q-layer').value;
    if (ly && d.nodes) { var nids = {}; d.nodes.forEach(function (n) { nids[n.id] = n.layer; }); qs = qs.filter(function (q) { return nids[q.node_id] === ly; }); }
    s.questions = qs;
    document.getElementById('q-count').textContent = '共 ' + qs.length + ' 题';
    if (!qs.length) { document.getElementById('question-list').innerHTML = '<div style="color:var(--tx2);padding:20px;text-align:center">暂无试题</div>'; return; }
    var tl = { knowledge: '概念', scenario: '场景', application: '应用' };
    var h = '<table class="tbl"><tr><th>#</th><th>题目</th><th>节点</th><th>类型</th><th style="width:80px">操作</th></tr>';
    qs.forEach(function (q, i) {
      h += '<tr><td>' + (i + 1) + '</td><td style="max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + q.question + '</td><td><code style="font-size:10px">' + q.node_id + '</code></td><td>' + (tl[q.type] || q.type) + '</td><td>' +
        '<button class="btn btn-secondary btn-sm" onclick="editQ(' + i + ')">✏️</button> <button class="btn btn-danger btn-sm" onclick="delQ(' + i + ')">✕</button></td></tr>';
    });
    document.getElementById('question-list').innerHTML = h + '</table>';
  });
}
function showQuestionModal(eidx) {
  var q = eidx !== undefined ? s.questions[eidx] : null;
  var h = '<h3>' + (q ? '编辑' : '添加') + '试题</h3>';
  h += '<div class="field"><label>节点ID</label><input id="qq-node" value="' + (q ? q.node_id : '') + '"></div>';
  h += '<div class="form-row"><div class="field"><label>类型</label><select id="qq-type"><option value="knowledge" ' + (q && q.type === 'knowledge' ? 'selected' : '') + '>概念理解</option><option value="scenario" ' + (q && q.type === 'scenario' ? 'selected' : '') + '>场景判断</option><option value="application" ' + (q && q.type === 'application' ? 'selected' : '') + '>实际应用</option></select></div><div class="field"><label>难度</label><select id="qq-diff"><option value="1"' + (q && q.difficulty === 1 ? ' selected' : '') + '>★</option><option value="2"' + (q && q.difficulty === 2 ? ' selected' : '') + '>★★</option><option value="3"' + (q && q.difficulty === 3 ? ' selected' : '') + '>★★★</option></select></div></div>';
  h += '<div class="field"><label>题目</label><textarea id="qq-question">' + (q ? q.question : '') + '</textarea></div>';
  for (var i = 0; i < 4; i++) h += '<div class="field"><label>选项' + 'ABCD'[i] + '</label><input id="qq-o' + i + '" value="' + (q ? (q.options ? q.options[i] : '') : '') + '"></div>';
  h += '<div class="field"><label>正确答案 (0=A,1=B,2=C,3=D)</label><input id="qq-correct" type="number" min="0" max="3" value="' + (q ? q.correct : 0) + '"></div>';
  h += '<div class="modal-actions"><button class="btn btn-secondary" onclick="closeModal()">取消</button><button class="btn btn-primary" onclick="saveQ(' + (eidx !== undefined ? eidx : '-1') + ')">保存</button></div>';
  document.getElementById('modal-content').innerHTML = h; document.getElementById('modal').classList.add('active');
}
function saveQ(idx) {
  var q = { node_id: document.getElementById('qq-node').value.trim(), type: document.getElementById('qq-type').value, difficulty: parseInt(document.getElementById('qq-diff').value), question: document.getElementById('qq-question').value.trim(), options: [document.getElementById('qq-o0').value.trim(), document.getElementById('qq-o1').value.trim(), document.getElementById('qq-o2').value.trim(), document.getElementById('qq-o3').value.trim()], correct: parseInt(document.getElementById('qq-correct').value) };
  if (!q.node_id || !q.question) return toast('节点ID和题目不能为空', 'error');
  var qs = s.questions.slice(); if (idx >= 0) qs[idx] = q; else qs.push(q);
  fetch(API + '/admin/roles/' + s.cr + '/tests', { method: 'PUT', headers: authH(), body: JSON.stringify(qs) }).then(function (r) { return r.json(); }).then(function (d) { if (d.ok) { closeModal(); loadQuestions(); toast('已保存'); } else toast(d.error, 'error'); });
}
function delQ(idx) { if (!confirm('删除此题？')) return; var qs = s.questions.slice(); qs.splice(idx, 1);
  fetch(API + '/admin/roles/' + s.cr + '/tests', { method: 'PUT', headers: authH(), body: JSON.stringify(qs) }).then(function (r) { return r.json(); }).then(function (d) { if (d.ok) { loadQuestions(); toast('已删除'); } else toast(d.error, 'error'); }); }
function editQ(i) { showQuestionModal(i); }
function importQuestions() { document.getElementById('file-input').click(); }
function handleFileImport(e) {
  var file = e.target.files[0]; if (!file) return;
  var reader = new FileReader();
  reader.onload = function (ev) {
    try {
      var data = JSON.parse(ev.target.result);
      var arr = Array.isArray(data) ? data : (data.tests || data.questions || []);
      if (!arr.length) return toast('未找到试题数据', 'error');
      var qs = s.questions.slice();
      arr.forEach(function (q) { if (q.node_id && q.question) qs.push(q); });
      fetch(API + '/admin/roles/' + s.cr + '/tests', { method: 'PUT', headers: authH(), body: JSON.stringify(qs) }).then(function (r) { return r.json(); }).then(function (d) { if (d.ok) { loadQuestions(); toast('已导入 ' + arr.length + ' 题'); } else toast(d.error, 'error'); });
    } catch (e) { toast('JSON 解析失败: ' + e, 'error'); }
    e.target.value = '';
  };
  reader.readAsText(file);
}

// ── Users ──
function loadUsers() {
  var sr = document.getElementById('u-search') ? document.getElementById('u-search').value : '';
  fetch(API + '/admin/users?search=' + encodeURIComponent(sr), { headers: authH() }).then(function (r) { return r.json(); }).then(function (users) {
    if (!Array.isArray(users)) return toast('加载失败', 'error');
    if (!users.length) { document.getElementById('user-list').innerHTML = '<div style="color:var(--tx2);padding:20px;text-align:center">暂无用户</div>'; return; }
    var h = '<table class="tbl"><tr><th>ID</th><th>姓名</th><th>邮箱</th><th>身份</th><th>测试</th><th>注册时间</th><th style="width:60px">操作</th></tr>';
    users.forEach(function (u) {
      h += '<tr><td>' + u.id + '</td><td>' + u.name + '</td><td>' + u.email + '</td><td>' + (u.is_admin ? '管理员' : '用户') + '</td><td>' + (u.session_count || 0) + '次</td><td>' + (u.created_at || '').substring(0, 16) + '</td><td>' + (u.is_admin ? '' : '<button class="btn btn-danger btn-sm" onclick="delUser(' + u.id + ')">删除</button>') + '</td></tr>';
    });
    document.getElementById('user-list').innerHTML = h + '</table>';
  });
}
function delUser(id) { if (!confirm('确定删除此用户及其所有测试记录？')) return;
  fetch(API + '/admin/users/' + id + '?token=' + admin.token, { method: 'DELETE', headers: authH() }).then(function (r) { return r.json(); }).then(function (d) { if (d.ok) { loadUsers(); toast('已删除'); } else toast(d.error, 'error'); }); }

// ── Settings ──
function loadSettings() {
  fetch(API + '/admin/settings', { headers: authH() }).then(function (r) { return r.json(); }).then(function (st) {
    s.settings = st || {};
    document.getElementById('s-provider').value = st.llm_provider || 'openai';
    document.getElementById('s-key').value = st.llm_key || '';
    document.getElementById('s-base').value = st.llm_base || '';
    document.getElementById('s-model').value = st.llm_model || '';
    document.getElementById('s-webhook').value = st.webhook_url || '';
    document.getElementById('s-ext').value = st.external_data_url || '';
  }).catch(function () { });
}
function saveSettings() {
  var st = { llm_provider: document.getElementById('s-provider').value, llm_key: document.getElementById('s-key').value.trim(), llm_base: document.getElementById('s-base').value.trim(), llm_model: document.getElementById('s-model').value.trim(), webhook_url: document.getElementById('s-webhook').value.trim(), external_data_url: document.getElementById('s-ext').value.trim() };
  fetch(API + '/admin/settings', { method: 'PUT', headers: authH(), body: JSON.stringify(st) }).then(function (r) { return r.json(); }).then(function (d) { if (d.ok) toast('设置已保存'); else toast(d.error, 'error'); });
}

// ── Export ──
function doExport() {
  fetch(API + '/admin/export', { headers: authH() }).then(function (r) { return r.json(); }).then(function (data) {
    var blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    var a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'kgds_export_' + new Date().toISOString().slice(0, 10) + '.json'; a.click(); toast('导出完成');
  }).catch(function (e) { toast('导出失败', 'error'); });
}

// ── Flywheel Dashboard ──
function loadFlywheel() {
  var dash = document.getElementById('flywheel-dash');
  dash.innerHTML = '<div style="text-align:center;color:var(--tx2);padding:40px">加载飞轮数据...</div>';
  fetch(API + '/api/flywheel/stats', { headers: authH() })
    .then(function(r){ return r.ok ? r.json() : null; })
    .then(function(s){
      if (!s) { dash.innerHTML = '<div style="text-align:center;color:var(--rd);padding:40px">加载失败，请确认服务器已启动</div>'; return; }

      var sufClass = s.data_sufficiency || 'insufficient';
      var sufMap = { insufficient:'积累中', minimal:'起步', moderate:'中等', rich:'丰富' };
      var trend = s.recent_trend || '数据积累中，暂无趋势分析';
      var lastAnalysis = s.last_analysis ? s.last_analysis.slice(0, 10) + ' ' + s.last_analysis.slice(11, 19) : '暂无';

      var nodeConf = s.avg_node_confidence ? (s.avg_node_confidence * 100).toFixed(0) + '%' : '--';
      var qConf = s.avg_question_confidence ? (s.avg_question_confidence * 100).toFixed(0) + '%' : '--';

      var html = '';
      // 核心指标网格
      html += '<div class="fw-stat-grid">';
      html += '<div class="fw-stat"><div class="fwv">' + (s.total_sessions||0) + '</div><div class="fwl">累计诊断</div></div>';
      html += '<div class="fw-stat"><div class="fwv">' + (s.total_users||0) + '</div><div class="fwl">注册用户</div></div>';
      html += '<div class="fw-stat"><div class="fwv">' + (s.total_nodes||0) + '</div><div class="fwl">知识节点</div></div>';
      html += '<div class="fw-stat"><div class="fwv">' + (s.total_questions||0) + '</div><div class="fwl">试题总量</div></div>';
      html += '<div class="fw-stat"><div class="fwv">' + (s.auto_actions_applied||0) + '</div><div class="fwl">自动优化次数</div></div>';
      html += '<div class="fw-stat"><div class="fwv"><span class="fw-badge ' + sufClass + '">' + (sufMap[sufClass]||sufClass) + '</span></div><div class="fwl">数据充足度</div></div>';
      html += '</div>';

      // 校准详情
      html += '<div class="fw-box"><h4>📐 图谱校准</h4>';
      html += '<div class="desc">节点调整: <b>' + (s.nodes_adjusted||0) + '</b> 次 | 试题标记: <b>' + (s.questions_flagged||0) + '</b> 次</div>';
      html += '<div class="desc">节点平均置信度: <b>' + nodeConf + '</b> | 试题平均置信度: <b>' + qConf + '</b></div>';
      html += '<div class="desc">最近分析: <b>' + lastAnalysis + '</b></div>';
      html += '</div>';

      // 趋势
      html += '<div class="fw-box"><h4>📈 近期趋势</h4><div class="desc">' + trend + '</div></div>';

      // 飞轮机制说明
      html += '<div class="fw-box"><h4>⚙️ 飞轮机制</h4>';
      html += '<div class="desc">每次用户答题 → 三套模型同步进化：<br>';
      html += '① <b>图谱自校准</b>：根据答题数据调整节点难度权重和关系置信度<br>';
      html += '② <b>反盲选自进化</b>：标记高区分度试题，淘汰失效试题<br>';
      html += '③ <b>试题质量迭代</b>：IRT 参数动态更新（难度/区分度/猜测概率）<br>';
      html += '数据充足度达"丰富"后，系统自动触发写回节点和试题标注。</div>';
      html += '</div>';

      dash.innerHTML = html;
    })
    .catch(function(e){
      dash.innerHTML = '<div style="text-align:center;color:var(--rd);padding:40px">加载失败: ' + e + '</div>';
    });
}
