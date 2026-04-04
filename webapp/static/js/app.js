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

    /* ── modal ── */
    modalOpen: false,
    modalTitle: '',
    modalSrc: '',

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
    genPdfPath: '',
    genAutoIndex: true,
    selectedFile: null,
    dropActive: false,
    submitting: false,
    jobs: [],
    jobsLoading: false,

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

    /* ════════ MODAL ════════ */
    openPaper(paper) {
      const pid = paper?.paper_id || paper?.id || '';
      if (!pid) { this.toast('warning', '找不到 paper_id'); return; }
      this.modalTitle = paper?.title || '論文閱覽';
      this.modalSrc = '/api/papers/' + encodeURIComponent(pid) + '/html';
      this.modalOpen = true;
    },

    closeModal() {
      this.modalOpen = false;
      this.modalSrc = '';
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

    // marked.js (CommonMark) requires a blank line before a list block when it
    // follows paragraph text; without it list items are absorbed into the
    // paragraph and rendered as literal "- " / "* " text with <br> separators.
    // This preprocessor inserts the missing blank line so lists always parse
    // into proper <ul>/<ol><li> elements.
    _ensureListBlankLines(text) {
      const listRe = /^[ \t]*(?:[-*+]|\d+\.)[ \t]+/;
      const lines = text.split('\n');
      const out = [];
      for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        if (listRe.test(line)) {
          const prev = out.length ? out[out.length - 1] : '';
          if (prev.trim() && !listRe.test(prev)) {
            out.push('');
          }
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
        if (this.styles.length > 0) this.genStyle = this.styles[0].key;
      } catch (e) {
        // Fallback
        this.styles = [{ key: 'storyteller', label: '說書人' }];
      }
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

    async submitGenJob() {
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
        const data = await api.get('/api/jobs?limit=8');
        this.jobs = data.data || [];
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
    const opts = { method };
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
  del(url) { return this._request('DELETE', url); },
};
