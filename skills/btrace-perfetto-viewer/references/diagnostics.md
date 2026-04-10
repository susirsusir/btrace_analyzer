# Perfetto Diagnostic Queries & Call Stack Tracing

## SQL Queries

### Long-running slices on main thread

```sql
SELECT s.name, s.dur / 1000000.0 AS dur_ms, s.ts
FROM slice s
JOIN thread_track tt ON s.track_id = tt.id
JOIN thread t ON tt.utid = t.utid
WHERE t.name = 'main' OR t.tid = (SELECT pid FROM process LIMIT 1)
ORDER BY s.dur DESC
LIMIT 30
```

### Frame jank (frames exceeding 16.6ms)

```sql
SELECT s.name, s.dur / 1000000.0 AS dur_ms, s.ts
FROM slice s
JOIN thread_track tt ON s.track_id = tt.id
JOIN thread t ON tt.utid = t.utid
WHERE (t.name = 'main' OR t.is_main_thread = 1)
  AND s.name LIKE '%doFrame%' OR s.name LIKE '%Choreographer%' OR s.name LIKE '%traversal%'
  AND s.dur > 16600000
ORDER BY s.dur DESC
LIMIT 20
```

### CPU-heavy methods

```sql
SELECT s.name, COUNT(*) AS count, SUM(s.dur) / 1000000.0 AS total_ms, AVG(s.dur) / 1000000.0 AS avg_ms
FROM slice s
GROUP BY s.name
HAVING count > 5
ORDER BY total_ms DESC
LIMIT 30
```

### I/O on main thread

```sql
SELECT s.name, s.dur / 1000000.0 AS dur_ms
FROM slice s
JOIN thread_track tt ON s.track_id = tt.id
JOIN thread t ON tt.utid = t.utid
WHERE (t.name = 'main' OR t.is_main_thread = 1)
  AND (s.name LIKE '%Binder%' OR s.name LIKE '%sqlite%' OR s.name LIKE '%SharedPreferences%'
       OR s.name LIKE '%File%' OR s.name LIKE '%network%' OR s.name LIKE '%http%')
ORDER BY s.dur DESC
LIMIT 20
```

## Call Stack Tracing

For each identified problem slice, walk up the `parent_id` chain. Use `evaluate_script` in Perfetto's JS context:

```javascript
async () => {
  const engine = window.app.trace.engine;
  const traceStack = async (whereClause) => {
    const q1 = await engine.query(
      "SELECT CAST(id AS TEXT) as id, CAST(COALESCE(parent_id, -1) AS TEXT) as pid, name " +
      "FROM slice WHERE " + whereClause + " ORDER BY dur DESC LIMIT 1"
    );
    let sliceId, parentId, name;
    for (const it = q1.iter({id:'str', pid:'str', name:'str'}); it.valid(); it.next()) {
      sliceId = it.id; parentId = it.pid; name = it.name;
    }
    if (!sliceId) return [];
    const stack = [{name}];
    let currentPid = parentId;
    for (let i = 0; i < 25 && currentPid && currentPid !== '-1'; i++) {
      const q = await engine.query(
        "SELECT CAST(id AS TEXT) as id, CAST(COALESCE(parent_id,-1) AS TEXT) as pid, name " +
        "FROM slice WHERE id = " + currentPid
      );
      let found = false;
      for (const it = q.iter({id:'str', pid:'str', name:'str'}); it.valid(); it.next()) {
        stack.push({name: it.name}); currentPid = it.pid; found = true;
      }
      if (!found) break;
    }
    return stack;
  };
  return await traceStack("name LIKE '%keyword%'");
}
```

IMPORTANT: Use `COALESCE(parent_id, -1)` — root slices have NULL parent_id.

## Timeline Navigation & Screenshots

For each issue, navigate to the exact time range and take a screenshot.

**Get ts/dur for a slice:**

```sql
SELECT CAST(s.ts AS TEXT) as ts_str, CAST(s.dur AS TEXT) as dur_str
FROM slice s WHERE s.name LIKE '%keyword%' ORDER BY s.dur DESC LIMIT 1
```

**Navigate timeline** via `evaluate_script`:

```javascript
() => {
  const vw = window.app.trace.timeline._visibleWindow;
  const HPT = vw.start.constructor;
  const HPTS = vw.constructor;
  const ts = <TS_VALUE>n;
  const dur = <DUR_VALUE>n;
  const padding = dur / 3n;
  const startTime = new HPT({ integral: ts - padding, fractional: 0 });
  window.app.trace.timeline.setVisibleWindow(new HPTS(startTime, Number(dur + padding * 2n)));
  return 'navigated';
}
```

- Use `CAST(ts AS TEXT)` in SQL — ts/dur are int64 and need BigInt in JS
- Wait at least 1 second after navigation before taking the screenshot
- Save to `trace-analysis/<traceID>/screenshot_<N>_<name>.png`
