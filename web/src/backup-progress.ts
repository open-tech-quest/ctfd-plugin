import { createPoller } from "./polling.ts";
import { readBackupStatus, workerStatusUrl } from "./backup-transport.ts";
import type { BackupOperation } from "./backup-types";

export interface BackupProgressState {
  operation: BackupOperation | null;
  pendingRestoreId?: string;
  workerOrigin: string;
  error: string;
}
export function backupJobActive(operation: BackupOperation) {
  return (
    operation.job.status === "queued" || operation.job.status === "running"
  );
}

export function createBackupProgress(
  options: {
    load?: typeof readBackupStatus;
    active?: () => boolean;
    watchActivity?: (refresh: () => void) => () => void;
    clock?: {
      now: () => number;
      setTimer: (fn: () => void, delay: number) => unknown;
      clearTimer: (timer: unknown) => void;
      random: () => number;
    };
  } = {},
) {
  let state: BackupProgressState | null = null;
  let poller: ReturnType<typeof createPoller> | null = null;
  let unwatch = () => {};
  const listeners = new Set<() => void>();
  const completed = new Set<string>();
  function emit() {
    listeners.forEach((listener) => listener());
  }
  function stop() {
    poller?.stop();
    poller = null;
    unwatch();
    unwatch = () => {};
  }
  function begin(operation: BackupOperation, workerOrigin: string) {
    if (
      state?.operation &&
      backupJobActive(state.operation) &&
      state.operation.job.id !== operation.job.id
    )
      throw new Error(
        "Another backup operation is still running. Check its status before starting another.",
      );
    if (state?.operation?.job.id === operation.job.id) return;
    stop();
    state = { operation, workerOrigin, error: "" };
    try {
      workerStatusUrl(workerOrigin, operation);
    } catch (error) {
      state.error = (error as Error).message;
      emit();
      return;
    }
    emit();
    if (!backupJobActive(operation)) {
      completed.add(operation.job.id);
      return;
    }
    const id = operation.job.id;
    poller = createPoller({
      interval: 2000,
      active: options.active ?? (() => true),
      ...options.clock,
      load: (signal) =>
        (options.load ?? readBackupStatus)(workerOrigin, operation, signal),
      accept: (job) => {
        if (state?.operation?.job.id !== id) return;
        const operation = { ...state.operation, job };
        state = { ...state, operation, error: "" };
        if (!backupJobActive(operation)) {
          completed.add(id);
          stop();
        }
        emit();
      },
      reject: (error) => {
        if (state?.operation?.job.id !== id) return;
        state = {
          ...state,
          error:
            error instanceof Error
              ? error.message
              : "Backup status could not be read.",
        };
        emit();
      },
    });
    unwatch = options.watchActivity?.(() => poller?.activity()) ?? (() => {});
    poller.start();
  }
  return {
    getSnapshot: () => state,
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    begin,
    recover(operation: BackupOperation, workerOrigin: string) {
      if (
        (!state ||
          (state.operation &&
            state.operation.job.kind !== "restore" &&
            !backupJobActive(state.operation)) ||
          (state.pendingRestoreId &&
            state.pendingRestoreId ===
              (operation.job.operation_id ?? operation.job.id))) &&
        !completed.has(operation.job.id)
      )
        begin(operation, workerOrigin);
    },
    unconfirmedRestore(operationId: string, workerOrigin: string) {
      stop();
      state = {
        operation: null,
        pendingRestoreId: operationId,
        workerOrigin,
        error:
          "The restore request was not confirmed. It may already be running. Do not submit another restore; check its status or contact an operator.",
      };
      emit();
    },
    refresh: () => poller?.refresh() ?? Promise.resolve(),
    clear() {
      stop();
      state = null;
      emit();
    },
  };
}
