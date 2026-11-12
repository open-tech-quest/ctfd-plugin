import type { BackupDayPage } from "./backup-types";

export interface BackupDayState {
  day: string | null;
  page: BackupDayPage | null;
  loading: boolean;
  error: string;
  notice: string;
}
type LoadDay = (
  day: string,
  before: string | null,
  signal: AbortSignal,
) => Promise<BackupDayPage>;

/** One selected day, abortable reads, and a small cache of unopened pages. */
export function createBackupDayLoader(
  load: LoadDay,
  changed: (value: BackupDayState) => void,
) {
  let current: { day: string; revision: string } | null = null;
  let state: BackupDayState = {
    day: null,
    page: null,
    loading: false,
    error: "",
    notice: "",
  };
  let controller: AbortController | null = null;
  let sequence = 0;
  let retryMore = false;
  const cache = new Map<string, BackupDayPage>();
  const key = () => `${current?.revision}:${current?.day}`;
  function emit(value: Partial<BackupDayState>) {
    state = { ...state, ...value };
    changed(state);
  }
  function stop() {
    ++sequence;
    controller?.abort();
    controller = null;
  }
  async function read(more: boolean) {
    if (!current || state.loading || (more && !state.page?.next_cursor)) return;
    const selected = current;
    const previous = state.page;
    const id = ++sequence;
    controller = new AbortController();
    const signal = controller.signal;
    retryMore = more;
    emit({ loading: true, error: "" });
    try {
      let result = await load(
        selected.day,
        more ? (previous?.next_cursor ?? null) : null,
        signal,
      );
      if (signal.aborted || id !== sequence) return;
      if (result.day !== selected.day)
        throw new Error(
          "The backup service returned a different day. Refresh backup status.",
        );
      let notice = "";
      if (more && previous && result.total !== previous.total) {
        // Retention or a new capture changed the page set. Never stitch two generations.
        result = await load(selected.day, null, signal);
        if (signal.aborted || id !== sequence) return;
        if (result.day !== selected.day)
          throw new Error(
            "The backup service returned a different day. Refresh backup status.",
          );
        notice =
          "Backup history changed. Showing the latest backups for this day.";
      } else if (more && previous) {
        const unique = new Map(
          previous.snapshots.map((snapshot) => [snapshot.id, snapshot]),
        );
        result.snapshots.forEach((snapshot) =>
          unique.set(snapshot.id, snapshot),
        );
        result = { ...result, snapshots: [...unique.values()] };
      }
      cache.delete(key());
      cache.set(key(), result);
      while (cache.size > 8) cache.delete(cache.keys().next().value!);
      emit({ page: result, loading: false, notice });
    } catch (error) {
      if (signal.aborted || id !== sequence) return;
      emit({
        loading: false,
        error:
          error instanceof Error
            ? error.message
            : "Unable to load backups. Try again.",
      });
    }
  }
  return {
    open(day: string | null, revision: string) {
      if (day === current?.day && revision === current?.revision) return;
      stop();
      current = day ? { day, revision } : null;
      emit({
        day,
        page: day ? (cache.get(key()) ?? null) : null,
        loading: false,
        error: "",
        notice: "",
      });
      if (day && !state.page) void read(false);
    },
    more() {
      return read(true);
    },
    retry() {
      return read(retryMore);
    },
    refresh() {
      if (!current) return;
      stop();
      cache.delete(key());
      emit({ page: null, loading: false, error: "", notice: "" });
      void read(false);
    },
    close() {
      stop();
      current = null;
    },
  };
}
