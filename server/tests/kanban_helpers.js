/**
 * kanban_helpers.js — CJS re-export of shared pure interaction logic.
 *
 * This module wraps desktop/kanban-interaction.js (ESM) for Node.js CommonJS
 * consumption. It re-exports every constant and function so that tests import
 * the exact same code that plugin.js ships. No logic is duplicated.
 *
 * Used by: tests/test_kanban_helpers.mjs (Node behavioral tests)
 * Source of truth: desktop/kanban-interaction.js
 */

const path = require('path')
const sharedPath = path.resolve(__dirname, '..', 'desktop', 'kanban-interaction.js')

// Dynamic import of ESM from CJS — returns a Promise
async function loadShared() {
  return import(sharedPath)
}

module.exports = { loadShared }
