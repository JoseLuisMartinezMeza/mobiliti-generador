import {useEffect, useReducer, useRef} from "react";

import {
  autosaveReducer,
  initialAutosaveState,
  scheduleAutosave,
} from "./projectAutosave.js";

export function synchronizeAutosaveProject(current, {
  projectKey,
  revision,
  changeVersion,
}) {
  const projectChanged = current.projectKey !== projectKey;
  if (!projectChanged && changeVersion > 0) return current;
  return {
    projectKey,
    revision,
    operationId: "",
    dirtyVersion: 0,
  };
}

export function useProjectAutosave({
  project,
  projectKey,
  revision,
  changeVersion,
  saveProject,
  enabled = true,
}) {
  const [state, dispatch] = useReducer(autosaveReducer, revision, initialAutosaveState);
  const operationRef = useRef("");
  const revisionRef = useRef(revision);
  const dirtyVersionRef = useRef(0);
  const projectKeyRef = useRef(projectKey);
  const saveProjectRef = useRef(saveProject);

  saveProjectRef.current = saveProject;

  useEffect(() => {
    const current = {
      projectKey: projectKeyRef.current,
      revision: revisionRef.current,
      operationId: operationRef.current,
      dirtyVersion: dirtyVersionRef.current,
    };
    const synchronized = synchronizeAutosaveProject(current, {
      projectKey,
      revision,
      changeVersion,
    });
    if (synchronized === current) return;

    projectKeyRef.current = synchronized.projectKey;
    operationRef.current = synchronized.operationId;
    revisionRef.current = synchronized.revision;
    dirtyVersionRef.current = synchronized.dirtyVersion;
    dispatch({type: "loaded", revision});
  }, [changeVersion, projectKey, revision]);

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
  }, [changeVersion, enabled, projectKey]);

  return state;
}
