import json
from pathlib import Path

from test_project_model_ui import run_js


AUTOSAVE_MODULE = Path(
    "mobiliti_saas/web/src/projectAutosave.js"
).resolve().as_uri()
HOOK_MODULE = Path(
    "mobiliti_saas/web/src/useProjectAutosave.js"
).resolve()


def test_autosave_never_claims_saved_before_server_confirmation():
    result = run_js(f"""
      const {{autosaveReducer, initialAutosaveState}} =
        await import({json.dumps(AUTOSAVE_MODULE)});
      let state = initialAutosaveState(3);
      state = autosaveReducer(state, {{type: "changed"}});
      const pending = state.status;
      state = autosaveReducer(state, {{type: "saving", operationId: "op-1"}});
      const saving = state.status;
      state = autosaveReducer(state, {{
        type: "failed", operationId: "op-1", message: "red"
      }});
      const failed = state.status;
      state = autosaveReducer(state, {{
        type: "saved", operationId: "op-1", revision: 4
      }});
      console.log(JSON.stringify({{
        pending, saving, failed, afterLateSuccess: state.status
      }}));
    """)
    assert result == {
        "pending": "pending",
        "saving": "saving",
        "failed": "pending",
        "afterLateSuccess": "pending",
    }


def test_autosave_only_accepts_matching_confirmation_for_current_edit():
    result = run_js(f"""
      const {{autosaveReducer, initialAutosaveState}} =
        await import({json.dumps(AUTOSAVE_MODULE)});
      let state = initialAutosaveState(3);
      state = autosaveReducer(state, {{type: "changed"}});
      state = autosaveReducer(state, {{type: "saving", operationId: "op-1"}});
      state = autosaveReducer(state, {{type: "changed"}});
      const afterEditDuringSave = state.status;
      state = autosaveReducer(state, {{
        type: "saved", operationId: "op-1", revision: 4
      }});
      const afterStaleConfirmation = state.status;
      state = autosaveReducer(state, {{type: "saving", operationId: "op-2"}});
      state = autosaveReducer(state, {{
        type: "saved", operationId: "wrong-id", revision: 4
      }});
      const afterWrongId = state.status;
      state = autosaveReducer(state, {{
        type: "saved", operationId: "op-2", revision: 4
      }});
      console.log(JSON.stringify({{
        afterEditDuringSave, afterStaleConfirmation, afterWrongId,
        final: state.status, revision: state.revision
      }}));
    """)
    assert result == {
        "afterEditDuringSave": "pending",
        "afterStaleConfirmation": "pending",
        "afterWrongId": "saving",
        "final": "saved",
        "revision": 4,
    }


def test_autosave_conflict_is_terminal_for_the_matching_operation():
    result = run_js(f"""
      const {{autosaveReducer, initialAutosaveState}} =
        await import({json.dumps(AUTOSAVE_MODULE)});
      let state = autosaveReducer(initialAutosaveState(7), {{type: "changed"}});
      state = autosaveReducer(state, {{type: "saving", operationId: "op-1"}});
      state = autosaveReducer(state, {{
        type: "conflict", operationId: "other", project: {{revision: 8}}
      }});
      const ignored = state.status;
      state = autosaveReducer(state, {{
        type: "conflict", operationId: "op-1", project: {{revision: 8}}
      }});
      console.log(JSON.stringify({{ignored, status: state.status, project: state.currentProject}}));
    """)
    assert result == {
        "ignored": "saving",
        "status": "conflict",
        "project": {"revision": 8},
    }


def test_autosave_debounces_retries_with_same_id_and_uses_new_id_for_later_edit():
    result = run_js(f"""
      const {{scheduleAutosave}} = await import({json.dumps(AUTOSAVE_MODULE)});
      const timers = [];
      const requests = [];
      const setTimer = (callback, delay) => {{
        timers.push({{callback, delay}});
        return timers.length - 1;
      }};
      let calls = 0;
      const saveProject = (...args) => {{
        requests.push(args);
        calls += 1;
        return calls === 1
          ? Promise.reject(new Error("temporary network failure"))
          : Promise.resolve({{revision: calls + 2}});
      }};
      const dispatches = [];
      const options = {{
        snapshot: {{name: "draft"}}, expectedRevision: 3, operationId: "op-1",
        dirtyVersion: 1, saveProject, dispatch: (action) => dispatches.push(action),
        isCurrent: () => true, onConfirmed: () => {{}}, setTimer, clearTimer: () => {{}},
      }};
      scheduleAutosave(options);
      const beforeDebounce = requests.length;
      const debounceDelay = timers[0].delay;
      await timers[0].callback();
      await new Promise((resolve) => setImmediate(resolve));
      const retryDelay = timers[1].delay;
      await timers[1].callback();
      await new Promise((resolve) => setImmediate(resolve));

      scheduleAutosave({{...options, operationId: "op-2", dirtyVersion: 2}});
      await timers[2].callback();
      await new Promise((resolve) => setImmediate(resolve));
      console.log(JSON.stringify({{
        beforeDebounce, debounceDelay, retryDelay,
        operationIds: requests.map((request) => request[2]),
        expectedRevisions: requests.map((request) => request[1]),
        savingIds: dispatches.filter((action) => action.type === "saving")
          .map((action) => action.operationId),
      }}));
    """)
    assert result == {
        "beforeDebounce": 0,
        "debounceDelay": 500,
        "retryDelay": 1500,
        "operationIds": ["op-1", "op-1", "op-2"],
        "expectedRevisions": [3, 3, 3],
        "savingIds": ["op-1", "op-1", "op-2"],
    }


def test_autosave_409_dispatches_conflict_without_retry():
    result = run_js(f"""
      const {{scheduleAutosave}} = await import({json.dumps(AUTOSAVE_MODULE)});
      const timers = [];
      const dispatches = [];
      const conflict = Object.assign(new Error("revision conflict"), {{
        status: 409, project: {{id: "project-1", revision: 4}},
      }});
      scheduleAutosave({{
        snapshot: {{name: "draft"}}, expectedRevision: 3, operationId: "op-1",
        dirtyVersion: 1, saveProject: () => Promise.reject(conflict),
        dispatch: (action) => dispatches.push(action), isCurrent: () => true,
        onConfirmed: () => {{}},
        setTimer: (callback, delay) => {{ timers.push({{callback, delay}}); return timers.length - 1; }},
        clearTimer: () => {{}},
      }});
      await timers[0].callback();
      await new Promise((resolve) => setImmediate(resolve));
      console.log(JSON.stringify({{
        timers: timers.map((timer) => timer.delay),
        actions: dispatches.map((action) => action.type),
        conflictProject: dispatches[1].project,
      }}));
    """)
    assert result == {
        "timers": [500],
        "actions": ["saving", "conflict"],
        "conflictProject": {"id": "project-1", "revision": 4},
    }


def test_autosave_hook_does_not_persist_or_save_loaded_projects():
    source = HOOK_MODULE.read_text(encoding="utf-8")
    assert "changeVersion <= 0" in source
    assert "structuredClone(project)" in source
    forbidden = ("localStorage", "sessionStorage", "indexedDB")
    assert not any(name in source for name in forbidden)
