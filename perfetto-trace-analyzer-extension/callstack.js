/**
 * callstack.js — Call stack traversal for Perfetto Trace Analyzer
 *
 * Injected into the Perfetto page via chrome.scripting.executeScript
 * with world: "MAIN", giving direct access to window.app.trace.engine.
 *
 * Exposes traceCallStack on window.__perfettoAnalyzer namespace so it
 * can be called from background.js via chrome.scripting.executeScript.
 *
 * Requirements: 7.1, 7.2, 7.3
 */

(function () {
  "use strict";

  /**
   * Trace the call stack for a given Slice by walking up the parent_id chain.
   *
   * Returns an array of { name, id, parentId } objects starting from the
   * target slice and ending at the root caller (or when maxDepth is reached).
   *
   * @param {number|string} sliceId - The ID of the slice to start from.
   * @param {number} [maxDepth=25] - Maximum number of parent levels to traverse.
   * @returns {Promise<Array<{name: string, id: string, parentId: string}>>}
   * @throws {Error} If the Perfetto trace engine is not available.
   */
  async function traceCallStack(sliceId, maxDepth) {
    if (maxDepth === undefined || maxDepth === null) {
      maxDepth = 25;
    }

    var engine = window.app && window.app.trace && window.app.trace.engine;
    if (!engine) {
      throw new Error("Perfetto trace engine is not available. Is a trace loaded?");
    }

    var stack = [];

    // Get the initial slice by id
    var initial = await engine.query(
      "SELECT CAST(id AS TEXT) as id, CAST(COALESCE(parent_id, -1) AS TEXT) as pid, name " +
      "FROM slice WHERE id = " + sliceId
    );

    var currentPid;
    for (var it = initial.iter({ id: "str", pid: "str", name: "str" }); it.valid(); it.next()) {
      stack.push({ name: it.name, id: it.id, parentId: it.pid });
      currentPid = it.pid;
    }

    // Walk up the parent_id chain until we hit root (-1) or maxDepth
    for (var i = 0; i < maxDepth && currentPid && currentPid !== "-1"; i++) {
      var q = await engine.query(
        "SELECT CAST(id AS TEXT) as id, CAST(COALESCE(parent_id, -1) AS TEXT) as pid, name " +
        "FROM slice WHERE id = " + currentPid
      );
      var found = false;
      for (var it2 = q.iter({ id: "str", pid: "str", name: "str" }); it2.valid(); it2.next()) {
        stack.push({ name: it2.name, id: it2.id, parentId: it2.pid });
        currentPid = it2.pid;
        found = true;
      }
      if (!found) break;
    }

    // Mark truncation when we hit maxDepth but haven't reached root
    if (currentPid && currentPid !== "-1" && stack.length > maxDepth) {
      stack.truncated = true;
    }

    return stack;
  }

  // Expose on the shared namespace
  window.__perfettoAnalyzer = window.__perfettoAnalyzer || {};
  window.__perfettoAnalyzer.traceCallStack = traceCallStack;
})();
