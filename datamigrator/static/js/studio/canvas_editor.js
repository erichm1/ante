/* CanvasEditor — the shared base for the three editors: a pannable/zoomable
   surface with its own jsPlumb instance, drop handling for palette/explorer
   drags, and per-document node positions kept in localStorage.

   Zoom model: the surface is a 0×0 origin element transformed with
   translate(pan) scale(zoom); nodes are absolutely positioned inside it in
   unscaled "canvas" coordinates. jsPlumb computes connector geometry from
   layout offsets (which ignore transforms), and we tell it the zoom via
   setZoom() so dragging a node still tracks the mouse 1:1. The grid backdrop
   lives on the viewport and follows pan/zoom, so the canvas feels infinite.

   Controls: scroll pans, Ctrl/⌘ + scroll (or a trackpad pinch) zooms, dragging empty space
   pans. A `?` button in the corner opens a card listing them — plus whatever the specific
   canvas adds (each editor supplies helpSections()). */
(function () {
  'use strict';
  const S = window.Studio;
  const h = S.h;

  const ZOOM_MIN = 0.2;
  const ZOOM_MAX = 2;
  const GRID = 24;
  const FIT_PADDING = 48;

  S.CanvasEditor = class extends S.Editor {
    constructor(id) {
      super(id);
      this.canvas = this;           // lets the toolbar read .zoom
      this.zoom = 1;
      this.panX = 0;
      this.panY = 0;
      this.jsp = null;
      this.needsRender = false;
      this.hasFitted = false;
      this.lastNodeCount = -1;
      this.dataReady = false;       // set by the subclass's load() once render() has what it needs
      this.acceptMimes = [];        // drag payload types this canvas accepts

      this.viewport = h('div', { class: 'st-viewport' });
      this.surface = h('div', { class: 'st-surface' });
      this.viewport.append(this.surface);
      this.pane.append(this.viewport);
      this.bindViewport();
      this.buildHelp();
      this.applyTransform();
    }

    // ── Subclass contract ────────────────────────────────────────────────
    render() {}                                  // rebuild every node/connection from current data
    onDrop(mime, payload, x, y) {}               // x,y in canvas coordinates

    // ── Rendering ────────────────────────────────────────────────────────
    isVisible() { return this.pane.classList.contains('active'); }

    // (Re)build the canvas now if the pane is on screen — jsPlumb measures
    // real layout, which is all zeros in a display:none pane — otherwise defer
    // until the tab is shown.
    rerender() {
      if (!this.dataReady) return;
      if (!this.isVisible()) { this.needsRender = true; return; }
      this.needsRender = false;
      this.resetCanvas();
      this.render();
      // Refit on first draw and whenever nodes are added/removed — but not on every
      // redraw, so editing a wire or a poll tick never yanks the view around.
      const count = this.surface.querySelectorAll(':scope > .st-node').length;
      if (!this.hasFitted || count !== this.lastNodeCount) { this.hasFitted = true; requestAnimationFrame(() => this.fit()); }
      this.lastNodeCount = count;
    }

    onShow() {
      if (this.needsRender || !this.jsp) this.rerender();
      else this.jsp.repaintEverything();
      S.syncToolbar();
    }

    resetCanvas() {
      if (this.jsp) this.jsp.reset();
      this.surface.replaceChildren();
      this.jsp = jsPlumb.getInstance({
        Container: this.surface,
        Connector: ['Bezier', { curviness: 60 }],
        ConnectionsDetachable: false,   // removal goes through the UI so it always hits the API too
      });
      this.jsp.setZoom(this.zoom);
    }

    destroy() { if (this.jsp) this.jsp.reset(); }

    // ── Pan / zoom ───────────────────────────────────────────────────────
    applyTransform() {
      this.surface.style.transform = `translate(${this.panX}px, ${this.panY}px) scale(${this.zoom})`;
      const cell = GRID * this.zoom;
      this.viewport.style.backgroundSize = `${cell}px ${cell}px`;
      this.viewport.style.backgroundPosition = `${this.panX}px ${this.panY}px`;
      if (this.jsp) this.jsp.setZoom(this.zoom);
      if (S.active === this) S.syncToolbar();
    }

    // Zoom keeping the canvas point under (cx, cy) — viewport pixels — fixed.
    zoomAt(factor, cx, cy) {
      const next = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, this.zoom * factor));
      const k = next / this.zoom;
      this.panX = cx - (cx - this.panX) * k;
      this.panY = cy - (cy - this.panY) * k;
      this.zoom = next;
      this.applyTransform();
    }
    zoomBy(factor) { this.zoomAt(factor, this.viewport.clientWidth / 2, this.viewport.clientHeight / 2); }

    fit() {
      const nodes = [...this.surface.querySelectorAll(':scope > .st-node')];
      if (!nodes.length || !this.viewport.clientWidth) { this.zoom = 1; this.panX = 40; this.panY = 40; this.applyTransform(); return; }
      let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
      nodes.forEach(n => {
        x0 = Math.min(x0, n.offsetLeft); y0 = Math.min(y0, n.offsetTop);
        x1 = Math.max(x1, n.offsetLeft + n.offsetWidth); y1 = Math.max(y1, n.offsetTop + n.offsetHeight);
      });
      const w = x1 - x0 + FIT_PADDING * 2;
      const hgt = y1 - y0 + FIT_PADDING * 2;
      const vw = this.viewport.clientWidth;
      const vh = this.viewport.clientHeight;
      this.zoom = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Math.min(vw / w, vh / hgt, 1)));
      // Centre the content box in the viewport.
      this.panX = (vw - (x1 - x0) * this.zoom) / 2 - x0 * this.zoom;
      this.panY = (vh - (y1 - y0) * this.zoom) / 2 - y0 * this.zoom;
      this.applyTransform();
    }

    toCanvas(clientX, clientY) {
      const r = this.viewport.getBoundingClientRect();
      return { x: (clientX - r.left - this.panX) / this.zoom, y: (clientY - r.top - this.panY) / this.zoom };
    }

    bindViewport() {
      const vp = this.viewport;

      // Scroll pans; Ctrl/⌘ + scroll zooms (a trackpad pinch arrives as a ctrl+wheel too);
      // Shift + scroll pans sideways. Firefox reports wheel "lines" rather than pixels.
      vp.addEventListener('wheel', e => {
        e.preventDefault();
        const px = v => (e.deltaMode === 1 ? v * 16 : v);
        const dx = px(e.deltaX);
        const dy = px(e.deltaY);
        if (e.ctrlKey || e.metaKey) {
          const r = vp.getBoundingClientRect();
          this.zoomAt(Math.exp(-dy * 0.0015), e.clientX - r.left, e.clientY - r.top);
        } else {
          this.panX -= e.shiftKey && !dx ? dy : dx;
          this.panY -= e.shiftKey ? 0 : dy;
          this.applyTransform();
        }
      }, { passive: false });

      // Drag empty space to pan (nodes and endpoints handle their own drags).
      vp.addEventListener('mousedown', e => {
        if (e.button !== 0 || (e.target !== vp && e.target !== this.surface)) return;
        const start = { x: e.clientX, y: e.clientY, px: this.panX, py: this.panY };
        vp.classList.add('panning');
        const move = ev => { this.panX = start.px + ev.clientX - start.x; this.panY = start.py + ev.clientY - start.y; this.applyTransform(); };
        const up = () => { vp.classList.remove('panning'); removeEventListener('mousemove', move); removeEventListener('mouseup', up); };
        addEventListener('mousemove', move);
        addEventListener('mouseup', up);
      });

      // Palette / explorer drops.
      const accepts = e => this.acceptMimes.some(m => e.dataTransfer.types.includes(m));
      vp.addEventListener('dragover', e => { if (accepts(e)) { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; vp.classList.add('drop-hover'); } });
      vp.addEventListener('dragleave', e => { if (e.target === vp) vp.classList.remove('drop-hover'); });
      vp.addEventListener('drop', e => {
        vp.classList.remove('drop-hover');
        const mime = this.acceptMimes.find(m => e.dataTransfer.types.includes(m));
        if (!mime) return;
        e.preventDefault();
        let payload = null;
        try { payload = JSON.parse(e.dataTransfer.getData(mime)); } catch (err) { return; }
        const pos = this.toCanvas(e.clientX, e.clientY);
        this.onDrop(mime, payload, pos.x, pos.y);
      });
    }

    // ── Node positions (chains/plans; mapping entities persist server-side) ──
    posKey() { return `ante_studio_pos:${this.key}`; }
    loadPositions() { return S.store.get(this.posKey(), {}); }
    savePosition(nodeId, x, y) {
      const all = this.loadPositions();
      all[nodeId] = [Math.round(x), Math.round(y)];
      S.store.set(this.posKey(), all);
    }
    forgetPositions() { try { localStorage.removeItem(this.posKey()); } catch (e) { /* ignore */ } }

    // Places `el` on the surface at (x, y) and makes it draggable.
    placeNode(el, x, y, { onMoved, onDrag, filter } = {}) {
      el.style.left = x + 'px';
      el.style.top = y + 'px';
      this.surface.append(el);
      this.jsp.draggable(el, {
        grid: [12, 12],
        filter: filter || '.st-node-tools, .st-node-tools *',
        drag: p => { if (onDrag) onDrag(p.pos[0], p.pos[1]); },
        stop: p => { if (onMoved) onMoved(p.pos[0], p.pos[1]); },
      });
      return el;
    }

    // ── "How do I use this?" ─────────────────────────────────────────────
    // Subclasses return extra sections: [{ title, items: [[keys[], description], …] }].
    helpSections() { return []; }

    buildHelp() {
      const mod = /Mac|iPhone|iPad/.test(navigator.platform) ? '⌘' : 'Ctrl';
      const navigate = { title: 'Move around', items: [
        [['Scroll'], 'pan the canvas (with Shift: sideways)'],
        [[mod, 'Scroll'], 'zoom in and out — a trackpad pinch works too'],
        [['Drag'], 'empty space to pan'],
        [['−', '+', '⤢'], 'toolbar buttons: zoom, and fit everything on screen'],
        [['Drag'], 'a block to move it — where you leave it is remembered'],
        [['Right-click'], 'a block for its menu'],
      ] };
      const card = h('div', { class: 'st-help-card', role: 'dialog', 'aria-label': 'How to use this canvas', hidden: true });
      const btn = h('button', { type: 'button', class: 'st-help-btn', title: 'How to use this canvas', 'aria-expanded': 'false' }, h('i', { class: 'bi-question-lg' }));
      const seen = S.store.get('ante_studio_help_seen', false);
      if (!seen) btn.classList.add('attention');          // a quiet pulse until it has been opened once

      const fill = () => card.replaceChildren(
        h('div', { class: 'st-help-head' }, h('b', { text: 'Using this canvas' }),
          h('button', { type: 'button', class: 'st-x', title: 'Close', html: '&times;', onclick: () => toggle(false) })),
        ...[...this.helpSections(), navigate].map(sec => h('div', { class: 'st-help-sec' },
          h('div', { class: 'st-help-title', text: sec.title }),
          ...sec.items.map(([keys, text]) => h('div', { class: 'st-help-row' },
            h('span', { class: 'keys' }, keys.map(k => h('kbd', { text: k }))), h('span', { class: 'what', text }))))));
      const toggle = open => {
        const show = open === undefined ? card.hidden : open;
        if (show) fill();                                  // rebuilt each time: some help depends on the plan's state
        card.hidden = !show;
        btn.setAttribute('aria-expanded', String(show));
        if (show) { btn.classList.remove('attention'); S.store.set('ante_studio_help_seen', true); }
      };
      btn.addEventListener('click', e => { e.stopPropagation(); toggle(); });
      // Interacting with the canvas dismisses the card, so it never sits on top of the work.
      this.viewport.addEventListener('mousedown', () => { if (!card.hidden) toggle(false); });
      this.helpButton = btn;
      this.helpCard = card;
      this.pane.append(btn, card);
    }

    // Sets the run-status decoration (green tick / red cross / spinner) on a node
    // that contains a `.st-node-state` badge. status: success|failed|error|running|null.
    setNodeStatus(node, status) {
      if (!node) return;
      if (status) node.dataset.status = status; else delete node.dataset.status;
      const icon = node.querySelector('.st-node-state i');
      if (icon) icon.className = { success: 'bi-check-lg', error: 'bi-x-lg', failed: 'bi-x-lg', warn: 'bi-exclamation-lg', cancelled: 'bi-slash-lg', running: 'bi-arrow-repeat' }[status] || '';
    }

    // A small floating note in the corner of the canvas.
    banner(...kids) {
      this.viewport.querySelector('.st-banner')?.remove();
      if (kids.length) this.viewport.append(h('div', { class: 'st-banner' }, kids));
    }
    emptyHint(text) {
      this.viewport.querySelector('.st-canvas-empty')?.remove();
      if (text) this.viewport.append(h('div', { class: 'st-canvas-empty', html: text }));
    }
  };

  // Toolbar zoom buttons act on whichever canvas is active.
  S.zoomActive = fn => { if (S.active && S.active.canvas) fn(S.active.canvas); };
})();
