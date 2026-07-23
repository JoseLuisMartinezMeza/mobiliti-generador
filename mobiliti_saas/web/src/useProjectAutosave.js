import {useEffect, useReducer, useRef} from "react";

import {
  autosaveReducer,
  initialAutosaveState,
  scheduleAutosave,
} from "./projectAutosave.js";

export function useProjectAutosave({
  project,
  revision,
  changeVersion,
  saveProject,
  enabled = true,
}) {
  const [state, dispatch] = useReducer(autosaveReducer, revision, initialAutosaveState);
  const operationRef = useRef("");
  const revisionRef = useRef(revision);
  const dirtyVersionRef = useRef(0);
  const saveProjectRef = useRef(saveProject);

  saveProjectRef.current = saveProject;

  useEffect(() => {
    if (changeVersion > 0) return;

    operationRef.current = "";
    revisionRef.current = revision;
    dirtyVersionRef.current = 0;
    dispatch({type: "loaded", revision});
  }, [changeVersion, revision]);

  useEffect(() => {
    if (!enabled || !project || changeVersion <= 0) return undefined;

    dirtyVersionRef.current += 1;
    const dirtyVersion = dirtyVersionRef.current;
    const snapshot = structuredClone(project);
    const operationId = crypto.randomUUID();
    const expectedRevision = revisionRef.current;
    operationRef.current = operationId;
    dispatch({type: "changed"});

    return scheduleAutosave({
      snapshot,
      expectedRevision,
      operationId,
      dirtyVersion,
      saveProject: (...args) => saveProjectRef.current(...args),
      dispatch,
      isCurrent: () => operationRef.current === operationId,
      onConfirmed: (saved) => {
        if (operationRef.current === operationId) {
          revisionRef.current = saved.revision;
          operationRef.current = "";
        }
      },
    });
  }, [changeVersion, enabled]);

  return state;
}
