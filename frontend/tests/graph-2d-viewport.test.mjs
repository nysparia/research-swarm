import assert from 'node:assert/strict';
import test from 'node:test';

const viewport = await import('../src/graph2dViewport.ts').catch(error => {
  if (error.code !== 'ERR_MODULE_NOT_FOUND') throw error;
  return {};
});

function exported(name) {
  assert.equal(typeof viewport[name], 'function', `2D viewport must export ${name}`);
  return viewport[name];
}

function close(actual, expected) {
  assert.ok(Math.abs(actual - expected) < 1e-9, `Expected ${expected}, received ${actual}`);
}

test('zoom keeps the world point beneath the pointer stationary', () => {
  const view = Object.freeze({ x: 10, y: 20, scale: 0.5 });
  const anchor = Object.freeze({ x: 210, y: 120 });
  const result = exported('zoomGraphView')(view, 2, anchor);
  assert.deepEqual(result, { x: -190, y: -80, scale: 1 });
  assert.deepEqual(view, { x: 10, y: 20, scale: 0.5 });
});

test('zoom limits still preserve the pointer anchor', () => {
  const zoom = exported('zoomGraphView');
  const view = { x: 10, y: 20, scale: 0.5 };
  const anchor = { x: 210, y: 120 };
  assert.deepEqual(zoom(view, 100, anchor), { x: -510, y: -240, scale: 1.8 });
  assert.deepEqual(zoom(view, 0.001, anchor), { x: 190, y: 110, scale: 0.05 });
});

test('fitting a large graph leaves twenty pixels at the limiting desktop edges', () => {
  const result = exported('fitGraphView')({ width: 1800, height: 1000 }, { width: 1200, height: 800 });
  close(result.scale, 29 / 45);
  close(result.x, 20);
  close(result.y, 77.77777777777777);
});

test('fitting a wide graph on a narrow screen keeps the entire graph visible', () => {
  const result = exported('fitGraphView')({ width: 1600, height: 1000 }, { width: 400, height: 800 });
  assert.deepEqual(result, { x: 20, y: 287.5, scale: 0.225 });
});

test('fitting centers small graphs without enlarging text', () => {
  const result = exported('fitGraphView')({ width: 140, height: 90 }, { width: 600, height: 400 });
  assert.deepEqual(result, { x: 230, y: 155, scale: 1 });
});

test('tiny viewports reduce padding so the graph remains visible', () => {
  const result = exported('fitGraphView')({ width: 100, height: 100 }, { width: 20, height: 20 });
  assert.deepEqual(result, { x: 5, y: 5, scale: 0.1 });
});

test('fit can show graphs larger than the interactive zoom minimum', () => {
  const result = exported('fitGraphView')({ width: 100000, height: 50000 }, { width: 400, height: 800 });
  assert.deepEqual(result, { x: 20, y: 310, scale: 0.0036 });
});

test('empty bounds and invalid viewport dimensions use finite defaults', () => {
  const fit = exported('fitGraphView');
  assert.deepEqual(fit({ width: 0, height: 0 }, { width: 600, height: 400 }), { x: 300, y: 200, scale: 1 });
  assert.deepEqual(fit({ width: NaN, height: 100 }, { width: 600, height: 400 }), { x: 300, y: 200, scale: 1 });
  assert.deepEqual(fit({ width: 100, height: 100 }, { width: Infinity, height: -1 }), { x: 0, y: 0, scale: 1 });
});

test('panning uses screen pixels and does not change the current scale', () => {
  const view = Object.freeze({ x: 10, y: 20, scale: 0.5 });
  const delta = Object.freeze({ x: -30, y: 70 });
  assert.deepEqual(exported('panGraphView')(view, delta), { x: -20, y: 90, scale: 0.5 });
  assert.deepEqual(view, { x: 10, y: 20, scale: 0.5 });
});

test('invalid zoom inputs do not poison a usable viewport', () => {
  const zoom = exported('zoomGraphView');
  const view = { x: 10, y: 20, scale: 0.5 };
  for (const factor of [NaN, Infinity, 0, -2]) {
    assert.deepEqual(zoom(view, factor, { x: 210, y: 120 }), view);
  }
  assert.deepEqual(zoom({ x: NaN, y: Infinity, scale: NaN }, 2, { x: NaN, y: Infinity }), { x: 0, y: 0, scale: 1.8 });
});

test('panning ignores nonfinite movement and prevents arithmetic overflow', () => {
  const pan = exported('panGraphView');
  assert.deepEqual(pan({ x: 10, y: 20, scale: 0.5 }, { x: Infinity, y: NaN }), { x: 10, y: 20, scale: 0.5 });
  const result = pan({ x: Number.MAX_VALUE, y: -Number.MAX_VALUE, scale: 0.5 }, { x: Number.MAX_VALUE, y: -Number.MAX_VALUE });
  assert.deepEqual(result, { x: Number.MAX_VALUE, y: -Number.MAX_VALUE, scale: 0.5 });
});
