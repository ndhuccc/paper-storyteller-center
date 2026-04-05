/* ═══════════════════════════════════════════════
   app.js — Alpine.js Application State
   Paper Storyteller Center
   ═══════════════════════════════════════════════ */

function App() {
  return {
    /* ── navigation ── */
    tab: 'search',

    /* ── global ── */
    globalLoading: false,
    toasts: [],
    _toastId: 0,

    /* ── sidebar ── */
    allPapers: [],
    rebuilding: false,
    deletePaperId: '',
    deleteConfirmed: false,
    deleting: false,
    renamePaperId: '',
    renameNewName: '',
    renaming: false,

    /* ── modal ── */
    modalOpen: false,
    modalTitle: '',
    modalSrc: '',
    modalDownloadUrl: '',

    /* ── search ── */
    searchQuery: '',
    searching: false,
    searchResults: [],
    searchDone: false,
    searchError: '',
    selectedPapers: {},          // { paper_id: paper }

    /* ── Q&A ── */
    qaQuestion: '',
    answering: false,
    qaHistory: [],

    /* ── generation ── */
    styles: [],
    genStyle: 'storyteller',
    styleParamDefs: {},      // { styleKey: [{key, label, min, max, step, default}] }
    genStyleParams: {},      // { paramKey: currentValue }
    genPdfPath: '',
    genAutoIndex: true,
    selectedFile: null,
    dropActive: false,
    submitting: false,
    jobs: [],
    jobsLoading: false,

    /* ── manual units input ── */
    inputMode: 'pdf',        // 'pdf' | 'manual'
    manualPaperTitle: '',
    manualUnits: [],         // [{id, title, body}]
    showBatchInput: false,
    batchInputText: '',
    _nextUnitId: 0,

    /* ════════ LIFECYCLE ════════ */
    async init() {
      await Promise.all([this.loadPapers(), this.loadStyles()]);
      this.loadJobs();
      // poll jobs every 8s
      setInterval(() => { if (this.tab === 'generate') this.loadJobs(); }, 8000);
    },

    /* ════════ PAPERS ════════ */
    async loadPapers() {
      try {
        const data = await api.get('/api/papers');
        this.allPapers = data.data || [];
      } catch (e) {
        this.toast('error', '載入論文列表失敗: ' + e.message);
      }
    },

    statusBadge(paper) {
      const s = paper?.paper_status || 'unavailable';
      const map = { ready: '✅', generated_not_indexed: '🟡', index_only: '🟠', unavailable: '⚪' };
      return map[s] || '⚪';
    },

    /* ════════ INDEX ════════ */
    async rebuildIndex() {
      this.rebuilding = true;
      try {
        await api.post('/api/index/rebuild');
        this.toast('success', '✅ 索引重建完成');
        await this.loadPapers();
      } catch (e) {
        this.toast('error', '重建失敗: ' + e.message);
      } finally {
        this.rebuilding = false;
      }
    },

    /* ════════ DELETE ════════ */
    async deletePaper() {
      if (!this.deletePaperId || !this.deleteConfirmed) return;
      this.deleting = true;
      try {
        const data = await api.del('/api/papers/' + encodeURIComponent(this.deletePaperId));
        this.toast('success', data.data?.message || '刪除完成');
        // cleanup state
        delete this.selectedPapers[this.deletePaperId];
        this.deletePaperId = '';
        this.deleteConfirmed = false;
        await this.loadPapers();
      } catch (e) {
        this.toast('error', '刪除失敗: ' + e.message);
      } finally {
        this.deleting = false;
      }
    },

    /* ════════ RENAME ════════ */
    async renamePaper() {
      if (!this.renamePaperId || !this.renameNewName.trim()) return;
      this.renaming = true;
      try {
        const data = await api.patch(
          '/api/papers/' + encodeURIComponent(this.renamePaperId) + '/rename',
          { new_name: this.renameNewName.trim() }
        );
        this.toast('success', data.data?.message || '重新命名完成，正在重建索引…');
        // update selectedPapers if this paper was selected
        if (this.renamePaperId in this.selectedPapers) {
          delete this.selectedPapers[this.renamePaperId];
        }
        this.renamePaperId = '';
        this.renameNewName = '';
        await this.loadPapers();
        await this.rebuildIndex();
      } catch (e) {
        this.toast('error', '重新命名失敗: ' + e.message);
      } finally {
        this.renaming = false;
      }
    },

    /* ════════ MODAL ════════ */
    openPaper(paper) {
      const pid = paper?.paper_id || paper?.id || '';
      if (!pid) { this.toast('warning', '找不到 paper_id'); return; }
      this.modalTitle = paper?.title || '論文閱覽';
      this.modalSrc = '/api/papers/' + encodeURIComponent(pid) + '/html';
      this.modalDownloadUrl = '/api/papers/' + encodeURIComponent(pid) + '/download';
      this.modalOpen = true;
    },

    closeModal() {
      this.modalOpen = false;
      this.modalSrc = '';
      this.modalDownloadUrl = '';
    },

    /* ════════ SEARCH ════════ */
    async doSearch() {
      const q = this.searchQuery.trim();
      if (!q) return;
      this.searching = true;
      this.searchError = '';
      this.searchDone = false;
      try {
        const data = await api.post('/api/search', { query: q, top_k: 10, threshold: 0.0 });
        this.searchResults = data.data || [];
        this.searchDone = true;
      } catch (e) {
        this.searchError = '搜尋錯誤: ' + e.message;
      } finally {
        this.searching = false;
      }
    },

    isSelected(paper) {
      const pid = paper?.paper_id || paper?.id || '';
      return pid in this.selectedPapers;
    },

    toggleSelected(paper, checked) {
      const pid = paper?.paper_id || paper?.id || '';
      if (!pid) return;
      if (checked) {
        this.selectedPapers[pid] = paper;
      } else {
        delete this.selectedPapers[pid];
      }
    },

    removeSelected(pid) {
      delete this.selectedPapers[pid];
    },

    simClass(r) {
      const dist = r._distance;
      if (dist == null) return '';
      const sim = 1 - dist;
      if (sim >= 0.5) return 'sim-high';
      if (sim >= 0.25) return 'sim-mid';
      return 'sim-low';
    },

    simText(r) {
      const dist = r._distance;
      if (dist == null) return '';
      return `📌 ${(1 - dist).toFixed(2)}`;
    },

    /* ════════ Q&A ════════ */
    async submitQuestion() {
      const q = this.qaQuestion.trim();
      if (!q || this.answering) return;
      this.answering = true;
      const forcedPapers = Object.values(this.selectedPapers);
      try {
        const payload = { question: q };
        if (forcedPapers.length > 0) payload.forced_papers = forcedPapers;
        const data = await api.post('/api/answer', payload);
        const { answer, sources } = data.data;
        const answerHtml = this.renderAnswer(answer);
        this.qaHistory.push({ question: q, answer, answerHtml, sources: sources || [] });
        this.qaQuestion = '';
        this.$nextTick(() => {
          const el = document.getElementById('qa-history');
          if (el) el.scrollTop = el.scrollHeight;
          // re-render math in new content
          if (window.renderMathInElement) {
            renderMathInElement(document.getElementById('qa-history'), {
              delimiters: [
                { left: '$$', right: '$$', display: true },
                { left: '$', right: '$', display: false },
                { left: '\\(', right: '\\)', display: false },
                { left: '\\[', right: '\\]', display: true },
              ],
              throwOnError: false,
            });
          }
        });
      } catch (e) {
        this.toast('error', '問答失敗: ' + e.message);
      } finally {
        this.answering = false;
      }
    },

    renderAnswer(text) {
      if (!text) return '';
      const prepared = this._ensureListBlankLines(text);
      if (window.marked) {
        return marked.parse(prepared);
      }
      // Fallback: basic HTML escape + preformatted
      return '<pre style="white-space:pre-wrap">' +
        text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;') + '</pre>';
    },

    // marked.js (CommonMark) requires a blank line before a list block or GFM
    // table when it follows paragraph text; without it they are absorbed into
    // the paragraph and rendered as literal text with <br> separators.
    // This preprocessor inserts the missing blank line.
    _ensureListBlankLines(text) {
      const listRe = /^[ \t]*(?:[-*+]|\d+\.)[ \t]+/;
      const tableRe = /^[ \t]*\|/;
      const lines = text.split('\n');
      const out = [];
      for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        const prev = out.length ? out[out.length - 1] : '';
        const isList = listRe.test(line);
        const isTable = tableRe.test(line);
        const prevIsList = listRe.test(prev);
        const prevIsTable = tableRe.test(prev);
        if (isList || isTable) {
          if (prev.trim() && !prevIsList && !prevIsTable) {
            out.push('');
          }
        } else if (line.trim() && prevIsTable) {
          // Table blocks need a closing blank line to prevent the next
          // paragraph from being absorbed as an extra table row.
          out.push('');
        }
        out.push(line);
      }
      return out.join('\n');
    },

    citationMeta(src) {
      const parts = [];
      const citation = src.citation || {};
      const pid = citation.paper_id || src.paper_id || src.id || '';
      const chunk = citation.chunk_index ?? src.chunk_index;
      const section = citation.section || citation.section_title || src.section || '';
      const sim = citation.similarity;
      if (pid) parts.push(`paper_id=${pid}`);
      if (chunk != null && typeof chunk !== 'boolean') parts.push(`chunk=${chunk}`);
      if (section) parts.push(`section=${section.slice(0, 48)}`);
      if (typeof sim === 'number') parts.push(`sim=${sim.toFixed(2)}`);
      return parts.join(' | ') || '-';
    },

    clearQA() {
      this.qaHistory = [];
    },

    /* ════════ GENERATION ════════ */
    async loadStyles() {
      try {
        const data = await api.get('/api/styles');
        this.styles = data.data || [];
        // Build styleParamDefs lookup
        this.styleParamDefs = {};
        for (const s of this.styles) {
          if (s.params && s.params.length > 0) this.styleParamDefs[s.key] = s.params;
        }
        if (this.styles.length > 0) this.genStyle = this.styles[0].key;
        this._applyStyleParamDefaults();
      } catch (e) {
        // Fallback
        this.styles = [{ key: 'storyteller', label: '說書人' }];
      }
    },

    _applyStyleParamDefaults() {
      const defs = this.styleParamDefs[this.genStyle] || [];
      const params = {};
      for (const d of defs) params[d.key] = d.default;
      this.genStyleParams = params;
    },

    onStyleChange() {
      this._applyStyleParamDefaults();
    },

    resetStyleParams() {
      this._applyStyleParamDefaults();
      this.toast('info', '已還原為建議值');
    },

    getOrderedStyleParams() {
      const defs = this.styleParamDefs[this.genStyle] || [];
      if (this.genStyle !== 'blog') return defs;

      const order = {
        affinity: 1,
        hook: 2,
        tech_density: 3,
        stance: 4,
        humor: 5,
      };

      return [...defs].sort((a, b) => {
        const oa = order[a.key] || 999;
        const ob = order[b.key] || 999;
        return oa - ob;
      });
    },

    getStyleParamHint(paramKey) {
      const byStyle = {
        storyteller: {
          warmth: '敘事溫度，越高越有陪伴感與引導語氣。',
          visual: '畫面感強度，越高越常用情境與意象幫助理解。',
          math_density: '公式與數學細節比例，越高越偏技術推導。',
          humor: '詼諧感強度，越高語氣越輕鬆活潑。',
        },
        blog: {
          affinity: '語氣親近度，越高越像在和讀者對話。',
          hook: '開場吸引力，越高越重視抓住注意力。',
          tech_density: '技術細節比例，越高越偏向專業內容。',
          stance: '觀點鮮明程度，越高越會提出清楚立場。',
          humor: '幽默感強度，越高語氣越輕鬆。',
        },
      };
      const map = byStyle[this.genStyle] || {};
      return map[paramKey] || '';
    },

    getStyleParamScaleHint(paramKey, side) {
      const byStyle = {
        storyteller: {
          warmth: { min: '理性', max: '溫暖' },
          visual: { min: '抽象', max: '具象' },
          math_density: { min: '淺白', max: '推導' },
          humor: { min: '嚴謹', max: '活潑' },
        },
        blog: {
          affinity: { min: '冷硬', max: '親切' },
          hook: { min: '平實', max: '吸睛' },
          tech_density: { min: '淺白', max: '專業' },
          stance: { min: '中性', max: '鮮明' },
          humor: { min: '嚴肅', max: '輕鬆' },
        },
      };
      const map = byStyle[this.genStyle] || {};
      return (map[paramKey] && map[paramKey][side]) || '';
    },

    onFileSelect(event) {
      const file = event.target.files[0];
      if (file) this.selectedFile = file;
    },

    onDrop(event) {
      this.dropActive = false;
      const file = event.dataTransfer.files[0];
      if (file && file.name.toLowerCase().endsWith('.pdf')) {
        this.selectedFile = file;
      } else {
        this.toast('warning', '請選擇 PDF 檔案');
      }
    },

    clearFile() {
      this.selectedFile = null;
      const input = document.getElementById('pdf-file-input');
      if (input) input.value = '';
    },

    /* ════════ MANUAL UNITS ════════ */
    addManualUnit() {
      this.manualUnits.push({ id: ++this._nextUnitId, title: '', body: '' });
    },

    removeManualUnit(id) {
      this.manualUnits = this.manualUnits.filter(u => u.id !== id);
    },

    moveUnitUp(id) {
      const i = this.manualUnits.findIndex(u => u.id === id);
      if (i > 0) {
        const tmp = this.manualUnits[i - 1];
        this.manualUnits[i - 1] = this.manualUnits[i];
        this.manualUnits[i] = tmp;
        this.manualUnits = [...this.manualUnits];
      }
    },

    moveUnitDown(id) {
      const i = this.manualUnits.findIndex(u => u.id === id);
      if (i >= 0 && i < this.manualUnits.length - 1) {
        const tmp = this.manualUnits[i + 1];
        this.manualUnits[i + 1] = this.manualUnits[i];
        this.manualUnits[i] = tmp;
        this.manualUnits = [...this.manualUnits];
      }
    },

    applyBatchInput() {
      const text = this.batchInputText.trim();
      if (!text) return;
      // Split by a line containing only 3+ = or - characters
      const blocks = text.split(/\n[ \t]*={3,}[ \t]*\n|\n[ \t]*-{3,}[ \t]*\n/)
        .map(b => b.trim()).filter(Boolean);
      let added = 0;
      for (const block of blocks) {
        const lines = block.split('\n');
        const firstLine = lines[0].replace(/^#+\s*/, '').trim();
        const body = lines.slice(1).join('\n').trim();
        if (body) {
          this.manualUnits.push({ id: ++this._nextUnitId, title: firstLine, body });
          added++;
        }
      }
      this.batchInputText = '';
      this.showBatchInput = false;
      if (added > 0) this.toast('success', `已匯入 ${added} 個改寫單元`);
      else this.toast('warning', '未解析出有效單元（請確認格式：標題行 + 內文，用 === 分隔多單元）');
    },

    clearManualUnits() {
      this.manualUnits = [];
      this.manualPaperTitle = '';
      this.batchInputText = '';
      this.showBatchInput = false;
    },

    async submitGenJob() {
      if (this.inputMode === 'manual') {
        // ── Manual units mode ────────────────────────────────────────────────
        const validUnits = this.manualUnits.filter(u => u.body && u.body.trim());
        if (validUnits.length === 0) {
          this.toast('warning', '請至少新增一個含有內文的改寫單元');
          return;
        }
        this.submitting = true;
        try {
          const payload = {
            manual_sections: validUnits.map(u => ({
              title: (u.title || '').trim() || '未命名單元',
              body: u.body.trim(),
            })),
            paper_title: this.manualPaperTitle.trim() || '手動輸入論文',
            style: this.genStyle,
            auto_index: this.genAutoIndex,
            style_params: this.genStyleParams,
          };
          const data = await api.post('/api/jobs/submit', payload);
          const jobId = data.data?.job_id || '';
          this.toast('success', `✅ 任務已提交：${jobId.slice(0,8)}（背景執行中）`);
          await this.loadJobs();
        } catch (e) {
          this.toast('error', '提交失敗: ' + e.message);
        } finally {
          this.submitting = false;
        }
        return;
      }

      // ── PDF upload mode ────────────────────────────────────────────────────
      if (!this.selectedFile) {
        this.toast('warning', '請先上傳 PDF 檔案');
        return;
      }
      this.submitting = true;
      try {
        const form = new FormData();
        form.append('pdf', this.selectedFile);
        form.append('style', this.genStyle);
        form.append('auto_index', this.genAutoIndex ? 'true' : 'false');
        if (Object.keys(this.genStyleParams).length > 0) {
          form.append('style_params', JSON.stringify(this.genStyleParams));
        }
        const data = await api.postForm('/api/jobs/submit', form);
        const jobId = data.data?.job_id || '';
        this.toast('success', `✅ 任務已提交：${jobId.slice(0,8)}（背景執行中）`);
        this.clearFile();
        await this.loadJobs();
      } catch (e) {
        this.toast('error', '提交失敗: ' + e.message);
      } finally {
        this.submitting = false;
      }
    },

    async loadJobs() {
      this.jobsLoading = true;
      try {
        const previousStatuses = {};
        for (const j of this.jobs) {
          if (j && j.job_id) previousStatuses[j.job_id] = j.status;
        }
        const data = await api.get('/api/jobs?limit=8');
        this.jobs = data.data || [];

        // Notify once when a tracked in-flight job turns into succeeded.
        for (const j of this.jobs) {
          const prev = previousStatuses[j.job_id];
          if (!prev || prev === j.status) continue;
          if (!['pending', 'running'].includes(prev)) continue;
          if (j.status !== 'succeeded') continue;

          const outputName = (j.output_filename || '').trim()
            || ((j.output_path || '').split('/').filter(Boolean).pop() || '').trim()
            || j.job_id.slice(0, 8);
          this.toast('success', `✅ 任務完成：${outputName}`);
        }

        // If any job transitions into a terminal state, refresh sidebar papers
        // so newly generated HTML appears without a full page reload.
        const terminalStates = new Set(['succeeded', 'failed', 'canceled']);
        const transitionedToTerminal = this.jobs.some((j) => {
          const prev = previousStatuses[j.job_id];
          return Boolean(prev) && prev !== j.status && terminalStates.has(j.status);
        });
        if (transitionedToTerminal) {
          await this.loadPapers();
        }
      } catch (e) {
        this.toast('error', '載入任務列表失敗: ' + e.message);
      } finally {
        this.jobsLoading = false;
      }
    },

    async loadJobDetail(jobId) {
      try {
        const data = await api.get('/api/jobs/' + encodeURIComponent(jobId));
        return data.data;
      } catch (e) {
        this.toast('error', '載入任務詳情失敗');
        return null;
      }
    },

    async retryJob(jobId) {
      try {
        await api.post('/api/jobs/' + encodeURIComponent(jobId) + '/retry');
        this.toast('success', '已送出重試，任務將於背景執行。');
        await this.loadJobs();
      } catch (e) {
        this.toast('error', '重試失敗: ' + e.message);
      }
    },

    async cancelJob(jobId) {
      try {
        await api.post('/api/jobs/' + encodeURIComponent(jobId) + '/cancel');
        this.toast('success', '任務已取消');
        await this.loadJobs();
      } catch (e) {
        this.toast('error', '取消失敗: ' + e.message);
      }
    },

    jobStatusSummary() {
      const counts = {};
      for (const j of this.jobs) {
        counts[j.status] = (counts[j.status] || 0) + 1;
      }
      return '狀態統計：' + Object.entries(counts).map(([k, v]) => `${k}: ${v}`).join(' | ');
    },

    /* ── Handoff ── */
    handoffOpen(jobDetail) {
      const paper = jobDetail.manifest_paper;
      const pid = jobDetail.output_paper_id || (paper?.paper_id) || (paper?.id) || '';
      const title = paper?.title || jobDetail.output_filename || pid;
      if (pid) {
        this.openPaper({ paper_id: pid, id: pid, title });
      } else {
        this.toast('warning', '找不到輸出論文');
      }
    },

    handoffSearch(jobDetail) {
      const paper = jobDetail.manifest_paper;
      const title = paper?.title || jobDetail.output_filename || '';
      if (title) {
        this.searchQuery = title.replace(' - 說書人版', '').trim() || title;
        this.tab = 'search';
        this.$nextTick(() => this.doSearch());
      }
    },

    handoffAsk(jobDetail) {
      const paper = jobDetail.manifest_paper;
      if (paper) {
        const pid = paper.paper_id || paper.id || '';
        if (pid) this.selectedPapers[pid] = paper;
      }
      const title = paper?.title || jobDetail.output_filename || '這篇論文';
      const displayTitle = title.replace(' - 說書人版', '').trim() || title;
      this.qaQuestion = `請幫我整理《${displayTitle}》的核心貢獻、方法、實驗結果與限制。`;
      this.tab = 'qa';
    },

    /* ════════ TOAST ════════ */
    toast(type, msg) {
      const id = ++this._toastId;
      this.toasts.push({ id, type, msg, visible: true });
      setTimeout(() => this.removeToast(id), 4000);
    },

    removeToast(id) {
      this.toasts = this.toasts.filter(t => t.id !== id);
    },
  };
}

/* ═══════════════════════════════════════════════
   api — lightweight fetch wrapper
   ═══════════════════════════════════════════════ */
const api = {
  async _request(method, url, body, isForm) {
    const opts = { method, cache: 'no-store' };
    if (body !== undefined) {
      if (isForm) {
        opts.body = body;
      } else {
        opts.headers = { 'Content-Type': 'application/json' };
        opts.body = JSON.stringify(body);
      }
    }
    const res = await fetch(url, opts);
    const json = await res.json();
    if (!res.ok || !json.ok) {
      throw new Error(json.error || `HTTP ${res.status}`);
    }
    return json;
  },

  get(url) { return this._request('GET', url); },
  post(url, body) { return this._request('POST', url, body); },
  postForm(url, form) { return this._request('POST', url, form, true); },
  patch(url, body) { return this._request('PATCH', url, body); },
  del(url) { return this._request('DELETE', url); },
};
