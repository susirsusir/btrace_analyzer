/**
 * diagnostics.js — SQL diagnostic queries for Perfetto Trace Analyzer
 *
 * Injected into the Perfetto page via chrome.scripting.executeScript
 * with world: "MAIN", giving direct access to window.app.trace.engine.
 *
 * Exposes functions on window.__perfettoAnalyzer namespace so they can
 * be called from background.js via chrome.scripting.executeScript.
 *
 * Requirements: 3.1, 3.2, 3.3, 4.1, 4.2, 4.3, 5.1, 5.2, 5.3, 6.1, 6.2, 6.3
 */

// Provide a window shim for Node.js/Jest testing environment
var _window = typeof window !== "undefined" ? window : {};

(function () {
  "use strict";

  // -----------------------------------------------------------------------
  // SQL query definitions
  // -----------------------------------------------------------------------

  // Sub-query that resolves the main thread utid reliably across Android traces.
  // Strategy: prefer is_main_thread=1, fall back to tid=pid (Android main thread
  // tid always equals the process pid), then fall back to thread name 'main'.
  const MAIN_THREAD_CONDITION = `(
    t.is_main_thread = 1
    OR t.tid = (SELECT pid FROM process ORDER BY pid LIMIT 1)
    OR t.name = 'main'
  )`;

  const DIAGNOSTIC_QUERIES = {
    long_slices: `
      SELECT s.id, s.name, s.dur / 1000000.0 AS dur_ms,
             CAST(s.ts AS TEXT) AS ts_str, CAST(s.dur AS TEXT) AS dur_str
      FROM slice s
      JOIN thread_track tt ON s.track_id = tt.id
      JOIN thread t ON tt.utid = t.utid
      WHERE ${MAIN_THREAD_CONDITION}
      ORDER BY s.dur DESC
      LIMIT 30
    `,

    frame_jank: `
      SELECT s.id, s.name, s.dur / 1000000.0 AS dur_ms,
             CAST(s.ts AS TEXT) AS ts_str, CAST(s.dur AS TEXT) AS dur_str
      FROM slice s
      JOIN thread_track tt ON s.track_id = tt.id
      JOIN thread t ON tt.utid = t.utid
      WHERE ${MAIN_THREAD_CONDITION}
        AND (s.name LIKE '%doFrame%' OR s.name LIKE '%Choreographer%' OR s.name LIKE '%traversal%')
        AND s.dur > 16600000
      ORDER BY s.dur DESC
      LIMIT 20
    `,

    cpu_heavy: `
      SELECT s.name, COUNT(*) AS count,
             SUM(s.dur) / 1000000.0 AS total_ms,
             AVG(s.dur) / 1000000.0 AS avg_ms
      FROM slice s
      JOIN thread_track tt ON s.track_id = tt.id
      JOIN thread t ON tt.utid = t.utid
      WHERE ${MAIN_THREAD_CONDITION}
      GROUP BY s.name
      HAVING count > 5
      ORDER BY total_ms DESC
      LIMIT 30
    `,

    main_thread_io: `
      SELECT s.id, s.name, s.dur / 1000000.0 AS dur_ms,
             CAST(s.ts AS TEXT) AS ts_str, CAST(s.dur AS TEXT) AS dur_str
      FROM slice s
      JOIN thread_track tt ON s.track_id = tt.id
      JOIN thread t ON tt.utid = t.utid
      WHERE ${MAIN_THREAD_CONDITION}
        AND (s.name LIKE '%Binder%' OR s.name LIKE '%sqlite%'
             OR s.name LIKE '%SharedPreferences%' OR s.name LIKE '%File%'
             OR s.name LIKE '%network%' OR s.name LIKE '%http%')
      ORDER BY s.dur DESC
      LIMIT 20
    `,
  };

  // -----------------------------------------------------------------------
  // I/O keyword matching helper
  // -----------------------------------------------------------------------

  const IO_KEYWORDS = [
    "Binder",
    "sqlite",
    "SharedPreferences",
    "File",
    "network",
    "http",
  ];

  /**
   * Check whether a Slice name is I/O-related.
   * @param {string} name - Slice name to test.
   * @returns {boolean} true if the name contains any I/O keyword.
   */
  function isIORelated(name) {
    if (typeof name !== "string") return false;
    return IO_KEYWORDS.some((kw) => name.includes(kw));
  }

  // -----------------------------------------------------------------------
  // Column schemas for Perfetto's result.iter() API
  // -----------------------------------------------------------------------

  const COLUMN_SCHEMAS = {
    long_slices: { id: "num", name: "str", dur_ms: "num", ts_str: "str", dur_str: "str" },
    frame_jank: { id: "num", name: "str", dur_ms: "num", ts_str: "str", dur_str: "str" },
    cpu_heavy: { name: "str", count: "num", total_ms: "num", avg_ms: "num" },
    main_thread_io: { id: "num", name: "str", dur_ms: "num", ts_str: "str", dur_str: "str" },
  };

  // -----------------------------------------------------------------------
  // Query execution
  // -----------------------------------------------------------------------

  /**
   * Run a diagnostic SQL query against the loaded Perfetto trace.
   *
   * @param {string} type - One of "long_slices", "frame_jank", "cpu_heavy", "main_thread_io".
   * @returns {Promise<Array<Object>>} Array of result row objects.
   * @throws {Error} If the query type is unknown or the engine is unavailable.
   */
  async function runDiagnosticQuery(type) {
    const sql = DIAGNOSTIC_QUERIES[type];
    if (!sql) {
      throw new Error(`Unknown diagnostic type: ${type}`);
    }

    const engine = _window.app && _window.app.trace && _window.app.trace.engine;
    if (!engine) {
      throw new Error("Perfetto trace engine is not available. Is a trace loaded?");
    }

    const result = await engine.query(sql);
    const schema = COLUMN_SCHEMAS[type];
    const rows = [];

    for (const it = result.iter(schema); it.valid(); it.next()) {
      const row = {};
      for (const col of Object.keys(schema)) {
        row[col] = it[col];
      }
      rows.push(row);
    }

    return rows;
  }

  // -----------------------------------------------------------------------
  // Expose on window.__perfettoAnalyzer
  // -----------------------------------------------------------------------

  _window.__perfettoAnalyzer = _window.__perfettoAnalyzer || {};
  Object.assign(_window.__perfettoAnalyzer, {
    DIAGNOSTIC_QUERIES,
    IO_KEYWORDS,
    isIORelated,
    runDiagnosticQuery,
    COLUMN_SCHEMAS,
  });

  // Export for testing (Node.js / Jest). In the browser IIFE context,
  // `module` is undefined and this block is safely skipped.
  if (typeof module !== "undefined" && module.exports) {
    module.exports = {
      DIAGNOSTIC_QUERIES,
      IO_KEYWORDS,
      isIORelated,
      COLUMN_SCHEMAS,
    };
  }
})();
