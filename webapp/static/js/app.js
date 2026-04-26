/* ═══════════════════════════════════════════════
   app.js — Alpine.js Application State
  Paper Story Rewriting Center
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
    editDisplayPaperId: '',
    editDisplayName: '',
    editingDisplayName: false,

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
    qaEngineSlots: [],
    _qaEnginesFull: [],

    /* ── generation ── */
    styles: [],
    genStyle: 'storyteller',
    styleParamDefs: {},      // { styleKey: [{key, label, min, max, step, default}] }
    genStyleParams: {},      // { paramKey: currentValue }
    genConciseLevel: 6,
    genAntiRepeatLevel: 6,
    genGeminiPreflightEnabled: true,
    genGeminiPreflightTimeoutSeconds: 30,
    genGeminiRewriteTimeoutSeconds: 140,
    genRewriteFallbackTimeoutSeconds: 180,
    genRewriteChunkChars: 5000,
    genIntegrateSubchunks: true,
    genRewriteFormulaRetry: true,
    genEngineSlots: [],
    _genEnginesFull: [],
    genPdfPath: '',
    genAutoIndex: true,
    selectedFile: null,
    dropActive: false,
    submitting: false,
    jobs: [],
    jobsLoading: false,
    jobsPage: 1,
    jobsPerPage: 6,

    /* ── manual units input ── */
    inputMode: 'pdf',        // 'pdf' | 'manual'
    manualPaperTitle: '',
    manualUnits: [],         // [{id, title, body}]
    showBatchInput: false,
    batchInputText: '',
    batchImportUrl: '',
    importHtmlLoading: false,
    _nextUnitId: 0,
    scanning: false,         // PDF scan-preview in progress

    /* ════════ LIFECYCLE ════════ */
    async init() {
      await Promise.all([this.loadPapers(), this.loadStyles(), this.loadEngines()]);
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

    /** 側欄風格分組標題（對應 /api/styles） */
    groupStyleLabel(styleKey) {
      const k = String(styleKey || '').trim() || 'unknown';
      const found = (this.styles || []).find((item) => item.key === k);
      if (found) return found.label;
      if (k === 'unknown') return '其他 / 未標示風格';
      return k;
    },

    /** 依 rewrite_style 分組；各組內依 generated_at 降冪（新→舊） */
    get paperGroups() {
      const order = ['storyteller', 'blog', 'professor', 'fairy', 'lazy', 'question', 'log', 'unknown'];
      const map = {};
      for (const p of this.allPapers || []) {
        const k = String(p.rewrite_style || '').trim() || 'unknown';
        if (!map[k]) map[k] = [];
        map[k].push(p);
      }
      for (const key of Object.keys(map)) {
        map[key].sort((a, b) => {
          const ta = String(a.generated_at || '');
          const tb = String(b.generated_at || '');
          return tb.localeCompare(ta);
        });
      }
      const seen = new Set();
      const out = [];
      for (const k of order) {
        if (map[k] && map[k].length) {
          out.push({ style: k, label: this.groupStyleLabel(k), papers: map[k] });
          seen.add(k);
        }
      }
      for (const k of Object.keys(map).sort()) {
        if (seen.has(k) || !map[k].length) continue;
        out.push({ style: k, label: this.groupStyleLabel(k), papers: map[k] });
      }
      return out;
    },

    formatPaperTime(p) {
      const raw = String(p?.generated_at || '').trim();
      if (!raw) return '';
      const d = Date.parse(raw);
      if (Number.isNaN(d)) return raw.slice(0, 16);
      return new Date(d).toLocaleString('zh-TW', { dateStyle: 'short', timeStyle: 'short' });
    },

    /** 任務列表分頁（每頁 jobsPerPage 筆） */
    get jobsPageItems() {
      const all = this.jobs || [];
      const start = (this.jobsPage - 1) * this.jobsPerPage;
      return all.slice(start, start + this.jobsPerPage);
    },

    get jobsTotalPages() {
      const n = (this.jobs || []).length;
      return Math.max(1, Math.ceil(n / this.jobsPerPage));
    },

    clampJobsPage() {
      const tp = this.jobsTotalPages;
      if (this.jobsPage > tp) this.jobsPage = tp;
      if (this.jobsPage < 1) this.jobsPage = 1;
    },

    jobsPrevPage() {
      if (this.jobsPage > 1) this.jobsPage -= 1;
    },

    jobsNextPage() {
      if (this.jobsPage < this.jobsTotalPages) this.jobsPage += 1;
    },

    /** ISO 時間以「系統／瀏覽器所在時區」顯示（不指定 timeZone 即為本地） */
    formatLocalDateTime(iso) {
      if (iso == null || String(iso).trim() === '') return '-';
      const d = Date.parse(String(iso));
      if (Number.isNaN(d)) return String(iso);
      return new Date(d).toLocaleString('zh-TW', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false,
      });
    },

    /** 任務列狀態標章：succeeded 依改寫風格（style）套用不同底色 */
    jobStatusChipClass(job) {
      const st = job?.status || '';
      if (st !== 'succeeded') {
        return 'status-' + st;
      }
      let k = String(job?.style ?? '').trim();
      if (!k || k === '-') k = 'unknown';
      k = k.replace(/[^a-zA-Z0-9_-]/g, '') || 'unknown';
      return 'status-succeeded s-style-' + k;
    },

    /** 任務列表中欄：論文標題（進行中不顯示，由模板 x-show 控制） */
    jobListPaperTitle(job) {
      const st = String(job?.status || '').trim();
      if (st === 'pending' || st === 'running') return '';
      const t = String(job?.paper_title ?? '').trim();
      return t || '（無標題）';
    },

    /** 執行中：phase_detail 結構化摘要（列表第二行） */
    formatRunningPhaseDetail(pd) {
      if (!pd || typeof pd !== 'object') return '';
      const parts = [];
      if (pd.kind) parts.push(String(pd.kind));
      if (pd.stage) parts.push(String(pd.stage));
      if (pd.section_index != null && pd.section_total != null) {
        parts.push(`節 ${pd.section_index}/${pd.section_total}`);
      }
      if (pd.section_title) parts.push(`「${String(pd.section_title).slice(0, 48)}」`);
      if (pd.chunk_index != null && pd.chunks_total != null) {
        parts.push(`子塊 ${pd.chunk_index}/${pd.chunks_total}`);
      }
      if (pd.primary_model) parts.push(`主模型 ${pd.primary_model}`);
      if (pd.used_model) parts.push(`實際 ${pd.used_model}`);
      if (pd.after_retry) parts.push('重試後成功');
      if (pd.elapsed_seconds != null) parts.push(`本節耗時 ${pd.elapsed_seconds}s`);
      if (pd.error_snippet) {
        const one = String(pd.error_snippet).replace(/\s+/g, ' ').trim();
        parts.push(`錯誤 ${one.length > 120 ? one.slice(0, 119) + '…' : one}`);
      }
      return parts.join(' · ');
    },

    /** 完成後：各節改寫牆鐘時間（多行文字） */
    formatSectionRewriteStats(stats, totalSeconds) {
      if (!Array.isArray(stats) || stats.length === 0) return '（無節次統計）';
      const lines = stats.map((s) => {
        const idx = s.index ?? '?';
        const sec = s.elapsed_seconds != null ? `${s.elapsed_seconds}s` : '-';
        const tt = String(s.title_short || s.title || '').slice(0, 64);
        const m = s.used_model ? ` — ${s.used_model}` : '';
        const r = s.had_chunk_retry ? '（含子塊重試）' : '';
        return `§${idx} 牆鐘 ${sec}${r}${m} — ${tt}`;
      });
      let out = lines.join('\n');
      if (totalSeconds != null && !Number.isNaN(Number(totalSeconds))) {
        out += `\n改寫階段牆鐘合計：${Number(totalSeconds).toFixed(2)}s`;
      }
      return out;
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

    /* ════════ EDIT DISPLAY NAME ════════ */
    async updateDisplayName() {
      if (!this.editDisplayPaperId || !this.editDisplayName.trim()) return;
      this.editingDisplayName = true;
      try {
        const data = await api.patch(
          '/api/papers/' + encodeURIComponent(this.editDisplayPaperId) + '/display-name',
          { display_name: this.editDisplayName.trim() }
        );
        this.toast('success', data.data?.message || '顯示名稱已更新');
        if (this.editDisplayPaperId in this.selectedPapers) {
          this.selectedPapers[this.editDisplayPaperId] = {
            ...this.selectedPapers[this.editDisplayPaperId],
            title: this.editDisplayName.trim(),
          };
        }
        this.editDisplayPaperId = '';
        this.editDisplayName = '';
        await this.loadPapers();
      } catch (e) {
        this.toast('error', '更新顯示名稱失敗: ' + e.message);
      } finally {
        this.editingDisplayName = false;
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
    async loadEngines() {
      try {
        const data = await api.get('/api/engines');
        const qa = (data.data?.qa || []).map((item) => ({ ...item }));
        const gen = (data.data?.generation || []).map((item) => ({ ...item }));
        this._qaEnginesFull = qa;
        this._genEnginesFull = gen;
        this.qaEngineSlots = this._buildEngineSlotsFromFull(qa);
        this.genEngineSlots = this._buildEngineSlotsFromFull(gen);
      } catch (e) {
        this._qaEnginesFull = [];
        this._genEnginesFull = [];
        this.qaEngineSlots = [];
        this.genEngineSlots = [];
        this.toast('warning', '載入 AI 引擎列表失敗，將使用後端預設順序');
      }
    },

    _isVertexProvider(provider) {
      const p = String(provider || '').trim().toLowerCase();
      return p === 'vertex' || p === 'vertexai';
    },

    _vertexOptionListsFromFull(list) {
      const raw = (list || []).map((item) => ({ ...item }));
      const vGlobal = [];
      const vUs = [];
      const seenG = new Set();
      const seenU = new Set();
      for (const e of raw) {
        if (!e || !e.id) continue;
        if (!this._isVertexProvider(e.provider)) continue;
        const loc = String(e.vertex_location || '').trim().toLowerCase();
        if (loc === 'global' && !seenG.has(e.id)) {
          seenG.add(e.id);
          vGlobal.push({ ...e });
        } else if (loc === 'us-central1' && !seenU.has(e.id)) {
          seenU.add(e.id);
          vUs.push({ ...e });
        }
      }
      const byOrder = (a, b) => (a.default_order || 999) - (b.default_order || 999);
      vGlobal.sort(byOrder);
      vUs.sort(byOrder);
      return { vGlobal, vUs };
    },

    _makeVertexSlot(slotKey, location, regionLabel, selectedId, options) {
      const opts = (options || []).map((o) => ({ ...o }));
      const ids = new Set(opts.map((o) => o.id));
      const sid = selectedId && ids.has(selectedId) ? selectedId : '';
      return {
        kind: 'vertex',
        slotKey,
        location,
        regionLabel,
        selectedId: sid,
        options: opts,
      };
    },

    _buildEngineSlotsFromFull(full) {
      const sorted = [...(full || [])]
        .filter((e) => e && e.id)
        .sort((a, b) => (a.default_order || 999) - (b.default_order || 999));
      const { vGlobal, vUs } = this._vertexOptionListsFromFull(full);
      const slots = [];
      let hasGlobalSlot = false;
      let hasUsSlot = false;
      for (const e of sorted) {
        if (this._isVertexProvider(e.provider)) {
          const loc = String(e.vertex_location || '').trim().toLowerCase();
          if (loc === 'global' && vGlobal.length) {
            if (!hasGlobalSlot) {
              hasGlobalSlot = true;
              slots.push(this._makeVertexSlot('vertex-global', 'global', 'Vertex（global）', e.id, vGlobal));
            }
          } else if (loc === 'us-central1' && vUs.length) {
            if (!hasUsSlot) {
              hasUsSlot = true;
              slots.push(
                this._makeVertexSlot('vertex-us-central1', 'us-central1', 'Vertex（us-central1）', e.id, vUs),
              );
            }
          }
        } else {
          slots.push({ kind: 'core', ...e });
        }
      }
      if (!hasGlobalSlot && vGlobal.length) {
        slots.push(this._makeVertexSlot('vertex-global', 'global', 'Vertex（global）', '', vGlobal));
      }
      if (!hasUsSlot && vUs.length) {
        slots.push(this._makeVertexSlot('vertex-us-central1', 'us-central1', 'Vertex（us-central1）', '', vUs));
      }
      return slots;
    },

    moveEngineUp(scope, rowKey) {
      const arrKey = scope === 'qa' ? 'qaEngineSlots' : 'genEngineSlots';
      const list = [...this[arrKey]];
      const index = list.findIndex(
        (s) =>
          s &&
          ((s.kind === 'core' && s.id === rowKey) || (s.kind === 'vertex' && s.slotKey === rowKey)),
      );
      if (index <= 0) return;
      const temp = list[index - 1];
      list[index - 1] = list[index];
      list[index] = temp;
      this[arrKey] = list;
    },

    moveEngineDown(scope, rowKey) {
      const arrKey = scope === 'qa' ? 'qaEngineSlots' : 'genEngineSlots';
      const list = [...this[arrKey]];
      const index = list.findIndex(
        (s) =>
          s &&
          ((s.kind === 'core' && s.id === rowKey) || (s.kind === 'vertex' && s.slotKey === rowKey)),
      );
      if (index < 0 || index >= list.length - 1) return;
      const temp = list[index + 1];
      list[index + 1] = list[index];
      list[index] = temp;
      this[arrKey] = list;
    },

    resetEngineOrder(scope) {
      const fullKey = scope === 'qa' ? '_qaEnginesFull' : '_genEnginesFull';
      const full = [...(this[fullKey] || [])].sort(
        (a, b) => (a.default_order || 999) - (b.default_order || 999),
      );
      this[fullKey] = full;
      const slotsKey = scope === 'qa' ? 'qaEngineSlots' : 'genEngineSlots';
      this[slotsKey] = this._buildEngineSlotsFromFull(full);
    },

    getEngineOrder(scope) {
      const arrKey = scope === 'qa' ? 'qaEngineSlots' : 'genEngineSlots';
      const out = [];
      for (const s of this[arrKey] || []) {
        if (!s) continue;
        if (s.kind === 'core' && s.id) out.push(s.id);
        else if (s.kind === 'vertex' && s.selectedId) out.push(s.selectedId);
      }
      return out;
    },

    formatEngineOrder(engines) {
      if (!Array.isArray(engines) || engines.length === 0) return '-';
      return engines.map((item) => item.label || item.model || item.id || '?').join(' → ');
    },

    async submitQuestion() {
      const q = this.qaQuestion.trim();
      if (!q || this.answering) return;
      this.answering = true;
      const forcedPapers = Object.values(this.selectedPapers);
      try {
        const payload = { question: q, engine_order: this.getEngineOrder('qa') };
        if (forcedPapers.length > 0) payload.forced_papers = forcedPapers;
        const data = await api.post('/api/answer', payload);
        const { answer, sources, used_model, engine_order } = data.data;
        const answerHtml = this.renderAnswer(answer);
        this.qaHistory.push({
          question: q,
          answer,
          answerHtml,
          sources: sources || [],
          usedModel: used_model || '',
          engineOrder: engine_order || [],
        });
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
      let prepared = this._splitInlineListMarkers(text);
      prepared = this._ensureListBlankLines(prepared);
      if (window.marked) {
        return marked.parse(prepared);
      }
      // Fallback: basic HTML escape + preformatted
      return '<pre style="white-space:pre-wrap">' +
        text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;') + '</pre>';
    },

    // Split list items compressed onto one line by LLMs into separate lines.
    // Handles: '- item1 * item2'  and  '1. step1 2. step2 3. step3'
    _splitInlineListMarkers(text) {
      const listStartRe = /^([ \t]*)([-*+]|\d+\.)[ \t]+/;
      const unorderedSepRe = / (?<!\*)\*(?!\*) /g;
      // ' N. ' where period is NOT followed by a digit (avoids decimals like 2.5)
      const orderedSepRe = / \d+\.(?!\d) /g;
      const lines = text.split('\n');
      const out = [];
      for (const line of lines) {
        const m = listStartRe.exec(line);
        if (m) {
          const indent = m[1];
          const isOrdered = /^[ \t]*\d+\./.test(line);
          if (isOrdered) {
            const parts = line.split(orderedSepRe);
            if (parts.length > 1) {
              parts.forEach((part, i) => {
                out.push(i === 0 ? part : `${indent}${i + 1}. ${part.trimStart()}`);
              });
              continue;
            }
          } else {
            const parts = line.split(unorderedSepRe);
            if (parts.length > 1) {
              out.push(parts[0]);
              for (let i = 1; i < parts.length; i++) {
                out.push(`${indent}* ${parts[i]}`);
              }
              continue;
            }
          }
        }
        out.push(line);
      }
      return out.join('\n');
    },

    // marked.js (CommonMark) requires a blank line before a list block or GFM
    // table when it follows paragraph text; without it they are absorbed into
    // the paragraph and rendered as literal text with <br> separators.
    // Also inserts an HTML comment between adjacent ul/ol blocks to prevent
    // marked.js from merging them into a single list.
    _ensureListBlankLines(text) {
      const ulRe   = /^[ \t]*[-*+][ \t]+/;
      const olRe   = /^[ \t]*\d+\.[ \t]+/;
      const tableRe = /^[ \t]*\|/;
      const lines = text.split('\n');
      const out = [];
      for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        const prev = out.length ? out[out.length - 1] : '';
        const isUl   = ulRe.test(line);
        const isOl   = olRe.test(line);
        const isList  = isUl || isOl;
        const isTable = tableRe.test(line);
        const prevIsUl   = ulRe.test(prev);
        const prevIsOl   = olRe.test(prev);
        const prevIsList  = prevIsUl || prevIsOl;
        const prevIsTable = tableRe.test(prev);
        if (isList || isTable) {
          if (prev.trim() && !prevIsList && !prevIsTable) {
            // Blank line before list/table when preceded by prose
            out.push('');
          } else if (prevIsList && ((isUl && prevIsOl) || (isOl && prevIsUl))) {
            // List type switches (ul↔ol): insert invisible HTML comment so
            // marked.js treats them as separate list blocks
            out.push('');
            out.push('<!-- -->');
            out.push('');
          }
        } else if (line.trim() && prevIsTable) {
          // Closing blank line after table to prevent next paragraph being
          // absorbed as an extra table row
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

    styleLabel(styleKey) {
      const found = (this.styles || []).find((item) => item.key === styleKey);
      return found?.label || styleKey || '-';
    },

    styleParamLabel(styleKey, paramKey) {
      const defs = this.styleParamDefs[styleKey] || [];
      const found = defs.find((item) => item.key === paramKey);
      return found?.label || paramKey;
    },

    formatStyleParams(styleKey, params) {
      if (!params || typeof params !== 'object' || Array.isArray(params)) return [];
      return Object.entries(params).map(([key, value]) => ({
        key,
        label: this.styleParamLabel(styleKey, key),
        value,
      }));
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
      if (this.genStyle === 'blog') {
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
      }

      if (this.genStyle === 'professor') {
        const order = {
          formality: 1,
          structure: 2,
          beginner_friendly: 3,
          math_density: 4,
          exam_focus: 5,
        };

        return [...defs].sort((a, b) => {
          const oa = order[a.key] || 999;
          const ob = order[b.key] || 999;
          return oa - ob;
        });
      }

      if (this.genStyle === 'fairy') {
        const order = {
          fairy_tone: 1,
          fidelity: 2,
          age_level: 3,
          visual: 4,
          explicitness: 5,
        };

        return [...defs].sort((a, b) => {
          const oa = order[a.key] || 999;
          const ob = order[b.key] || 999;
          return oa - ob;
        });
      }

      if (this.genStyle === 'lazy') {
        const order = {
          bullet_count: 1,
          compression: 2,
          beginner_friendly: 3,
          visual: 4,
          takeaway_strength: 5,
        };

        return [...defs].sort((a, b) => {
          const oa = order[a.key] || 999;
          const ob = order[b.key] || 999;
          return oa - ob;
        });
      }

      if (this.genStyle === 'question') {
        const order = {
          question_count: 1,
          curiosity: 2,
          depth: 3,
          beginner_friendly: 4,
          closure_strength: 5,
        };

        return [...defs].sort((a, b) => {
          const oa = order[a.key] || 999;
          const ob = order[b.key] || 999;
          return oa - ob;
        });
      }

      return defs;
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
        professor: {
          formality: '正式語氣強度，越高越像課堂講義而非口語聊天。',
          structure: '結構整理程度，越高越強調分層、歸納與教學順序。',
          beginner_friendly: '初學者照顧程度，越高越會補定義、直覺與基礎說明。',
          math_density: '數學與公式細節比例，越高越偏向推導與技術細節。',
          exam_focus: '考點整理傾向，越高越會強調比較、限制與重點整理。',
        },
        fairy: {
          fairy_tone: '童話氛圍強度，越高越有角色、場景、魔法規則與寓言感。',
          fidelity: '知識對應嚴謹度，越高越緊貼原文核心機制與邏輯。',
          age_level: '目標年齡層定位，越高越偏向較成熟、可承載更複雜敘事。',
          visual: '畫面感強度，越高越常用具體場景與意象幫助理解。',
          explicitness: '知識說明顯性程度，越高越會明講故事對應的知識。',
        },
        lazy: {
          bullet_count: '重點條列數量，越高越完整，越低越極簡。',
          compression: '資訊壓縮程度，越高越像快速重點整理。',
          beginner_friendly: '補背景與白話解釋的程度，越高越照顧新手。',
          visual: '是否用具象比喻或圖像化描述幫助快速理解。',
          takeaway_strength: '每節重點是否被明確收束並打亮。',
        },
        question: {
          question_count: '每節用多少核心問題來帶動閱讀節奏。',
          curiosity: '問題設計的勾引力，越高越會先挑起讀者疑問。',
          depth: '回答拆解深度，越高越會一路追問到機制與限制。',
          beginner_friendly: '是否補足背景、定義與直覺解釋。',
          closure_strength: '最後是否把答案清楚收斂成可帶走的結論。',
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
        professor: {
          formality: { min: '口語', max: '正式' },
          structure: { min: '自然鋪陳', max: '高度條理' },
          beginner_friendly: { min: '預設基礎', max: '零基礎友善' },
          math_density: { min: '概念為主', max: '推導為主' },
          exam_focus: { min: '理解導向', max: '考點導向' },
        },
        fairy: {
          fairy_tone: { min: '偏寫實', max: '童話濃厚' },
          fidelity: { min: '自由改寫', max: '緊貼原意' },
          age_level: { min: '低齡', max: '青少年' },
          visual: { min: '平實', max: '很有畫面' },
          explicitness: { min: '寓意隱含', max: '明白解說' },
        },
        lazy: {
          bullet_count: { min: '精簡', max: '完整' },
          compression: { min: '保留細節', max: '高度濃縮' },
          beginner_friendly: { min: '預設基礎', max: '新手友善' },
          visual: { min: '直白', max: '比喻化' },
          takeaway_strength: { min: '自然帶過', max: '重點很明確' },
        },
        question: {
          question_count: { min: '少量提問', max: '多題引導' },
          curiosity: { min: '平鋪直述', max: '強烈提問' },
          depth: { min: '快速回答', max: '逐層深挖' },
          beginner_friendly: { min: '偏專業', max: '偏新手' },
          closure_strength: { min: '開放收束', max: '明確結論' },
        },
      };
      const map = byStyle[this.genStyle] || {};
      return (map[paramKey] && map[paramKey][side]) || '';
    },

    onFileSelect(event) {
      const file = event.target.files[0];
      if (!file) return;
      this.selectedFile = file;
      const stem = file.name.replace(/\.pdf$/i, '');
      this.manualPaperTitle = stem.replace(/[_\-]+/g, ' ').trim() || stem;
    },

    onDrop(event) {
      this.dropActive = false;
      const file = event.dataTransfer.files[0];
      if (file && file.name.toLowerCase().endsWith('.pdf')) {
        this.selectedFile = file;
        const stem = file.name.replace(/\.pdf$/i, '');
        this.manualPaperTitle = stem.replace(/[_\-]+/g, ' ').trim() || stem;
      } else {
        this.toast('warning', '請選擇 PDF 檔案');
      }
    },

    clearFile() {
      this.selectedFile = null;
      this.manualPaperTitle = '';
      const input = document.getElementById('pdf-file-input');
      if (input) input.value = '';
    },

    /* ════════ PDF SCAN PREVIEW ════════ */
    async scanPdfSections() {
      if (!this.selectedFile) {
        this.toast('warning', '請先選擇 PDF 檔案');
        return;
      }
      this.scanning = true;
      try {
        const form = new FormData();
        form.append('pdf', this.selectedFile);
        const data = await api.postForm('/api/pdf/scan', form);
        const res = data.data || {};
        const sections = res.sections || [];
        if (sections.length === 0) {
          this.toast('warning', 'PDF 掃描完成，但未偵測到任何改寫單元');
          return;
        }
        // Populate manual units and switch to manual mode
        this._nextUnitId = 0;
        this.manualUnits = sections.map(s => ({
          id: ++this._nextUnitId,
          title: s.title || '',
          body: s.body || '',
        }));
        this.manualPaperTitle = res.paper_title || this.selectedFile.name.replace(/\.pdf$/i, '');
        this.inputMode = 'manual';
        if (res.warning) this.toast('warning', `掃描提醒：${res.warning}`);
        this.toast('success', `✅ 掃描完成，已載入 ${sections.length} 個改寫單元，請確認後提交`);
      } catch (e) {
        this.toast('error', 'PDF 掃描失敗: ' + e.message);
      } finally {
        this.scanning = false;
      }
    },

    /* ════════ MANUAL UNITS ════════ */
    dragSource: null,
    dragTarget: null,

    addManualUnit() {
      this.manualUnits.push({ id: ++this._nextUnitId, title: '', body: '' });
    },

    addUnitAfter(id) {
      const i = this.manualUnits.findIndex(u => u.id === id);
      if (i >= 0) {
        this.manualUnits.splice(i + 1, 0, { id: ++this._nextUnitId, title: '', body: '' });
        this.manualUnits = [...this.manualUnits];
      }
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

    onUnitDragStart(event, id) {
      this.dragSource = id;
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', String(id));
    },

    onUnitDragOver(event, id) {
      if (this.dragSource !== id) {
        this.dragTarget = id;
        event.dataTransfer.dropEffect = 'move';
      }
    },

    onUnitDrop(event, targetId) {
      event.preventDefault();
      if (this.dragSource === null || this.dragSource === targetId) return;
      const sourceIdx = this.manualUnits.findIndex(u => u.id === this.dragSource);
      const targetIdx = this.manualUnits.findIndex(u => u.id === targetId);
      if (sourceIdx === -1 || targetIdx === -1) return;
      // Swap elements
      const tmp = this.manualUnits[sourceIdx];
      this.manualUnits[sourceIdx] = this.manualUnits[targetIdx];
      this.manualUnits[targetIdx] = tmp;
      this.manualUnits = [...this.manualUnits];
      this.dragSource = null;
      this.dragTarget = null;
    },

    onUnitDragEnd(event) {
      this.dragSource = null;
      this.dragTarget = null;
    },

    async importHtmlToBatch() {
      const url = (this.batchImportUrl || '').trim();
      if (!url) {
        this.toast('warning', '請先輸入 HTML 網址');
        return;
      }
      this.importHtmlLoading = true;
      try {
        const data = await api.post('/api/html/import', { url });
        const md = (data.data && data.data.markdown) ? String(data.data.markdown) : '';
        const pageTitle = (data.data && data.data.page_title) ? String(data.data.page_title).trim() : '';
        if (!md.trim()) {
          this.toast('warning', '轉換後內容為空，請檢查該頁面是否可讀取');
          return;
        }
        const head = pageTitle || '網頁內容';
        this.batchInputText = `${head}\n\n${md.trim()}`;
        this.toast('success', '已從網址轉成 Markdown 並填入文字框');
      } catch (e) {
        this.toast('error', '匯入失敗：' + (e && e.message ? e.message : String(e)));
      } finally {
        this.importHtmlLoading = false;
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
      this.batchImportUrl = '';
      this.showBatchInput = false;
    },

    async submitGenJob() {
      const paperTitle = (this.manualPaperTitle || '').trim();
      if (!paperTitle) {
        this.toast('warning', '請填寫論文標題（用於輸出檔名）');
        return;
      }

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
            paper_title: paperTitle,
            style: this.genStyle,
            auto_index: this.genAutoIndex,
            concise_level: this.genConciseLevel,
            anti_repeat_level: this.genAntiRepeatLevel,
            gemini_preflight_enabled: this.genGeminiPreflightEnabled,
            gemini_preflight_timeout_seconds: this.genGeminiPreflightTimeoutSeconds,
            gemini_rewrite_timeout_seconds: this.genGeminiRewriteTimeoutSeconds,
            rewrite_fallback_timeout_seconds: this.genRewriteFallbackTimeoutSeconds,
            integrate_subchunk_rewrites: this.genIntegrateSubchunks,
            rewrite_formula_retry: this.genRewriteFormulaRetry,
            style_params: this.genStyleParams,
            engine_order: this.getEngineOrder('generate'),
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
        form.append('paper_title', paperTitle);
        form.append('style', this.genStyle);
        form.append('auto_index', this.genAutoIndex ? 'true' : 'false');
        form.append('concise_level', String(this.genConciseLevel));
        form.append('anti_repeat_level', String(this.genAntiRepeatLevel));
        form.append('gemini_preflight_enabled', this.genGeminiPreflightEnabled ? 'true' : 'false');
        form.append('gemini_preflight_timeout_seconds', String(this.genGeminiPreflightTimeoutSeconds));
        form.append('gemini_rewrite_timeout_seconds', String(this.genGeminiRewriteTimeoutSeconds));
        form.append('rewrite_fallback_timeout_seconds', String(this.genRewriteFallbackTimeoutSeconds));
        form.append('integrate_subchunk_rewrites', this.genIntegrateSubchunks ? 'true' : 'false');
        form.append('rewrite_formula_retry', this.genRewriteFormulaRetry ? 'true' : 'false');
        if (Object.keys(this.genStyleParams).length > 0) {
          form.append('style_params', JSON.stringify(this.genStyleParams));
        }
        const genOrder = this.getEngineOrder('generate');
        if (genOrder.length > 0) {
          form.append('engine_order', JSON.stringify(genOrder));
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
        const data = await api.get('/api/jobs?limit=0');
        this.jobs = data.data || [];
        this.clampJobsPage();

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
