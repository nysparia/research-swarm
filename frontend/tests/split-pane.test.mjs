import test from 'node:test';
import assert from 'node:assert/strict';
import { DEFAULT_PANE_PERCENT, paneLimits, panePercentAt, panePercentForKey, clampPanePercent } from '../src/workbench/splitPaneState.ts';

test('desktop divider keeps both panes readable at supported widths', () => {
  for (const width of [760, 900, 1170, 1600]) {
    const [min, max] = paneLimits(width);
    assert.ok(min <= max);
    assert.ok(min / 100 * width >= 279.999);
    assert.ok(width * (1 - max / 100) >= 319.999);
  }
});
test('pointer resizing is relative to the split container, not the browser edge', () => {
  assert.equal(panePercentAt(680, 260, 1000), 42);
  assert.equal(panePercentAt(-500, 260, 1000), 28);
  assert.equal(panePercentAt(9999, 260, 1000), 65);
});
test('arrow keys, endpoints and reset share pointer constraints', () => {
  assert.equal(panePercentForKey(42, 'ArrowRight', 1000), 44);
  assert.equal(panePercentForKey(42, 'ArrowLeft', 1000, true), 37);
  assert.equal(panePercentForKey(42, 'Home', 1000), 28);
  assert.equal(panePercentForKey(42, 'End', 1000), 65);
  assert.equal(panePercentForKey(61, 'Enter', 1000), DEFAULT_PANE_PERCENT);
  assert.equal(panePercentForKey(61, 'Tab', 1000), null);
});
test('zero-sized, hidden and malformed measurements remain finite', () => {
  for (const width of [0, -10, NaN, Infinity, 390]) {
    assert.equal(clampPanePercent(NaN, width), DEFAULT_PANE_PERCENT);
    assert.equal(panePercentAt(10, 0, width), DEFAULT_PANE_PERCENT);
  }
});
