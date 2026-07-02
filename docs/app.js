// ArmInfer-Bench Dashboard — vanilla JS 客户端渲染
// P1: fetch ./data/dashboard.json (同目录,不跨目录)
// P2: headline 数字引用 headlines 字段(compute_headlines 单一计算)
// P3②: fetch 失败或字段缺失时显示"暂无数据"占位,不白屏

(function () {
  'use strict';

  var VARIANTS = ['naive', 'norepack', 'repack', 'kleidiai_only', 'kleidiai'];
  var QUANTS = ['q4_k_m', 'q4_0', 'q8_0'];
  var QUANT_LABELS = { q4_k_m: 'Q4_K_M', q4_0: 'Q4_0', q8_0: 'Q8_0' };

  var dashboard = null;
  var currentTs = null;
  var currentQuant = 'q4_k_m';

  // === P3②: 错误占位 ===
  function showError(msg) {
    document.getElementById('error-placeholder').classList.remove('hidden');
    document.getElementById('content').classList.add('hidden');
    var detail = document.getElementById('error-detail');
    detail.textContent = msg || '';
  }

  function hideError() {
    document.getElementById('error-placeholder').classList.add('hidden');
    document.getElementById('content').classList.remove('hidden');
  }

  // === P1: fetch ./data/dashboard.json ===
  async function loadDashboard() {
    try {
      var resp = await fetch('./data/dashboard.json');
      if (!resp.ok) throw new Error('HTTP ' + resp.status + ' ' + resp.statusText);
      var data = await resp.json();
      if (!data || !data.runs || !data.latest_timestamp) {
        throw new Error('dashboard.json missing required fields (runs/latest_timestamp)');
      }
      dashboard = data;
      hideError();
      initUI();
    } catch (e) {
      showError(e.message || String(e));
    }
  }

  function initUI() {
    var tsSelect = document.getElementById('ts-select');
    tsSelect.innerHTML = '';
    var timestamps = Object.keys(dashboard.runs).sort().reverse();
    timestamps.forEach(function (ts) {
      var opt = document.createElement('option');
      opt.value = ts;
      opt.textContent = ts;
      tsSelect.appendChild(opt);
    });
    tsSelect.value = dashboard.latest_timestamp;
    currentTs = dashboard.latest_timestamp;
    tsSelect.onchange = function () {
      currentTs = tsSelect.value;
      render();
    };

    // Tabs
    var tabs = document.getElementById('quant-tabs');
    tabs.innerHTML = '';
    QUANTS.forEach(function (q) {
      var btn = document.createElement('button');
      btn.className = 'tab' + (q === currentQuant ? ' active' : '');
      btn.textContent = QUANT_LABELS[q];
      btn.onclick = function () {
        currentQuant = q;
        tabs.querySelectorAll('.tab').forEach(function (t) { t.classList.remove('active'); });
        btn.classList.add('active');
        renderSpeedupTable();
      };
      tabs.appendChild(btn);
    });

    render();
  }

  function render() {
    var run = dashboard.runs[currentTs];
    if (!run) { showError('No data for timestamp ' + currentTs); return; }

    renderMeta(run);
    renderHeadlines(run);
    renderSpeedupTable(run);
    renderDecisionTable(run);
    renderPMU(run);
    renderProbeMatrix(run);
  }

  function renderMeta(run) {
    var el = document.getElementById('meta');
    var shortCommit = run.llama_commit ? run.llama_commit.substring(0, 7) : '?';
    el.innerHTML =
      '<span>CPU: ' + esc(run.cpu_model || '?') + '</span>' +
      '<span>OS: ' + esc(run.runner_os || '?') + '</span>' +
      '<span>llama: ' + shortCommit + '</span>' +
      '<span>compiler: ' + esc(run.compiler || '?') + '</span>';
  }

  // === P2: Headline cards — 数字引用 headlines 单一计算 ===
  function renderHeadlines(run) {
    var container = document.getElementById('headline-cards');
    container.innerHTML = '';
    var headlines = run.headlines || {};

    var order = [
      { q: 'q8_0', title: 'Q8_0 — KleidiAI 真赢' },
      { q: 'q4_0', title: 'Q4_0 — 打平' },
      { q: 'q4_k_m', title: 'Q4_K_M — KleidiAI no-op' }
    ];

    order.forEach(function (item) {
      var h = headlines[item.q];
      if (!h) return;
      var card = document.createElement('div');
      card.className = 'card verdict-' + (h.verdict || 'tie');

      var badge = document.createElement('span');
      badge.className = 'card-verdict-badge';
      badge.textContent = h.verdict || '?';

      var title = document.createElement('div');
      title.className = 'card-title';
      title.textContent = item.title;

      var headline = document.createElement('div');
      headline.className = 'card-headline';
      // P2: narrative 直接引用 compute_headlines() 产出,不手打
      headline.textContent = h.narrative || '(no narrative)';

      var subtext = document.createElement('div');
      subtext.className = 'card-subtext';
      if (item.q === 'q8_0') {
        subtext.textContent = 'kleidiai_active=' + h.kai_active + ', source=verbose_log_primary_kernel';
      } else if (item.q === 'q4_0') {
        subtext.textContent = 'kai_mem=' + h.kai_mem + 'MB < repack_mem=' + h.repack_mem + 'MB (G5 tie-break)';
      } else {
        subtext.textContent = 'KleidiAI 未接管(三重确认:源码覆盖空 + prefill 噪声内 + source=no_runtime_takeover_kquant_noop)';
      }

      // P2: numbers from headlines single computation
      var numbers = document.createElement('div');
      numbers.className = 'card-numbers';
      numbers.innerHTML =
        '<span><span class="label">kai decode:</span> <span class="value">' + fmt(h.kai_decode) + '</span></span>' +
        '<span><span class="label">repack decode:</span> <span class="value">' + fmt(h.repack_decode) + '</span></span>' +
        '<span><span class="label">diff:</span> <span class="value">' + (h.decode_diff_pct >= 0 ? '+' : '') + fmt(h.decode_diff_pct) + '%</span></span>' +
        '<span><span class="label">kai gain:</span> <span class="value">' + fmt(h.kai_gain_vs_norepack) + '×</span></span>' +
        '<span><span class="label">repack gain:</span> <span class="value">' + fmt(h.repack_gain_vs_norepack) + '×</span></span>';

      card.appendChild(badge);
      card.appendChild(title);
      card.appendChild(headline);
      card.appendChild(subtext);
      card.appendChild(numbers);
      container.appendChild(card);
    });
  }

  function renderSpeedupTable(run) {
    run = run || dashboard.runs[currentTs];
    var container = document.getElementById('speedup-table');
    var records = run.speed_records || {};
    var naiveKey = 'naive-' + currentQuant;
    var naive = records[naiveKey];
    if (!naive) { container.innerHTML = '<p class="card-subtext">暂无 ' + QUANT_LABELS[currentQuant] + ' 数据</p>'; return; }

    var html = '<table><thead><tr>';
    html += '<th>variant</th>';
    html += '<th>prefill tok/s</th>';
    html += '<th>decode tok/s</th>';
    html += '<th>TTFT (推算)</th>';
    html += '<th>peak mem MB</th>';
    html += '<th>pp speedup</th>';
    html += '<th>tg speedup</th>';
    html += '<th>ttft ratio</th>';
    html += '<th>mem ratio</th>';
    html += '<th>k_compiled</th>';
    html += '<th>k_active</th>';
    html += '<th>repack_active</th>';
    html += '<th>probe sources</th>';
    html += '</tr></thead><tbody>';

    // Find best variant (highest decode, G5 tie-break by memory)
    var bestV = findBestVariant(records, currentQuant);

    VARIANTS.forEach(function (v) {
      var key = v + '-' + currentQuant;
      var r = records[key];
      if (!r) return;
      var isNaive = v === 'naive';
      var isBest = v === bestV;
      var cls = isNaive ? 'naive-row' : (isBest ? 'best-row' : '');

      var ppSpeedup = safeRatio(r.prefill_tok_s, naive.prefill_tok_s);
      var tgSpeedup = safeRatio(r.decode_tok_s, naive.decode_tok_s);
      var ttftRatio = safeRatio(r.ttft_ms, naive.ttft_ms);
      var memRatio = safeRatio(r.peak_mem_mb, naive.peak_mem_mb);

      html += '<tr class="' + cls + '">';
      html += '<td>' + v + (isBest ? ' ★' : '') + '</td>';
      html += '<td>' + fmt3(r.prefill_tok_s) + '</td>';
      html += '<td>' + fmt3(r.decode_tok_s) + '</td>';
      html += '<td title="ttft_ms = pp_n / prefill_tok_s × 1000 (pp_n=512)">推算 ' + fmt1(r.ttft_ms) + '</td>';
      html += '<td>' + fmt1(r.peak_mem_mb) + '</td>';
      html += speedupCell(ppSpeedup);
      html += speedupCell(tgSpeedup);
      html += speedupCell(ttftRatio, true);
      html += speedupCell(memRatio, true);
      html += '<td class="' + (r.kleidiai_compiled ? 'probe-true' : 'probe-false') + '">' + r.kleidiai_compiled + '</td>';
      html += '<td class="' + (r.kleidiai_active ? 'probe-true' : 'probe-false') + '">' + r.kleidiai_active + '</td>';
      html += '<td class="' + (r.repack_active ? 'probe-true' : 'probe-false') + '">' + r.repack_active + '</td>';
      html += '<td style="font-size:11px;color:var(--text-dim)">k:' + esc(r.kleidiai_active_source || '') + '<br>r:' + esc(r.repack_active_source || '') + '</td>';
      html += '</tr>';
    });

    html += '</tbody></table>';
    container.innerHTML = html;
  }

  function findBestVariant(records, quant) {
    // G5 tie-break: highest decode, if <5% diff take lower memory
    var candidates = VARIANTS.map(function (v) {
      var r = records[v + '-' + quant];
      return r ? { v: v, decode: r.decode_tok_s, mem: r.peak_mem_mb } : null;
    }).filter(Boolean);
    if (candidates.length === 0) return null;
    var bestDecode = Math.max.apply(null, candidates.map(function (c) { return c.decode; }));
    var withinNoise = candidates.filter(function (c) {
      return Math.abs(c.decode - bestDecode) / bestDecode < 0.05;
    });
    if (withinNoise.length > 1) {
      withinNoise.sort(function (a, b) { return a.mem - b.mem; });
      return withinNoise[0].v;
    }
    return withinNoise[0].v;
  }

  function speedupCell(val, inverse) {
    if (val === null || val === undefined) return '<td>—</td>';
    var cls = 'speedup-cell';
    if (!inverse) {
      if (val > 1.05) cls += ' high';
      else if (val < 0.95) cls += ' low';
    } else {
      // ttft/mem ratio: lower is better
      if (val < 0.95) cls += ' high';
      else if (val > 1.05) cls += ' low';
    }
    return '<td class="' + cls + '">' + fmt3(val) + '×</td>';
  }

  // === Decision table (render markdown) ===
  function renderDecisionTable(run) {
    var md = run.decision_table_md || '(无决策表数据)';
    document.getElementById('decision-table').innerHTML = renderMarkdown(md);
  }

  // === PMU summary ===
  function renderPMU(run) {
    var pmu = run.pmu_summary || {};
    var container = document.getElementById('pmu-summary');
    var html = '';

    html += pmuItem('/sys/bus/event_source/devices 含 armv8_pmuv3_0', pmu.armv8_pmuv3_0_present, true);
    html += pmuItem('arm_spe (SPE) 存在', pmu.arm_spe_present, true);
    html += pmuItem('perf stat cycles/instructions', pmu.perf_stat_ok, true);

    if (pmu.conclusion) {
      html += '<div class="pmu-conclusion">' + esc(pmu.conclusion) + '</div>';
    }
    html += '<div class="pmu-conclusion" style="margin-top:8px;font-size:12px;color:var(--text-dim);">完整 pmu_probe.log 见 CI artifact(全程 AI 参赛佐证)。T3b 实测:SPE 不可用,锁 fallback 叙事(perf stat 软件事件 + llama-bench -v + 消融链当瓶颈分解)。</div>';

    container.innerHTML = html;
  }

  function pmuItem(label, value, boolVal) {
    var valClass = 'pmu-value';
    var valText;
    if (boolVal) {
      valClass += value ? ' ok' : ' fail';
      valText = value ? 'True' : 'False';
    } else {
      valText = String(value);
    }
    return '<div class="pmu-item"><span class="pmu-label">' + esc(label) + '</span><span class="' + valClass + '">' + valText + '</span></div>';
  }

  // === Probe matrix ===
  function renderProbeMatrix(run) {
    var records = run.speed_records || {};
    var container = document.getElementById('probe-matrix');
    var html = '<table><thead><tr><th>variant \\ quant</th>';
    QUANTS.forEach(function (q) { html += '<th>' + QUANT_LABELS[q] + '</th>'; });
    html += '</tr></thead><tbody>';

    VARIANTS.forEach(function (v) {
      html += '<tr><td>' + v + '</td>';
      QUANTS.forEach(function (q) {
        var r = records[v + '-' + q];
        if (!r) { html += '<td>—</td>'; return; }
        var kCls = r.kleidiai_active ? 'probe-true' : 'probe-false';
        var rCls = r.repack_active ? 'probe-true' : 'probe-false';
        html += '<td style="font-size:11px;text-align:center;">';
        html += '<span class="' + kCls + '">k=' + r.kleidiai_active + '</span><br>';
        html += '<span class="' + rCls + '">r=' + r.repack_active + '</span><br>';
        html += '<span style="color:var(--text-dim);font-size:10px">' + esc((r.kleidiai_active_source || '').replace(/_/g, ' ')) + '</span>';
        html += '</td>';
      });
      html += '</tr>';
    });

    html += '</tbody></table>';
    container.innerHTML = html;
  }

  // === Minimal Markdown renderer (handles tables, headers, bold, lists, code) ===
  function renderMarkdown(md) {
    var lines = md.split('\n');
    var html = '';
    var i = 0;
    var inTable = false;
    var inList = false;

    while (i < lines.length) {
      var line = lines[i];

      // Table detection
      if (line.trim().startsWith('|') && line.trim().endsWith('|')) {
        if (!inTable) {
          // Check if next line is separator
          if (i + 1 < lines.length && /^\s*\|[\s\-:|]+\|\s*$/.test(lines[i + 1])) {
            inTable = true;
            html += '<table><thead><tr>';
            var headers = parseTableRow(line);
            headers.forEach(function (h) { html += '<th>' + esc(h.trim()) + '</th>'; });
            html += '</tr></thead><tbody>';
            i += 2; // skip header + separator
            continue;
          }
        } else {
          var cells = parseTableRow(line);
          html += '<tr>';
          cells.forEach(function (c) { html += '<td>' + inlineMd(c.trim()) + '</td>'; });
          html += '</tr>';
          i++;
          continue;
        }
      } else if (inTable) {
        html += '</tbody></table>';
        inTable = false;
      }

      // Headers
      if (line.startsWith('### ')) { html += '<h3>' + inlineMd(line.slice(4)) + '</h3>'; i++; continue; }
      if (line.startsWith('## ')) { html += '<h2>' + inlineMd(line.slice(3)) + '</h2>'; i++; continue; }
      if (line.startsWith('# ')) { html += '<h2>' + inlineMd(line.slice(2)) + '</h2>'; i++; continue; }

      // List items
      if (line.trim().startsWith('- ')) {
        if (!inList) { html += '<ul>'; inList = true; }
        html += '<li>' + inlineMd(line.trim().slice(2)) + '</li>';
        i++;
        continue;
      } else if (inList) {
        html += '</ul>';
        inList = false;
      }

      // Empty line
      if (line.trim() === '') { i++; continue; }

      // Paragraph
      html += '<p>' + inlineMd(line) + '</p>';
      i++;
    }

    if (inTable) html += '</tbody></table>';
    if (inList) html += '</ul>';
    return html;
  }

  function parseTableRow(line) {
    var trimmed = line.trim().slice(1, -1); // remove leading | and trailing |
    return trimmed.split('|');
  }

  function inlineMd(text) {
    // Bold
    text = text.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    // Code
    text = text.replace(/`(.+?)`/g, '<code>$1</code>');
    return esc(text, true);
  }

  // === Helpers ===
  function esc(s, allowTags) {
    if (s === null || s === undefined) return '';
    s = String(s);
    if (allowTags) {
      // Already has tags from inlineMd, just escape remaining
      return s;
    }
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function fmt(v) { return v === null || v === undefined ? '—' : (typeof v === 'number' ? v.toFixed(2) : v); }
  function fmt1(v) { return v === null || v === undefined ? '—' : v.toFixed(1); }
  function fmt3(v) { return v === null || v === undefined ? '—' : v.toFixed(3); }
  function safeRatio(a, b) { return (b && b > 0) ? a / b : null; }

  // === Boot ===
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', loadDashboard);
  } else {
    loadDashboard();
  }
})();
