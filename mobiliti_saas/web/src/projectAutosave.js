export const initialAutosaveState = (revision = 0) => ({
  status: "saved",
  revision,
  dirtyVersion: 0,
  savingVersion: 0,
  operationId: "",
  message: "",
  currentProject: null,
});

export function autosaveReducer(state, action) {
  if (action.type === "loaded") {
    return initialAutosaveState(action.revision);
  }

  if (action.type === "changed") {
    return {
      ...state,
      status: "pending",
      dirtyVersion: state.dirtyVersion + 1,
      operationId: "",
      message: "",
      currentProject: null,
    };
  }

  if (action.type === "saving") {
    if (action.dirtyVersion !== undefined && action.dirtyVersion !== state.dirtyVersion) {
      return state;
    }
    return {
      ...state,
      status: "saving",
      operationId: action.operationId,
      savingVersion: state.dirtyVersion,
      message: "",
    };
  }

  const isCurrentSave = action.operationId === state.operationId
    && state.savingVersion === state.dirtyVersion;

  if (action.type === "saved" && isCurrentSave) {
    return {
      ...state,
      status: "saved",
      revision: action.revision,
      operationId: "",
      message: "",
    };
  }

  if (action.type === "conflict" && isCurrentSave) {
    return {
      ...state,
      status: "conflict",
      operationId: "",
      currentProject: action.project,
      message: "",
    };
  }

  if (action.type === "failed" && isCurrentSave) {
    return {
      ...state,
      status: "pending",
      operationId: "",
      message: action.message,
    };
  }

  return state;
}

export function enqueueProjectSave({
  previous,
  snapshot,
  operationId,
  getExpectedRevision,
  saveProject,
  onConfirmed,
}) {
  return Promise.resolve(previous)
    .catch(() => undefined)
    .then(async () => {
      const saved = await saveProject(
        snapshot,
        getExpectedRevision(),
        operationId,
      );
      onConfirmed(saved);
      return saved;
    });
}

export function scheduleAutosave({
  snapshot,
  expectedRevision,
  operationId,
  dirtyVersion,
  saveProject,
  dispatch,
  isCurrent,
  onConfirmed,
  setTimer = window.setTimeout,
  clearTimer = window.clearTimeout,
}) {
  let cancelled = false;
  let debounceTimer = null;
  let retryTimer = null;

  const attempt = async () => {
    if (cancelled || !isCurrent()) return;

    dispatch({type: "saving", operationId, dirtyVersion});
    try {
      const saved = await saveProject(snapshot, expectedRevision, operationId);
      if (cancelled || !isCurrent()) return;

      onConfirmed(saved);
      dispatch({type: "saved", operationId, revision: saved.revision});
    } catch (error) {
      if (cancelled || !isCurrent()) return;

      if (error?.status === 409 && error?.project) {
        dispatch({type: "conflict", operationId, project: error.project});
        return;
      }

      dispatch({
        type: "failed",
        operationId,
        message: String(error?.message || error),
      });
      retryTimer = setTimer(attempt, 1500);
    }
  };

  debounceTimer = setTimer(attempt, 500);
  return () => {
    cancelled = true;
    if (debounceTimer !== null) clearTimer(debounceTimer);
    if (retryTimer !== null) clearTimer(retryTimer);
  };
}
