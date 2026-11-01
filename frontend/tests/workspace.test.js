import test from 'node:test';
import assert from 'node:assert/strict';
import { clampPanelWidth, keyboardPanelWidth, shouldSubmit } from '../src/api/workspace.js';

test('splitter preserves a readable chart and assistant when window narrows', () => {
  assert.equal(clampPanelWidth(600, 1024), 536);
  assert.equal(clampPanelWidth(800, 1440), 640);
  assert.equal(clampPanelWidth(-10, 1440), 340);
  assert.equal(clampPanelWidth(NaN, 1440), 420);
});

test('keyboard splitter respects bounds and does not consume unrelated keys', () => {
  assert.equal(keyboardPanelWidth('ArrowLeft', 420, 1440), 444);
  assert.equal(keyboardPanelWidth('ArrowRight', 340, 1440), 340);
  assert.equal(keyboardPanelWidth('Home', 420, 1440), 340);
  assert.equal(keyboardPanelWidth('End', 420, 1024), 536);
  assert.equal(keyboardPanelWidth('Tab', 420, 1440), null);
});

test('Enter submits while composition and Shift+Enter preserve editing', () => {
  assert.equal(shouldSubmit({ key: 'Enter' }), true);
  assert.equal(shouldSubmit({ key: 'Enter', shiftKey: true }), false);
  assert.equal(shouldSubmit({ key: 'Enter', isComposing: true }), false);
  assert.equal(shouldSubmit({ key: 'Enter', keyCode: 229 }), false);
  assert.equal(shouldSubmit({ key: 'a' }), false);
});
