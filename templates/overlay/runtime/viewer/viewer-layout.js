/** Resizable desktop panes for docs/overlay.md section 4; all sizes use CSSOM. */
export function installPanelResize({ workspace, details, divider, documentRef, windowRef,
                                     storage, storageKey, onResize = () => {} }) {
  const key = `${storageKey}-panel-width`;
  const minimum = 280;
  const pdfMinimum = 420; // Keep the floating PDF controls inside their pane.
  const dividerWidth = 8;
  let preferred = null;
  let drag = null;
  try {
    const value = Number(storage?.getItem(key));
    if (Number.isFinite(value) && value >= minimum && value <= 10000) preferred = value;
  } catch { /* Private browsing may deny storage; resizing still works. */ }

  function bounds() {
    const box = workspace.getBoundingClientRect?.();
    if (!box || box.width < 1000 || workspace.hidden || details.hidden) return null;
    return { box, max: box.width - pdfMinimum - dividerWidth };
  }

  function apply() {
    const limit = bounds();
    if (!limit) return;
    const initial = Math.min(600, Math.max(380, limit.box.width * 0.4));
    const width = Math.round(Math.max(minimum, Math.min(limit.max, preferred ?? initial)));
    workspace.style.setProperty('--details-width', `${width}px`);
    divider.setAttribute('aria-valuemin', String(minimum));
    divider.setAttribute('aria-valuemax', String(Math.floor(limit.max)));
    divider.setAttribute('aria-valuenow', String(width));
    divider.setAttribute('aria-valuetext', `${width} pixels wide`);
    onResize();
  }

  function remember() {
    try {
      if (preferred === null) storage?.removeItem(key);
      else storage?.setItem(key, String(preferred));
    } catch { /* A denied write must not interrupt the drag. */ }
  }

  function setWidth(width) {
    const limit = bounds();
    if (!limit || !Number.isFinite(width)) return;
    preferred = Math.round(Math.max(minimum, Math.min(limit.max, width)));
    apply();
  }

  function finish(event) {
    if (!drag || (event?.pointerId !== undefined && event.pointerId !== drag.id)) return;
    const id = drag.id;
    drag = null;
    workspace.classList.remove('is-resizing');
    try { divider.releasePointerCapture?.(id); } catch { /* Already cancelled. */ }
    remember();
  }

  divider.addEventListener('pointerdown', (event) => {
    if (event.button !== 0 || event.isPrimary === false || !bounds()) return;
    event.preventDefault();
    divider.focus?.({ preventScroll: true });
    drag = { id: event.pointerId, x: event.clientX, width: details.getBoundingClientRect().width };
    workspace.classList.add('is-resizing');
    try { divider.setPointerCapture?.(event.pointerId); } catch { /* Document events remain usable. */ }
  });
  documentRef.addEventListener('pointermove', (event) => {
    if (!drag || event.pointerId !== drag.id) return;
    if (!bounds() || event.buttons === 0) { finish(event); return; }
    event.preventDefault();
    setWidth(drag.width + drag.x - event.clientX);
  });
  documentRef.addEventListener('pointerup', finish);
  documentRef.addEventListener('pointercancel', finish);
  divider.addEventListener('lostpointercapture', finish);
  windowRef?.addEventListener?.('blur', () => finish());
  windowRef?.addEventListener?.('resize', () => { finish(); apply(); });

  divider.addEventListener('keydown', (event) => {
    if (event.altKey || event.ctrlKey || event.metaKey || event.isComposing || !bounds()) return;
    const step = event.shiftKey ? 60 : 24;
    const current = details.getBoundingClientRect().width;
    const maximum = bounds().max;
    const next = { ArrowLeft: current + step, ArrowRight: current - step,
                   Home: minimum, End: maximum }[event.key];
    if (next === undefined && event.key !== 'Enter') return;
    event.preventDefault();
    event.stopPropagation();
    if (event.key === 'Enter') { preferred = null; apply(); }
    else setWidth(next);
    remember();
  });
  divider.addEventListener('dblclick', () => {
    if (!bounds()) return;
    preferred = null;
    apply();
    remember();
  });

  apply();
  return { refresh() { finish(); apply(); } };
}
