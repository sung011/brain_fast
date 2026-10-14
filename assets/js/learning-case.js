/*
 * 학습자 문제 풀이·채점 결과 (templates/learning_case.html)
 *
 * 흐름: GET /learning/problem → 영상(GET /learning/problem/{idx}/image) → ROI 그리기
 *      → POST /learning/roi-grade → 결과 화면 → 다음 케이스(응답의 next)
 * 좌표: ROI 는 영상 픽셀 좌표로 들고 있다가, 제출할 때 0~1 로 바꿔 보낸다.
 * 로그인 정보는 localStorage 'mediscan.user'({idx}) 가 있으면 쓴다. 없으면 익명 제출.
 */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const PARTS = {1: ['brain', '뇌'], 2: ['chest', '흉부'], 3: ['abdomen', '복부'], 4: ['knee', '무릎']};
  const MODALS = {1: ['xray', 'X-ray'], 2: ['ct', 'CT'], 3: ['mri', 'MRI']};
  const PART_CODE = {brain: 1, chest: 2, thorax: 2, abdomen: 3, knee: 4};
  const MODAL_CODE = {xray: 1, 'x-ray': 1, ct: 2, mri: 3};
  const RESULT_TEXT = {
    '정답': ['correct', '정답', '이상 부위를 잘 짚었습니다.'],
    '부분정답': ['partial', '부분 정답', '정답 영역과 일부만 겹칩니다. 위치를 다시 확인해 보세요.'],
    '오답': ['wrong', '오답', '채점 기준 영역과 겹치지 않습니다. 결과 영상을 확인해 보세요.'],
  };
  const MIN_SCALE = 0.1, MAX_SCALE = 8, HANDLE = 7;

  const query = new URLSearchParams(location.search);
  const want = {
    part: PART_CODE[(query.get('part') || 'chest').toLowerCase()] || null,
    modal: MODAL_CODE[(query.get('modal') || 'xray').toLowerCase()] || null,
  };
  const user = (() => {
    try { return JSON.parse(localStorage.getItem('mediscan.user') || 'null'); } catch { return null; }
  })();
  const solved = [];   // 익명일 때 이번 접속에서 푼 문제 (서버는 user_idx 로 걸러 준다)

  const canvas = $('stage');
  const ctx = canvas.getContext('2d');
  const state = {
    problem: null, paths: [], slice: 0, image: null, blobs: {},
    scale: 1, ox: 0, oy: 0, bright: 100, contrast: 100,
    tool: 'box', roi: null, drag: null, glossary: [], chip: '전체',
  };

  /* ---------- 공통 ---------- */
  function setStatus(text) { $('status').textContent = text || ''; $('status').hidden = !text; }
  function viewerMessage(text) { $('viewerMsg').textContent = text || ''; $('viewerMsg').hidden = !text; }

  async function api(url, options = {}) {
    const response = await fetch(url, {cache: 'no-store', ...options});
    let data = null;
    try { data = await response.json(); } catch { /* 이미지·HTML 오류 */ }
    if (!response.ok) {
      const detail = data && (typeof data.detail === 'string' ? data.detail : data.detail?.message);
      throw new Error(detail || `요청을 처리하지 못했습니다. (${response.status})`);
    }
    return data;
  }

  function labelOf(table, code) { return (table[Number(code)] || [null, '알 수 없음'])[1]; }
  function nameOf(table, code) { return (table[Number(code)] || [null])[0]; }

  /* ---------- 문제 불러오기 ---------- */
  async function loadProblem(problem) {
    showSolve();
    setStatus('');
    state.roi = null; updateSubmit();
    try {
      if (!problem) {
        const params = new URLSearchParams();
        if (want.part) params.set('st_part', want.part);
        if (want.modal) params.set('st_modal', want.modal);
        if (user?.idx) params.set('user_idx', user.idx);
        solved.forEach(idx => params.append('exclude_idxs', idx));
        problem = (await api('/learning/problem?' + params)).problem;
      }
    } catch (error) {
      viewerMessage(error.message);
      $('caseGuide').textContent = '문제를 불러오지 못했습니다.';
      return;
    }
    state.problem = problem;
    state.paths = parsePaths(problem.st_image);
    state.blobs = {};
    const part = labelOf(PARTS, problem.st_part), modal = labelOf(MODALS, problem.st_modal);
    $('caseNo').textContent = String(problem.idx).padStart(2, '0');
    $('crumb').textContent = `${part} · ${modal} · 문제 풀이`;
    $('caseGuide').textContent = `제시된 ${part} ${modal}에서 이상 소견이 의심되는 부위를 원이나 박스로 표시한 뒤 제출하세요.`;
    $('sliceBox').hidden = state.paths.length < 2;
    $('sliceRange').max = Math.max(0, state.paths.length - 1);
    $('sliceRange').value = 0;
    loadGlossary(problem);
    await showSlice(0);
  }

  function parsePaths(stImage) {
    const raw = (stImage || '').trim();
    if (raw.startsWith('[')) {
      try { const list = JSON.parse(raw); if (Array.isArray(list) && list.length) return list.map(String); } catch { /* 단일 영상 */ }
    }
    return raw ? [raw] : [];
  }

  async function imageBlob(index) {
    if (!state.blobs[index]) {
      const response = await fetch(`/learning/problem/${state.problem.idx}/image?slice_index=${index}`);
      if (!response.ok) {
        let detail = ''; try { detail = (await response.json()).detail; } catch { /* 본문 없음 */ }
        throw new Error(detail || `영상을 불러오지 못했습니다. (${response.status})`);
      }
      state.blobs[index] = await response.blob();
    }
    return state.blobs[index];
  }

  async function showSlice(index) {
    state.slice = index;
    $('sliceLabel').textContent = `${index + 1} / ${state.paths.length || 1}`;
    viewerMessage('영상을 불러오는 중입니다.');
    try {
      const url = URL.createObjectURL(await imageBlob(index));
      const img = new Image();
      await new Promise((resolve, reject) => { img.onload = resolve; img.onerror = () => reject(new Error('영상 형식을 읽지 못했습니다.')); img.src = url; });
      const keepView = state.image && state.image.naturalWidth === img.naturalWidth && state.image.naturalHeight === img.naturalHeight;
      state.image = img;
      if (!keepView) fit();
      viewerMessage('');
      draw();
    } catch (error) {
      viewerMessage(error.message);
    }
  }

  /* ---------- 보기 (확대·이동·밝기) ---------- */
  function resizeCanvas() {
    const rect = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(rect.width * dpr);
    canvas.height = Math.round(rect.height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function fit() {
    if (!state.image) return;
    const rect = canvas.getBoundingClientRect();
    const s = Math.min(rect.width / state.image.naturalWidth, rect.height / state.image.naturalHeight) * 0.95;
    state.scale = Math.max(MIN_SCALE, Math.min(MAX_SCALE, s));
    state.ox = (rect.width - state.image.naturalWidth * state.scale) / 2;
    state.oy = (rect.height - state.image.naturalHeight * state.scale) / 2;
  }

  function zoomAt(factor, sx, sy) {
    const next = Math.max(MIN_SCALE, Math.min(MAX_SCALE, state.scale * factor));
    state.ox = sx - (sx - state.ox) * (next / state.scale);
    state.oy = sy - (sy - state.oy) * (next / state.scale);
    state.scale = next;
    draw();
  }

  function centre() { const r = canvas.getBoundingClientRect(); return [r.width / 2, r.height / 2]; }
  const toImage = (sx, sy) => [(sx - state.ox) / state.scale, (sy - state.oy) / state.scale];
  const toScreen = (ix, iy) => [ix * state.scale + state.ox, iy * state.scale + state.oy];

  function draw() {
    const rect = canvas.getBoundingClientRect();
    ctx.clearRect(0, 0, rect.width, rect.height);
    if (state.image) {
      ctx.save();
      ctx.filter = `brightness(${state.bright}%) contrast(${state.contrast}%)`;
      ctx.imageSmoothingQuality = 'high';
      ctx.drawImage(state.image, state.ox, state.oy, state.image.naturalWidth * state.scale, state.image.naturalHeight * state.scale);
      ctx.restore();
    }
    $('readout').textContent = `밝기 ${state.bright}% / 대비 ${state.contrast}% · 배율 ${Math.round(state.scale * 100)}%`;
    if (state.roi) drawRoi(state.roi);
  }

  function roiScreen(roi) {
    const [x1, y1] = toScreen(roi.x, roi.y);
    const [x2, y2] = toScreen(roi.x + roi.w, roi.y + roi.h);
    return {x1, y1, x2, y2};
  }

  function handles(r) {
    const mx = (r.x1 + r.x2) / 2, my = (r.y1 + r.y2) / 2;
    return {nw: [r.x1, r.y1], n: [mx, r.y1], ne: [r.x2, r.y1], e: [r.x2, my],
            se: [r.x2, r.y2], s: [mx, r.y2], sw: [r.x1, r.y2], w: [r.x1, my]};
  }

  function drawRoi(roi) {
    const r = roiScreen(roi);
    ctx.save();
    ctx.strokeStyle = '#29d3e0';
    ctx.lineWidth = 2;
    ctx.setLineDash([6, 4]);
    ctx.beginPath();
    if (roi.type === 'circle') ctx.ellipse((r.x1 + r.x2) / 2, (r.y1 + r.y2) / 2, Math.abs(r.x2 - r.x1) / 2, Math.abs(r.y2 - r.y1) / 2, 0, 0, Math.PI * 2);
    else ctx.rect(r.x1, r.y1, r.x2 - r.x1, r.y2 - r.y1);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = '#ffffff';
    Object.values(handles(r)).forEach(([hx, hy]) => { ctx.fillRect(hx - HANDLE / 2, hy - HANDLE / 2, HANDLE, HANDLE); ctx.strokeRect(hx - HANDLE / 2, hy - HANDLE / 2, HANDLE, HANDLE); });
    ctx.restore();
  }

  /* ---------- ROI 그리기·고치기 ---------- */
  function pointer(event) { const r = canvas.getBoundingClientRect(); return [event.clientX - r.left, event.clientY - r.top]; }

  function hitHandle(sx, sy) {
    if (!state.roi) return null;
    for (const [name, [hx, hy]] of Object.entries(handles(roiScreen(state.roi)))) {
      if (Math.abs(sx - hx) <= HANDLE && Math.abs(sy - hy) <= HANDLE) return name;
    }
    return null;
  }

  function insideRoi(sx, sy) {
    if (!state.roi) return false;
    const r = roiScreen(state.roi);
    if (state.roi.type === 'circle') {
      const cx = (r.x1 + r.x2) / 2, cy = (r.y1 + r.y2) / 2, rx = (r.x2 - r.x1) / 2 || 1, ry = (r.y2 - r.y1) / 2 || 1;
      return ((sx - cx) / rx) ** 2 + ((sy - cy) / ry) ** 2 <= 1;
    }
    return sx >= r.x1 && sx <= r.x2 && sy >= r.y1 && sy <= r.y2;
  }

  function clampRoi(roi) {
    const W = state.image.naturalWidth, H = state.image.naturalHeight;
    let {x, y, w, h} = roi;
    if (w < 0) { x += w; w = -w; }
    if (h < 0) { y += h; h = -h; }
    x = Math.max(0, Math.min(W, x)); y = Math.max(0, Math.min(H, y));
    w = Math.min(w, W - x); h = Math.min(h, H - y);
    return {...roi, x, y, w, h};
  }

  canvas.addEventListener('pointerdown', event => {
    if (!state.image || event.button > 0) return;
    canvas.setPointerCapture(event.pointerId);
    const [sx, sy] = pointer(event);
    const [ix, iy] = toImage(sx, sy);
    const handle = (state.tool === 'box' || state.tool === 'circle') && hitHandle(sx, sy);
    if (state.tool === 'pan') state.drag = {mode: 'pan', sx, sy, ox: state.ox, oy: state.oy};
    else if (state.tool === 'window') state.drag = {mode: 'window', sx, sy, b: state.bright, c: state.contrast};
    else if (handle) state.drag = {mode: 'resize', handle, start: {...state.roi}, ix, iy};
    else if (insideRoi(sx, sy)) state.drag = {mode: 'move', start: {...state.roi}, ix, iy};
    else { state.roi = {type: state.tool, x: ix, y: iy, w: 0, h: 0}; state.drag = {mode: 'create', ix, iy}; }
  });

  canvas.addEventListener('pointermove', event => {
    const [sx, sy] = pointer(event);
    if (!state.drag) {
      canvas.style.cursor = state.tool === 'pan' ? 'grab' : state.tool === 'window' ? 'ns-resize'
        : hitHandle(sx, sy) ? 'nwse-resize' : insideRoi(sx, sy) ? 'move' : 'crosshair';
      return;
    }
    const d = state.drag;
    const [ix, iy] = toImage(sx, sy);
    if (d.mode === 'pan') { state.ox = d.ox + sx - d.sx; state.oy = d.oy + sy - d.sy; }
    else if (d.mode === 'window') {
      state.contrast = Math.round(Math.max(20, Math.min(300, d.c + (sx - d.sx) / 2)));
      state.bright = Math.round(Math.max(20, Math.min(300, d.b - (sy - d.sy) / 2)));
    } else if (d.mode === 'create') {
      let w = ix - d.ix, h = iy - d.iy;
      if (state.roi.type === 'circle' || event.shiftKey) { const s = Math.max(Math.abs(w), Math.abs(h)); w = Math.sign(w || 1) * s; h = Math.sign(h || 1) * s; }
      state.roi = {...state.roi, x: d.ix, y: d.iy, w, h};
    } else if (d.mode === 'move') {
      state.roi = clampRoi({...d.start, x: d.start.x + ix - d.ix, y: d.start.y + iy - d.iy});
    } else if (d.mode === 'resize') {
      const s = d.start; let x1 = s.x, y1 = s.y, x2 = s.x + s.w, y2 = s.y + s.h;
      if (d.handle.includes('w')) x1 = ix;
      if (d.handle.includes('e')) x2 = ix;
      if (d.handle.includes('n')) y1 = iy;
      if (d.handle.includes('s')) y2 = iy;
      state.roi = {...s, x: x1, y: y1, w: x2 - x1, h: y2 - y1};
    }
    draw();
  });

  function endDrag() {
    if (!state.drag) return;
    if (state.roi && ['create', 'resize', 'move'].includes(state.drag.mode)) {
      state.roi = clampRoi(state.roi);
      if (state.roi.w * state.scale < 6 || state.roi.h * state.scale < 6) state.roi = null;   // 클릭만 한 경우
    }
    state.drag = null;
    updateSubmit();
    draw();
  }
  canvas.addEventListener('pointerup', endDrag);
  canvas.addEventListener('pointercancel', endDrag);
  canvas.addEventListener('wheel', event => {
    if (!state.image) return;
    event.preventDefault();
    if (state.paths.length > 1 && !event.ctrlKey) {   // MRI: 휠로 슬라이스, Ctrl+휠로 확대
      const next = Math.max(0, Math.min(state.paths.length - 1, state.slice + Math.sign(event.deltaY)));
      if (next !== state.slice) { $('sliceRange').value = next; showSlice(next); }
      return;
    }
    const [sx, sy] = pointer(event);
    zoomAt(event.deltaY < 0 ? 1.15 : 1 / 1.15, sx, sy);
  }, {passive: false});

  function selectTool(tool) {
    state.tool = tool;
    document.querySelectorAll('.tool[data-tool]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.tool === tool)));
    $('toolHint').textContent = {
      box: '영상 위를 드래그해 네모 영역을 지정하세요. 모서리를 끌면 크기가 바뀝니다.',
      circle: '영상 위를 드래그해 원 영역을 지정하세요.',
      pan: '드래그해 영상을 옮깁니다.',
      window: '좌우로 끌면 대비, 위아래로 끌면 밝기가 바뀝니다.',
    }[tool];
  }
  document.querySelectorAll('.tool[data-tool]').forEach(b => b.addEventListener('click', () => {
    if (state.roi && (b.dataset.tool === 'box' || b.dataset.tool === 'circle') && state.roi.type !== b.dataset.tool) {
      const size = Math.max(state.roi.w, state.roi.h);
      state.roi = clampRoi(b.dataset.tool === 'circle' ? {...state.roi, type: 'circle', w: size, h: size} : {...state.roi, type: 'box'});
      draw();
    }
    selectTool(b.dataset.tool);
  }));
  $('clearRoi').addEventListener('click', () => { state.roi = null; updateSubmit(); draw(); });
  $('zoomIn').addEventListener('click', () => zoomAt(1.25, ...centre()));
  $('zoomOut').addEventListener('click', () => zoomAt(1 / 1.25, ...centre()));
  $('zoomOne').addEventListener('click', () => { const [cx, cy] = centre(); zoomAt(1 / state.scale, cx, cy); });
  $('resetView').addEventListener('click', () => { state.bright = 100; state.contrast = 100; fit(); draw(); });
  $('sliceRange').addEventListener('input', event => showSlice(Number(event.target.value)));
  window.addEventListener('resize', () => { resizeCanvas(); draw(); });

  function updateSubmit() { $('submitBtn').disabled = !state.roi || !state.problem; }

  /* ---------- 제출 ---------- */
  $('submitBtn').addEventListener('click', async () => {
    if (!state.roi || !state.problem) return;
    const W = state.image.naturalWidth, H = state.image.naturalHeight, roi = state.roi, p = state.problem;
    const form = new FormData();
    const blob = await imageBlob(state.slice).catch(() => null);
    if (!blob) { setStatus('영상을 다시 불러오지 못했습니다.'); return; }
    form.append('image', blob, (state.paths[state.slice] || 'problem.png').split('/').pop());
    form.append('roi_type', roi.type);
    form.append('normalized', 'true');
    if (roi.type === 'circle') {
      form.append('cx', (roi.x + roi.w / 2) / W);
      form.append('cy', (roi.y + roi.h / 2) / H);
      form.append('radius', (Math.min(roi.w, roi.h) / 2) / Math.min(W, H));
    } else {
      form.append('x', roi.x / W); form.append('y', roi.y / H);
      form.append('width', roi.w / W); form.append('height', roi.h / H);
    }
    form.append('study_idx', p.idx);
    form.append('st_part', p.st_part);
    form.append('st_modal', p.st_modal);
    if (state.paths.length > 1) { form.append('slice_index', state.slice); form.append('image_path', state.paths[state.slice]); }
    if (user?.idx) form.append('user_idx', user.idx);

    $('submitBtn').disabled = true;
    $('submitBtn').textContent = '채점 중…';
    setStatus('');
    try {
      const result = await api('/learning/roi-grade', {method: 'POST', body: form});
      if (!solved.includes(p.idx)) solved.push(p.idx);
      showResult(result, blob);
    } catch (error) {
      setStatus(error.message);
    } finally {
      $('submitBtn').textContent = '제출';
      updateSubmit();
    }
  });

  /* ---------- 결과 ---------- */
  function showSolve() { $('solveView').hidden = false; $('resultView').hidden = true; $('modeLabel').textContent = 'CASE SOLVING'; resizeCanvas(); draw(); }

  function showResult(result, blob) {
    state.lastResult = result;
    $('solveView').hidden = true;
    $('resultView').hidden = false;
    $('modeLabel').textContent = 'SCORING';
    const [cls, title, message] = RESULT_TEXT[result.result] || ['', result.result || '-', ''];
    $('resultBadge').className = 'badge ' + cls;
    $('resultBadge').textContent = title;
    // 모델이 이상 부위를 하나도 찾지 못하면 위치를 비교할 기준이 없어 오답이 된다. 이유를 함께 알려 준다.
    $('resultMessage').textContent = result.has_abnormality === false
      ? 'AI가 이 영상에서 채점 기준 영역을 찾지 못해 위치를 비교할 수 없었습니다. 정답 병명과 영상을 다시 확인해 보세요.'
      : message;

    $('resultImage').src = result.overlay_png_base64 ? 'data:image/png;base64,' + result.overlay_png_base64 : URL.createObjectURL(blob);
    $('resultFrame').querySelectorAll('.box').forEach(el => el.remove());
    addBox(result.abnormality_bbox, 'box answer');
    addBox(result.user_roi_bbox, 'box' + (state.roi?.type === 'circle' ? ' circle' : ''));

    $('sumDisease').textContent = result.expected_disease || '-';
    $('sumPlace').textContent = result.has_abnormality ? (result.overlay_summary || '표시됨') : 'AI가 찾은 이상 부위 없음';
    $('sumIou').textContent = typeof result.iou === 'number' ? Math.round(result.iou * 100) + '%' : '-';
    $('sumIouNote').textContent = typeof result.recall === 'number' ? `이상 부위를 덮은 비율 ${Math.round(result.recall * 100)}%` : '겹침(IoU)';

    const findings = (result.classification?.findings || []).slice(0, 3);
    $('findings').replaceChildren(...findings.map(f => {
      const li = document.createElement('li');
      const pct = Math.round(f.score * 100);
      li.innerHTML = '<span></span><strong></strong><div class="bar"><span></span></div>';
      li.children[0].textContent = f.label_ko || f.label;
      li.children[1].textContent = pct + '%';
      li.querySelector('.bar span').style.width = Math.min(100, pct) + '%';
      return li;
    }));
    if (!findings.length) $('findings').innerHTML = '<li>참고 소견이 없습니다.</li>';
    $('disclaimer').textContent = result.disclaimer || '';
    renderResultTerms(result);
    $('nextBtn').disabled = false;
    $('nextBtn').focus();
  }

  function addBox(bbox, className) {
    if (!bbox) return;
    const el = document.createElement('div');
    el.className = className;
    Object.assign(el.style, {left: bbox.x * 100 + '%', top: bbox.y * 100 + '%', width: bbox.width * 100 + '%', height: bbox.height * 100 + '%'});
    $('resultFrame').appendChild(el);
  }

  $('nextBtn').addEventListener('click', () => loadProblem(state.lastResult?.next || null));
  $('retryBtn').addEventListener('click', () => { showSolve(); });

  /* ---------- 의학용어 사전 ---------- */
  async function loadGlossary(problem) {
    const region = nameOf(PARTS, problem.st_part), type = nameOf(MODALS, problem.st_modal);
    if (!region) { $('termList').innerHTML = '<li class="empty">이 부위의 용어가 없습니다.</li>'; return; }
    try {
      const data = await api(`/glossary?region=${region}` + (type ? `&type=${type}` : ''));
      state.glossary = data.items || [];
    } catch (error) {
      state.glossary = [];
      $('termList').innerHTML = '';
      const li = document.createElement('li'); li.className = 'empty'; li.textContent = error.message;
      $('termList').appendChild(li);
      return;
    }
    const cats = ['전체', ...new Set(state.glossary.map(t => t.category).filter(Boolean))];
    $('termChips').replaceChildren(...cats.map(cat => {
      const b = document.createElement('button');
      b.type = 'button'; b.textContent = cat; b.setAttribute('aria-pressed', String(cat === state.chip));
      b.addEventListener('click', () => { state.chip = cat; $('termChips').querySelectorAll('button').forEach(x => x.setAttribute('aria-pressed', String(x === b))); renderTerms(); });
      return b;
    }));
    if (!cats.includes(state.chip)) state.chip = '전체';
    renderTerms();
  }

  function renderTerms(highlight = []) {
    const q = $('termSearch').value.trim().toLowerCase();
    const items = state.glossary.filter(t => (state.chip === '전체' || t.category === state.chip)
      && (!q || [t.name_ko, t.name_en, t.definition_ko].some(v => (v || '').toLowerCase().includes(q))));
    if (!items.length) { $('termList').innerHTML = '<li class="empty">맞는 용어가 없습니다.</li>'; return; }
    $('termList').replaceChildren(...items.map(t => {
      const li = document.createElement('li');
      li.className = 'term' + (highlight.includes(t.id) ? ' highlight open' : '');
      li.innerHTML = '<button type="button" aria-expanded="false"><span class="en"></span><span class="ko"></span></button><div class="def"><p></p><div class="tags"></div></div>';
      li.querySelector('.en').textContent = t.name_en || t.name_ko;
      li.querySelector('.ko').textContent = t.name_ko || '';
      li.querySelector('.def p').textContent = t.definition_ko || '';
      li.querySelector('.tags').replaceChildren(...String(t.tags_ko || '').split(/[,|]/).map(s => s.trim()).filter(Boolean).map(s => {
        const span = document.createElement('span'); span.textContent = s; return span;
      }));
      const button = li.querySelector('button');
      button.setAttribute('aria-expanded', String(li.classList.contains('open')));
      button.addEventListener('click', () => { li.classList.toggle('open'); button.setAttribute('aria-expanded', String(li.classList.contains('open'))); });
      return li;
    }));
  }
  $('termSearch').addEventListener('input', () => renderTerms());

  function renderResultTerms(result) {
    // 정답 병명·AI 소견 이름이 들어간 용어를 '핵심 의학용어'로 보여 준다
    const words = [result.expected_disease, ...(result.classification?.findings || []).slice(0, 3).map(f => f.label_ko)]
      .filter(Boolean).map(w => w.replace(/\s+/g, ''));
    const hits = state.glossary.filter(t => words.some(w => {
      const ko = (t.name_ko || '').replace(/\s+/g, '');
      return ko && (w.includes(ko) || ko.includes(w));
    })).slice(0, 6);
    if (!hits.length) { $('resultTerms').innerHTML = '<span class="note">연결된 용어가 없습니다.</span>'; return; }
    $('resultTerms').replaceChildren(...hits.map(t => {
      const b = document.createElement('button');
      b.type = 'button';
      b.textContent = t.name_en ? `${t.name_ko}(${t.name_en})` : t.name_ko;
      b.addEventListener('click', () => { $('termSearch').value = ''; state.chip = '전체'; renderTerms([t.id]); document.querySelector('.term.highlight')?.scrollIntoView({block: 'nearest'}); });
      return b;
    }));
  }

  /* ---------- 시작 ---------- */
  selectTool('box');
  resizeCanvas();
  loadProblem(null);
})();
