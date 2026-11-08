/** Pointer, keyboard and persistence behavior of the desktop panel divider. */
import assert from "node:assert/strict";
import test from "node:test";
import { installPanelResize } from "../runtime/viewer/viewer-layout.js";
import { fakeElement, fire } from "./fixtures.mjs";

function setup({ width = 1200, stored = null, deny = false } = {}) {
  const workspace = fakeElement(), details = fakeElement(), divider = fakeElement();
  const documentRef = fakeElement(), windowRef = fakeElement();
  let saved = stored;
  const storage = {
    getItem() { if (deny) throw Error('denied'); return saved; },
    setItem(key, value) { assert.equal(key, 'test-panel-width'); if (deny) throw Error('denied'); saved = value; },
    removeItem() { if (deny) throw Error('denied'); saved = null; },
  };
  workspace.getBoundingClientRect = () => ({ width });
  details.getBoundingClientRect = () => ({ width: parseFloat(workspace.style['--details-width']) || 480 });
  const controller = installPanelResize({ workspace, details, divider, documentRef, windowRef,
    storage, storageKey: 'test' });
  return { workspace, details, divider, documentRef, windowRef, controller,
    resize: async value => { width = value; await fire(windowRef, 'resize'); },
    saved: () => saved, width: () => details.getBoundingClientRect().width };
}
const pointer = (x, extra = {}) => ({ button: 0, pointerId: 1, clientX: x, ...extra });
const key = name => ({ key: name, stopPropagation() {} });

test('dragging in either direction resizes live and persists only at release', async () => {
  const s = setup();
  await fire(s.divider, 'pointerdown', pointer(720));
  await fire(s.documentRef, 'pointermove', pointer(600));
  assert.equal(s.width(), 600);
  assert.equal(s.saved(), null);
  assert.ok(s.workspace.classList.contains('is-resizing'));
  await fire(s.documentRef, 'pointermove', pointer(650));
  assert.equal(s.width(), 550);
  await fire(s.documentRef, 'pointerup', pointer(650));
  assert.equal(s.saved(), '550');
  assert.ok(!s.workspace.classList.contains('is-resizing'));
  assert.equal(setup({ stored: s.saved() }).width(), 550);
});

test('limits keep both panes visible and resizing the window preserves the preference', async () => {
  const s = setup({ stored: '800' });
  await s.resize(1000);
  assert.equal(s.width(), 672);
  assert.equal(s.saved(), '800');
  await s.resize(1400);
  assert.equal(s.width(), 800);
  await fire(s.divider, 'keydown', key('End'));
  assert.equal(s.width(), 1072);
  await fire(s.divider, 'keydown', key('Home'));
  assert.equal(s.width(), 280);
  await fire(s.divider, 'keydown', key('ArrowRight'));
  assert.equal(s.width(), 280);
  assert.equal(s.divider.attributes['aria-valuenow'], '280');
});

test('keyboard resizes, reports width, and both reset gestures clear the saved preference', async () => {
  const s = setup();
  await fire(s.divider, 'keydown', key('ArrowLeft'));
  assert.equal(s.width(), 504);
  await fire(s.divider, 'keydown', { ...key('ArrowRight'), shiftKey: true });
  assert.equal(s.width(), 444);
  assert.equal(s.divider.attributes['aria-valuetext'], '444 pixels wide');
  await fire(s.divider, 'keydown', key('Enter'));
  assert.equal(s.width(), 480);
  assert.equal(s.saved(), null);
  await fire(s.divider, 'keydown', key('End'));
  await fire(s.divider, 'dblclick');
  assert.equal(s.width(), 480);
  assert.equal(s.saved(), null);
});

test('cancel, lost capture and window blur finish a drag; unrelated pointers cannot move it', async () => {
  for (const ending of ['pointercancel', 'lostpointercapture', 'blur']) {
    const s = setup();
    await fire(s.divider, 'pointerdown', pointer(720));
    await fire(s.documentRef, 'pointermove', pointer(400, { pointerId: 2 }));
    assert.equal(s.width(), 480);
    await fire(ending === 'blur' ? s.windowRef : ending === 'lostpointercapture' ? s.divider : s.documentRef,
      ending, ending === 'blur' ? {} : pointer(720));
    await fire(s.documentRef, 'pointermove', pointer(400));
    assert.equal(s.width(), 480);
    assert.ok(!s.workspace.classList.contains('is-resizing'));
  }
});

test('narrow and reading layouts leave the preference alone; blocked or invalid storage is harmless', async () => {
  const s = setup({ stored: '600' });
  s.details.hidden = true;
  s.controller.refresh();
  await fire(s.divider, 'keydown', key('End'));
  assert.equal(s.width(), 600);
  s.details.hidden = false;
  s.controller.refresh();
  await s.resize(900);
  await fire(s.divider, 'pointerdown', pointer(500));
  await fire(s.documentRef, 'pointermove', pointer(300));
  assert.equal(s.saved(), '600');
  assert.ok(!s.workspace.classList.contains('is-resizing'));
  for (const stored of ['NaN', 'Infinity', '-10', '999999', 'url(evil)']) {
    assert.equal(setup({ stored }).width(), 480);
  }
  const denied = setup({ deny: true });
  await fire(denied.divider, 'keydown', key('ArrowLeft'));
  assert.equal(denied.width(), 504);
});
