// Unit tests for the pure helpers of the viewer UI (node --test tests/js/).
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { samplePath } from '../../slidelite/web/js/animations.js';
import { relativeTime } from '../../slidelite/web/js/recent.js';
import { rectsPath, seededOrder, supported } from '../../slidelite/web/js/transitions.js';
import { ZOOM_STEPS } from '../../slidelite/web/js/viewer.js';

test('motion paths: lines, relative moves and curves', () => {
  assert.deepEqual(samplePath('M 0 0 L 0.25 0 E'), [[0, 0], [0.25, 0]]);
  assert.deepEqual(samplePath('M 0 0 l 0.1 0.2 l 0.1 0 E'), [[0, 0], [0.1, 0.2], [0.2, 0.2]]);
  const curve = samplePath('M 0 0 C 0 0.5 1 0.5 1 0 E');
  assert.equal(curve.length, 13);
  assert.deepEqual(curve.at(-1), [1, 0]);
  assert.ok(curve[6][1] > 0.3, 'curve bulges towards the control points');
  const closed = samplePath('M 0 0 L 1 0 L 1 1 Z');
  assert.deepEqual(closed.at(-1), [0, 0]);
});

test('motion paths: garbage input never throws', () => {
  for (const bad of ['', 'E', 'M x y', 'C 1 2', 'L 1 L', '\u0000'.repeat(10)]) {
    assert.ok(Array.isArray(samplePath(bad)));
  }
});

test('clip paths built from rectangles', () => {
  assert.equal(rectsPath([], 100, 50), 'path("M0 0Z")');
  assert.equal(rectsPath([[0, 0, 0.5, 1]], 100, 50), 'path("M0 0h50v50h-50Z")');
});

test('pattern transitions use a stable shuffled order', () => {
  const a = seededOrder(24, 5);
  assert.deepEqual(a, seededOrder(24, 5));
  assert.deepEqual([...a].sort((x, y) => x - y), [...Array(24).keys()]);
  assert.notDeepEqual(a, [...Array(24).keys()]);
});

test('transition catalogue covers the common PowerPoint effects', () => {
  for (const name of ['fade', 'push', 'wipe', 'split', 'cover', 'uncover', 'randomBar', 'circle', 'diamond', 'dissolve', 'zoom', 'cut']) {
    assert.ok(supported.includes(name), name);
  }
});

test('relative times for the recent list', () => {
  const now = 1_800_000_000;
  assert.equal(relativeTime(now - 10, now), 'Just now');
  assert.equal(relativeTime(now - 600, now), '10 min ago');
  assert.equal(relativeTime(now - 7200, now), '2 h ago');
  assert.equal(relativeTime(now - 100000, now), 'Yesterday');
  assert.match(relativeTime(now - 30 * 86400, now), /\d/);
});

test('zoom presets are increasing and include 100%', () => {
  assert.ok(ZOOM_STEPS.includes(1));
  assert.deepEqual([...ZOOM_STEPS].sort((a, b) => a - b), ZOOM_STEPS);
});
